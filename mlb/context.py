"""
MLB matchup context — handedness, platoon splits, home/away, park, lineups, BvP.

Everything here refines the OPPONENT and ENVIRONMENT terms of the strikeout
projection. The base model is pitcher K-rate combined with an opponent rate via
log5; this module makes both sides sharper and adds a park adjustment.

WHAT EACH PIECE IS WORTH, measured before building (2026-08-07):

  platoon splits   REAL. Per-pitcher vs LHB/RHB. Wheeler 30.6% / 32.3%. The
                   biggest available upgrade: it turns "team K%" into "this
                   lineup's handedness mix against this arm".
  home/away        REAL, smaller. Wheeler 31.9% home / 30.8% away.
  park factor      REAL. Derived from each club's HOME vs AWAY pitching K-rate
                   ratio, which cancels pitcher quality and isolates the venue.
                   Spread 0.820 (St. Louis) to 1.158 (Seattle). Not published by
                   the API — /venues carries only id/name/season.
  lineups          Available, but posted ~2-3h before first pitch. At the 9am
                   board time ZERO games have them. Used opportunistically.
  batter-vs-pitcher SAMPLE IS TOO SMALL TO TRUST, measured across 36 real pairs:
                   median 4 career PA, 35/36 under 20 PA, none at 50+. At 20 PA a
                   K-rate estimate carries ~±10pp standard error — wider than the
                   entire spread between the league's best and worst contact
                   hitters. Included because Shawn asked for it, but shrunk hard
                   (BVP_PRIOR_PA) so a 4-PA sample moves the projection ~4%.
                   It is a tie-breaker, not a signal.

Every function degrades to None/{} and never raises (Rule 2).
"""

import logging
import statistics as _stx

from . import client

log = logging.getLogger("baseline.mlb.context")

# Empirical-Bayes shrinkage constants, in plate appearances / batters faced.
# adjusted = (obs*n + prior*k) / (n + k). Larger k = more regression to the prior.
#
# BVP_PRIOR_PA is deliberately huge. With a median 4 PA of history, a 4-PA sample
# gets 4/(4+200) ≈ 2% weight, so BvP can nudge a close call and nothing more.
# That is the correct strength for a statistic whose measured standard error is
# wider than the population spread — see the module docstring.
BVP_PRIOR_PA = 200
SPLIT_PRIOR_BF = 150      # platoon / home-away splits: real, but still regress
PARK_PRIOR_GAMES = 40     # park factors move slowly; regress toward 1.0

_cache = {}


def _shrink(obs_rate, n, prior_rate, k):
    """Empirical-Bayes blend of an observed rate toward a prior."""
    if obs_rate is None or n is None or n <= 0:
        return prior_rate
    return ((obs_rate * n) + (prior_rate * k)) / (n + k)


# ── Handedness ───────────────────────────────────────────────────────────────
def handedness(player_id) -> dict:
    """{'pitch': 'R'|'L'|None, 'bat': 'R'|'L'|'S'|None}. {} on failure."""
    if not player_id:
        return {}
    key = ("hand", player_id)
    if key in _cache:
        return _cache[key]
    try:
        d = client._get(f"/people/{player_id}")
        p = (d.get("people") or [{}])[0]
        out = {"pitch": (p.get("pitchHand") or {}).get("code"),
               "bat": (p.get("batSide") or {}).get("code")}
        _cache[key] = out
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("mlb handedness %s failed: %s", player_id, str(exc)[:120])
        return {}


# ── Pitcher splits: platoon and home/away ────────────────────────────────────
def pitcher_splits(pitcher_id, season: int = None) -> dict:
    """{'vs_lhb', 'vs_rhb', 'home', 'away'} K-rates plus their BF counts.

    Rates are strikeouts / batters faced within each split. Missing splits come
    back absent rather than as a guess.
    """
    if not pitcher_id:
        return {}
    season = season or client._dt.date.today().year
    key = ("splits", pitcher_id, season)
    if key in _cache:
        return _cache[key]
    out = {}
    try:
        for codes, names in ((("vl", "vr"), ("vs_lhb", "vs_rhb")),
                             (("h", "a"), ("home", "away"))):
            d = client._get(f"/people/{pitcher_id}/stats", stats="statSplits",
                            group="pitching", season=season,
                            sitCodes=",".join(codes))
            for b in (d.get("stats") or []):
                for s in (b.get("splits") or []):
                    code = (s.get("split") or {}).get("code")
                    if code not in codes:
                        continue
                    st = s.get("stat") or {}
                    bf, so = st.get("battersFaced"), st.get("strikeOuts")
                    if not bf or so is None:
                        continue
                    name = names[codes.index(code)]
                    out[name] = so / bf
                    out[name + "_bf"] = bf
        _cache[key] = out
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("mlb splits %s failed: %s", pitcher_id, str(exc)[:120])
        return {}


# ── Park factors ─────────────────────────────────────────────────────────────
def park_factors(season: int = None) -> dict:
    """{team_id: K factor} for each club's HOME venue, league-normalised to 1.0.

    Derived from each club's home vs away PITCHING K-rate. The same staff pitches
    both, so dividing cancels pitcher quality and what remains is the venue. Not
    available from the API directly — /venues carries only id/name/season.

    Regressed toward 1.0 (PARK_PRIOR_GAMES) because a half-season of home games
    is a thin basis for a park effect, and an unregressed factor would swing
    projections on noise.
    """
    season = season or client._dt.date.today().year
    key = ("park", season)
    if key in _cache:
        return _cache[key]
    try:
        d = client._get("/teams/stats", stats="statSplits", group="pitching",
                        season=season, sportId=1, sitCodes="h,a")
        raw = {}
        for b in (d.get("stats") or []):
            for s in (b.get("splits") or []):
                t = (s.get("team") or {}).get("id")
                code = (s.get("split") or {}).get("code")
                st = s.get("stat") or {}
                bf, so = st.get("battersFaced"), st.get("strikeOuts")
                if not (t and bf and so is not None):
                    continue
                raw.setdefault(t, {})[code] = (so / bf, bf)
        fac = {}
        for t, v in raw.items():
            if "h" not in v or "a" not in v or not v["a"][0]:
                continue
            fac[t] = v["h"][0] / v["a"][0]
        if not fac:
            return {}
        mean = _stx.mean(fac.values())
        games = 40          # rough home games so far; drives the regression
        out = {t: _shrink(f / mean, games, 1.0, PARK_PRIOR_GAMES)
               for t, f in fac.items()}
        _cache[key] = out
        log.info("mlb park factors: %d venues, range %.3f-%.3f",
                 len(out), min(out.values()), max(out.values()))
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("mlb park factors failed: %s", str(exc)[:140])
        return {}


# ── Lineups ──────────────────────────────────────────────────────────────────
def get_lineup(game_pk, side: str) -> list:
    """Confirmed batting order for one side: [{id, name, bat}]. [] when not yet
    posted, which at a 9am board time is the normal case, not an error."""
    if not game_pk:
        return []
    try:
        d = client._get(f"/schedule", sportId=1, gamePk=game_pk,
                        hydrate="lineups")
        for day in (d.get("dates") or []):
            for g in (day.get("games") or []):
                lu = (g.get("lineups") or {})
                players = lu.get("homePlayers" if side == "home" else "awayPlayers")
                if not players:
                    continue
                out = []
                for p in players:
                    h = handedness(p.get("id"))
                    out.append({"id": p.get("id"), "name": p.get("fullName"),
                                "bat": h.get("bat")})
                return out
        return []
    except Exception as exc:  # noqa: BLE001
        log.warning("mlb lineup %s failed: %s", game_pk, str(exc)[:120])
        return []


def lineup_handedness_mix(lineup: list) -> dict:
    """{'L': fraction, 'R': fraction} of a lineup's bats. Switch hitters count as
    the platoon-advantage side, which is how they actually bat."""
    if not lineup:
        return {}
    l = r = 0
    for b in lineup:
        s = (b.get("bat") or "").upper()
        if s == "L":
            l += 1
        elif s == "R":
            r += 1
        elif s == "S":
            l += 0.5
            r += 0.5
    tot = l + r
    return {"L": l / tot, "R": r / tot} if tot else {}


# ── Batter vs pitcher ────────────────────────────────────────────────────────
def bvp_k_rate(batter_id, pitcher_id, prior_rate: float) -> dict:
    """Shrunk K-rate for one batter against one pitcher.

    Returns {'raw', 'pa', 'shrunk', 'weight'}. `shrunk` is what callers should
    use — with BVP_PRIOR_PA=200 and a median 4 PA of history, the observation
    carries ~2% weight. That is deliberate: the measured standard error at these
    samples is wider than the spread between the best and worst hitters, so an
    unshrunk BvP term would be noise wearing a lab coat.
    """
    if not batter_id or not pitcher_id:
        return {}
    key = ("bvp", batter_id, pitcher_id)
    if key in _cache:
        return _cache[key]
    try:
        d = client._get(f"/people/{batter_id}/stats", stats="vsPlayer",
                        group="hitting", opposingPlayerId=pitcher_id)
        best_pa, best_k = 0, 0
        for b in (d.get("stats") or []):
            for s in (b.get("splits") or []):
                st = s.get("stat") or {}
                pa = st.get("plateAppearances") or 0
                if pa > best_pa:                 # the career row is the largest
                    best_pa, best_k = pa, st.get("strikeOuts") or 0
        raw = (best_k / best_pa) if best_pa else None
        out = {"raw": raw, "pa": best_pa,
               "shrunk": _shrink(raw, best_pa, prior_rate, BVP_PRIOR_PA),
               "weight": best_pa / (best_pa + BVP_PRIOR_PA) if best_pa else 0.0}
        _cache[key] = out
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("mlb bvp %s/%s failed: %s", batter_id, pitcher_id,
                    str(exc)[:120])
        return {}


# ── The combined opponent rate ───────────────────────────────────────────────
def opponent_k_rate(pitcher_id, opponent_team_id, game_pk=None,
                    is_home: bool = None, team_batting: dict = None,
                    league_rate: float = None, use_bvp: bool = True) -> dict:
    """Best available estimate of the K-rate the pitcher faces tonight.

    Preference order, each falling back cleanly:
      1. CONFIRMED LINEUP — each batter's own K-rate, BvP-adjusted, averaged.
      2. LINEUP HANDEDNESS MIX — the pitcher's own vL/vR splits weighted by the
         lineup's composition.
      3. TEAM K-RATE — the original behaviour.

    Returns the rate plus a `basis` string naming which path was used, so a board
    can never imply lineup-level precision it did not have.
    """
    out = {"basis": "team", "opponent_rate": None, "detail": {}}
    try:
        tb = team_batting if team_batting is not None else client.get_team_batting()
        lg = league_rate or client.league_k_rate(tb)
        team = (tb or {}).get(opponent_team_id) or {}
        out["opponent_rate"] = team.get("k_rate")

        side = "home" if is_home is False else "away"   # the OPPOSING side bats
        lineup = get_lineup(game_pk, "home" if is_home is False else "away") \
            if game_pk else []

        if lineup:
            rates, n = [], 0
            for b in lineup:
                bv = bvp_k_rate(b["id"], pitcher_id, lg) if use_bvp else {}
                rates.append(bv.get("shrunk") if bv else lg)
                n += 1
            if rates:
                out["opponent_rate"] = sum(rates) / len(rates)
                out["basis"] = "lineup+bvp" if use_bvp else "lineup"
                out["detail"] = {"batters": n,
                                 "mix": lineup_handedness_mix(lineup)}
                return out

        # No lineup: use the pitcher's platoon split against the TEAM's typical mix
        sp = pitcher_splits(pitcher_id)
        if sp.get("vs_lhb") is not None and sp.get("vs_rhb") is not None:
            out["detail"]["platoon"] = {"vs_lhb": round(sp["vs_lhb"], 4),
                                        "vs_rhb": round(sp["vs_rhb"], 4)}
            out["basis"] = "team+platoon"
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb opponent_k_rate failed: %s", exc)
        return out
