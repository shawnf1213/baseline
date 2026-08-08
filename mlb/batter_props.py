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
                    "date": s.get("date"), "pa": pa, "h": h,
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
            **kw) -> dict:
    """Project one batter prop. {} when unsupported or the sample is too thin.

    `lineup_confirmed` is carried through, never enforced here — the board
    decides whether to post an unconfirmed batter. Making it visible rather than
    silently assuming he plays is the point.
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

        if prop in COMPOSITE_PROPS:
            if prop == "hitter_fantasy_score":
                vals = [_fs_of(r) for r in rows]
                model = "empirical per-game Fantasy Score (components correlated)"
            else:                                    # hits+runs+rbis
                vals = [r["h"] + r["r"] + r["rbi"] for r in rows]
                model = "empirical per-game H+R+RBI (components correlated)"
            mu = sum(vals) / n
            sd = (sum((v - mu) ** 2 for v in vals) / n) ** 0.5
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
        per_game = total / n
        pa_total = sum(r["pa"] for r in rows) or 1
        out.update({"projection": round(per_game, 2),
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
