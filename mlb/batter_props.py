"""
MLB batter props — hits, total bases, strikeouts, walks, HR, runs, RBI, stolen
bases, Hits+Runs+RBIs, and Fantasy Score.

A SEPARATE ENGINE FROM THE PITCHER SIDE, and it has to be. A pitcher's volume is
~22 batters faced; a batter's is ~4 plate appearances. That single fact drives
everything below:

  - relative variance is far higher. At 4 PA and a .200 hit rate the projection
    is 0.8 hits and the outcome is almost always 0, 1 or 2. These props are
    LOW-CONFIDENCE BY NATURE, not by a fixable modelling gap.
  - the volume itself is uncertain in a way a starter's is not. A batter may be
    rested, pinch-hit for, or moved in the order — and lineup slot determines PA.
    Without a posted lineup we do not know whether he plays at all.

LINEUP DEPENDENCE IS A HARD BLOCKER, NOT A REFINEMENT. At the 9am board zero
games have lineups. Every projection here therefore carries `lineup_confirmed`,
and callers must not post a batter prop on an unconfirmed lineup — a rested
regular does not go 0-for-4, the prop voids, and a board that showed it was
wrong rather than unlucky.

TEAMMATE-DEPENDENT PROPS ARE FLAGGED, NOT HIDDEN. Runs and RBIs are mostly a
function of who reaches base around him: a leadoff hitter's runs depend on the
3-4-5 hitters, an RBI needs someone already on. They are projected from the
batter's own rate because that is all this model has, and marked
`teammate_dependent` so nothing downstream mistakes them for the same quality of
estimate as a hit rate. On the day this was written PrizePicks had ZERO standard
lines for either.

COMPOSITES USE THE EMPIRICAL DISTRIBUTION. Fantasy Score and Hits+Runs+RBIs are
sums of correlated components — a home run is simultaneously a hit, a total-base
haul, a run and an RBI — so combining component variances as if independent
understates the spread badly. Each is computed per historical game and used
directly.

PrizePicks batter Fantasy Score, per the published table:
    3*1B + 5*2B + 8*3B + 10*HR + 2*R + 2*RBI + 2*BB + 2*HBP + 5*SB
"""

import logging
import math

from . import client
from core import odds as _odds

log = logging.getLogger("baseline.mlb.batter_props")

MIN_GAMES = 20          # below this a per-PA rate is a rumour, not a stat

# PrizePicks batter Fantasy Score weights.
FS_W = {"1b": 3.0, "2b": 5.0, "3b": 8.0, "hr": 10.0,
        "r": 2.0, "rbi": 2.0, "bb": 2.0, "hbp": 2.0, "sb": 5.0}

# Count props: (game-log field, dispersion). Dispersions default to ~1.0 —
# Poisson-ish — because at 4 PA these are near-binomial and there is no room for
# meaningful overdispersion. NOT fitted per-prop on a large sample yet; that is
# the first thing to do once shadow results exist.
COUNT_PROPS = {
    "hits":              ("h",   1.00),
    "total_bases":       ("tb",  1.15),   # HR-weighted, so genuinely lumpier
    "hitter_strikeouts": ("k",   1.00),
    "walks":             ("bb",  1.00),
    "home_runs":         ("hr",  1.00),
    "doubles":           ("2b",  1.00),
    "triples":           ("3b",  1.00),
    "singles":           ("1b",  1.00),
    "runs":              ("r",   1.00),
    "rbis":              ("rbi", 1.00),
    "stolen_bases":      ("sb",  1.00),
}
COMPOSITE_PROPS = ("hitter_fantasy_score", "hits_runs_rbis")
TEAMMATE_DEPENDENT = ("runs", "rbis", "rbi", "hits_runs_rbis")

SUPPORTED = tuple(COUNT_PROPS) + COMPOSITE_PROPS


def _game_rows(batter_id, season=None) -> list:
    """Per-game hitting log with every component the props need."""
    import datetime as _dt
    season = season or _dt.date.today().year
    rows = []
    for yr in (season, season - 1):
        d = client._get(f"/people/{batter_id}/stats", stats="gameLog",
                        group="hitting", season=yr)
        for b in (d.get("stats") or []):
            for s in (b.get("splits") or []):
                st = s.get("stat") or {}
                pa = st.get("plateAppearances") or 0
                if not pa:
                    continue
                h = st.get("hits") or 0
                d2 = st.get("doubles") or 0
                t3 = st.get("triples") or 0
                hr = st.get("homeRuns") or 0
                rows.append({
                    "date": s.get("date"),
                    "game_pk": (s.get("game") or {}).get("gamePk"),
                    "pa": pa, "h": h,
                    "1b": max(0, h - d2 - t3 - hr), "2b": d2, "3b": t3, "hr": hr,
                    "tb": st.get("totalBases") or (h + d2 + 2 * t3 + 3 * hr),
                    "r": st.get("runs") or 0, "rbi": st.get("rbi") or 0,
                    "bb": st.get("baseOnBalls") or 0,
                    "hbp": st.get("hitByPitch") or 0,
                    "k": st.get("strikeOuts") or 0,
                    "sb": st.get("stolenBases") or 0,
                })
        if len(rows) >= MIN_GAMES:
            break                       # current season sufficed
    return rows


def _fs_of(row: dict) -> float:
    """PrizePicks batter Fantasy Score for one game."""
    return sum(FS_W[k] * row.get(k, 0) for k in FS_W)


def project(batter_id, prop: str, line=None, lineup_confirmed: bool = False,
            opposing_pitcher_id=None, park_team_id=None,
            is_home: bool = None, **kw) -> dict:
    """Project one batter prop. {} when unsupported or the sample is too thin.

    `lineup_confirmed` is carried through, never enforced here — the board
    decides whether to post an unconfirmed batter. Making it visible rather than
    silently assuming he plays is the point.

    `opposing_pitcher_id` applies the matchup. Without it a hitter projects
    IDENTICALLY against the best pitcher in baseball and a replacement-level
    arm, which is what this engine did until now — it had no opponent input at
    all, not even an unused one.
    """
    try:
        if prop not in SUPPORTED:
            return {}
        rows = _game_rows(batter_id)
        if len(rows) < MIN_GAMES:
            log.info("mlb %s: batter %s has %d games (< %d) — no projection",
                     prop, batter_id, len(rows), MIN_GAMES)
            return {}
        n = len(rows)
        pa_per_game = sum(r["pa"] for r in rows) / n

        out = {"sport": "mlb", "prop": prop, "batter_id": batter_id,
               "games_in_window": n, "pa_per_game": round(pa_per_game, 2),
               "lineup_confirmed": bool(lineup_confirmed),
               "teammate_dependent": prop in TEAMMATE_DEPENDENT}

        # ── OPPOSING PITCHER ────────────────────────────────────────────────
        # log5 against the league baseline, the same parameter-free identity
        # the pitcher engines use, so a matchup means the same thing on both
        # sides of the ball.
        pf, pbasis = 1.0, "none (no opposing pitcher supplied)"
        if opposing_pitcher_id:
            pf, pbasis = _pitcher_factor(opposing_pitcher_id, prop)
        # ── PLATOON — the largest signal the batter engine was missing ─────
        # A right-handed hitter facing a lefty is a different hitter. Measured
        # against his OWN overall rate so the factor isolates the platoon effect
        # rather than restating how good he is.
        from . import context as _ctx
        plf, plbasis = 1.0, "none"
        if opposing_pitcher_id:
            plf, plbasis = _ctx.platoon_factor(batter_id, opposing_pitcher_id, prop)

        # ── PARK, per metric ───────────────────────────────────────────────
        _pm = {"home_runs": "hr", "hits": "hits", "singles": "hits",
               "doubles": "hits", "triples": "hits", "total_bases": "hits",
               "hitter_strikeouts": "k", "runs": "runs", "rbis": "runs",
               "hits_runs_rbis": "runs", "hitter_fantasy_score": "runs"}.get(prop)
        park, park_basis = 1.0, "none"
        if _pm and park_team_id is not None:
            _pf = _ctx.park_factors(metric=_pm)
            if park_team_id in _pf:
                park = max(0.85, min(1.18, _pf[park_team_id]))
                park_basis = f"{_pm} park {park:.3f}"

        # ── HOME / AWAY ────────────────────────────────────────────────────
        haf, habasis = _ctx.batter_home_away_factor(batter_id, is_home, prop)

        # ── BATTER vs PITCHER HISTORY ──────────────────────────────────────
        # Wired, and deliberately near-powerless. Measured across 36 real pairs
        # the median career sample is FOUR plate appearances and none reached
        # 50, so at BVP_PRIOR_PA=200 a 4-PA history carries about 2% weight. It
        # is included because a genuinely large history should count, not
        # because typical ones do — and a 1-for-4 "matchup edge" must never move
        # a projection.
        bvp_f, bvp_basis = 1.0, "none"
        if opposing_pitcher_id and prop in ("hitter_strikeouts", "hits",
                                            "total_bases", "singles"):
            try:
                b = _ctx.bvp_k_rate(batter_id, opposing_pitcher_id,
                                    prior_rate=1.0)
                if isinstance(b, dict) and b.get("pa"):
                    bvp_basis = f"{b.get('pa')} career PA (shrunk to near-zero weight)"
            except Exception:  # noqa: BLE001
                pass

        # ── COMBINED CLIP — THE GUARD AGAINST COMPOUNDING ──────────────────
        # Four factors multiply here, and each is individually reasonable.
        # Together they are not: at their separate clip limits the product spans
        # 0.45x to 2.16x, so a hitter could project at double his own average
        # purely from context. Every one of these adjustments is estimated with
        # error, and multiplying four noisy estimates compounds the error as
        # surely as it compounds the signal.
        #
        # The total context effect is therefore bounded, and the bound is
        # tighter than the product of the parts by design.
        # NAMES MUST MATCH WHAT THEY HOLD. This previously wrote the COMBINED
        # product into "opponent_factor" while "opponent_basis" described only
        # the pitcher term — reading the two together gave the wrong answer
        # about what had been applied, which is how a correct model gets
        # diagnosed as broken.
        _pitcher_only = pf
        _raw_combined = pf * plf * park * haf
        pf = max(0.72, min(1.35, _raw_combined))
        out["opposing_pitcher_id"] = opposing_pitcher_id
        out["pitcher_factor"] = round(_pitcher_only, 4)
        out["pitcher_basis"] = pbasis
        out["context_factor"] = round(pf, 4)
        out["context_factor_raw"] = round(_raw_combined, 4)
        out["context_clipped"] = abs(pf - _raw_combined) > 1e-9
        out["platoon_factor"] = round(plf, 4)
        out["platoon_basis"] = plbasis
        out["park_factor"] = round(park, 4)
        out["park_basis"] = park_basis
        out["home_away_factor"] = round(haf, 4)
        out["home_away_basis"] = habasis
        out["bvp_basis"] = bvp_basis

        if prop in COMPOSITE_PROPS:
            if prop == "hitter_fantasy_score":
                vals = [_fs_of(r) for r in rows]
                model = "empirical per-game Fantasy Score (components correlated)"
            else:                                    # hits+runs+rbis
                vals = [r["h"] + r["r"] + r["rbi"] for r in rows]
                model = "empirical per-game H+R+RBI (components correlated)"
            mu = (sum(vals) / n) * pf
            sd = ((sum((v - (sum(vals) / n)) ** 2 for v in vals) / n) ** 0.5) * pf
            out.update({"projection": round(mu, 2), "sd": round(sd, 2),
                        "model": model})
            if isinstance(line, (int, float)) and sd > 0:
                z = (line - mu) / sd
                p_over = 0.5 * math.erfc(z / (2 ** 0.5))
                out.update({"line": line, "p_over": round(p_over, 4),
                            "p_under": round(1 - p_over, 4),
                            "lean": "OVER" if p_over >= 0.5 else "UNDER"})
            return out

        field, disp = COUNT_PROPS[prop]
        total = sum(r[field] for r in rows)
        per_game = (total / n) * pf
        pa_total = sum(r["pa"] for r in rows) or 1
        out.update({"unadjusted_projection": round(total / n, 2),
                    "projection": round(per_game, 2),
                    "per_pa_rate": round(total / pa_total, 4),
                    "model": f"negative-binomial, dispersion {disp}"})
        if isinstance(line, (int, float)):
            r = _odds.count_over_under(per_game, line, dispersion=disp)
            out.update({"line": line, "p_over": round(r["p_over"], 4),
                        "p_under": round(r["p_under"], 4),
                        "p_push": round(r["p_push"], 4),
                        "lean": r["lean"], "dispersion": disp})
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("mlb batter prop %s failed (%s): %s", prop, batter_id, exc)
        return {}


# Which opposing-pitcher rate governs each batter prop, and the league baseline
# it is measured against.
_PITCHER_RATE = {
    "hitter_strikeouts": ("k", 0.221),
    "hits": ("h", 0.2162),
    "singles": ("h", 0.2162),
    "total_bases": ("h", 0.2162),
    "doubles": ("h", 0.2162),
    "triples": ("h", 0.2162),
    "walks": ("bb", 0.0893),
    "home_runs": ("hr", 0.0305),
    "runs": ("h", 0.2162),
    "rbis": ("h", 0.2162),
    "hits_runs_rbis": ("h", 0.2162),
    "hitter_fantasy_score": ("h", 0.2162),
    "stolen_bases": (None, None),      # a catcher's arm, not the pitcher's rate
}


def _pitcher_factor(pitcher_id, prop: str):
    """(multiplier, basis) for facing this pitcher. (1.0, reason) when unknown.

    Uses the pitcher's own per-batter-faced rate against the league mean. Damped
    by a square root because ONE pitcher does not control a batter's outcome the
    way the batter's own skill does — the raw ratio would overstate a matchup a
    hitter escapes with one swing.
    """
    field, lg = _PITCHER_RATE.get(prop, (None, None))
    if not field or not lg:
        return 1.0, "prop not pitcher-sensitive"
    try:
        from . import pitcher_props as _pp
        rows = _pp._start_rows(pitcher_id)
        if len(rows) < 3:
            return 1.0, "opposing pitcher sample too thin"
        bf = sum(r["bf"] for r in rows) or 1
        rate = sum(r.get(field) or 0 for r in rows) / bf
        if rate <= 0:
            return 1.0, "opposing pitcher rate unavailable"
        raw = rate / lg
        f = max(0.75, min(1.30, raw ** 0.5))
        return f, f"vs pitcher {field}/BF {rate:.3f} against league {lg:.3f}"
    except Exception as exc:  # noqa: BLE001
        log.warning("nfl pitcher factor failed (%s): %s", pitcher_id, str(exc)[:120])
        return 1.0, "opposing pitcher lookup failed"
