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
        # One call per code, for the same truncation reason as park_factors.
        for codes, names in ((("vl", "vr"), ("vs_lhb", "vs_rhb")),
                             (("h", "a"), ("home", "away"))):
          for _c in codes:
            d = client._get(f"/people/{pitcher_id}/stats", stats="statSplits",
                            group="pitching", season=season, sitCodes=_c)
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
def park_factors(season: int = None, metric: str = "k") -> dict:
    """{team_id: factor} for each club's HOME venue, league-normalised to 1.0.

    METRIC MATTERS AND MUST NOT BE ASSUMED. This used to compute strikeouts
    only, and a strikeout park is NOT a run park — Coors suppresses breaking
    balls and inflates runs at the same time. Using the K factor to adjust
    earned runs or hits would apply a real number to the wrong question.
    Supported: "k", "runs", "hits", "hr".

    Derived from each club's home vs away PITCHING K-rate. The same staff pitches
    both, so dividing cancels pitcher quality and what remains is the venue. Not
    available from the API directly — /venues carries only id/name/season.

    Regressed toward 1.0 (PARK_PRIOR_GAMES) because a half-season of home games
    is a thin basis for a park effect, and an unregressed factor would swing
    projections on noise.
    """
    season = season or client._dt.date.today().year
    key = ("park", season, metric)
    if key in _cache:
        return _cache[key]
    try:
        # ONE CALL PER SIT CODE. Requesting "h,a" together returns a TRUNCATED
        # 50 splits instead of 60, so nine clubs came back with only one side and
        # were silently dropped — including Colorado, the largest park effect in
        # baseball. Two calls return 30 each and cost nothing.
        raw = {}
        _blocks = []
        for _code in ("h", "a"):
            _d = client._get("/teams/stats", stats="statSplits", group="pitching",
                             season=season, sportId=1, sitCodes=_code)
            _blocks.extend(_d.get("stats") or [])
        for b in _blocks:
            for s in (b.get("splits") or []):
                t = (s.get("team") or {}).get("id")
                code = (s.get("split") or {}).get("code")
                st = s.get("stat") or {}
                bf = st.get("battersFaced")
                num = {"k": st.get("strikeOuts"), "runs": st.get("runs"),
                       "hits": st.get("hits"), "hr": st.get("homeRuns")}.get(metric)
                if not (t and bf and num is not None):
                    continue
                raw.setdefault(t, {})[code] = (num / bf, bf)
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
        log.info("mlb park factors (%s): %d venues, range %.3f-%.3f",
                 metric, len(out), min(out.values()), max(out.values()))
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("mlb park factors (%s) failed: %s", metric, str(exc)[:140])
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


# ── Batter platoon splits ────────────────────────────────────────────────────
def batter_splits(batter_id, season: int = None) -> dict:
    """A hitter's rates by pitcher hand AND by home/away.

    {'vs_lhp','vs_rhp','home','away'} with hit/k/hr/bb per plate appearance and
    the PA behind each.

    THE PLATOON SPLIT IS THE LARGEST UNUSED SIGNAL ON THE BATTER SIDE. A
    right-handed hitter facing a lefty is a different hitter — the effect runs
    around ten percent on contact rates and more on power, which dwarfs most of
    what the model already adjusts for. It was computed nowhere and used nowhere.
    """
    if not batter_id:
        return {}
    season = season or client._dt.date.today().year
    key = ("bsplits", batter_id, season)
    if key in _cache:
        return _cache[key]
    out = {}
    try:
        # One call per code — the API truncates multi-code split requests, which
        # is what silently cost nine parks their factors.
        for code, name in (("vl", "vs_lhp"), ("vr", "vs_rhp"),
                           ("h", "home"), ("a", "away")):
            d = client._get(f"/people/{batter_id}/stats", stats="statSplits",
                            group="hitting", season=season, sitCodes=code)
            for b in (d.get("stats") or []):
                for s in (b.get("splits") or []):
                    if (s.get("split") or {}).get("code") != code:
                        continue
                    st = s.get("stat") or {}
                    pa = st.get("plateAppearances")
                    if not pa:
                        continue
                    out[name] = {
                        "pa": pa,
                        "hit_rate": (st.get("hits") or 0) / pa,
                        "k_rate": (st.get("strikeOuts") or 0) / pa,
                        "hr_rate": (st.get("homeRuns") or 0) / pa,
                        "bb_rate": (st.get("baseOnBalls") or 0) / pa,
                        "ops": float(st.get("ops") or 0) or None,
                    }
        _cache[key] = out
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("mlb batter splits %s failed: %s", batter_id, str(exc)[:120])
        return {}


def platoon_factor(batter_id, pitcher_id, prop: str, season: int = None):
    """(multiplier, basis) for this hitter against this pitcher's HAND.

    Compares the hitter's rate against that hand to his OWN overall rate, so the
    factor isolates the platoon effect rather than re-stating how good he is —
    his overall quality is already in the projection.

    Shrunk by the split's plate appearances: a 40-PA split is a hint, not a fact.
    """
    field = {"hits": "hit_rate", "singles": "hit_rate", "total_bases": "hit_rate",
             "doubles": "hit_rate", "triples": "hit_rate",
             "runs": "hit_rate", "rbis": "hit_rate",
             "hits_runs_rbis": "hit_rate", "hitter_fantasy_score": "hit_rate",
             "hitter_strikeouts": "k_rate", "walks": "bb_rate",
             "home_runs": "hr_rate"}.get(prop)
    if not field:
        return 1.0, "prop not platoon-sensitive"
    hand = (handedness(pitcher_id) or {}).get("pitch")
    if hand not in ("L", "R"):
        return 1.0, "pitcher hand unknown"
    sp = batter_splits(batter_id, season)
    side = sp.get("vs_lhp" if hand == "L" else "vs_rhp")
    other = sp.get("vs_rhp" if hand == "L" else "vs_lhp")
    if not side or not other:
        return 1.0, "platoon split unavailable"
    own_pa = (side.get("pa") or 0) + (other.get("pa") or 0)
    overall = (((side[field] * side["pa"]) + (other[field] * other["pa"]))
               / own_pa) if own_pa else None
    if not overall or overall <= 0:
        return 1.0, "platoon baseline unavailable"
    raw = side[field] / overall
    # Shrink toward 1.0 by the split's own sample.
    f = _shrink(raw, side.get("pa") or 0, 1.0, SPLIT_PRIOR_BF)
    f = max(0.78, min(1.28, f))
    return f, f"vs {hand}HP ({side.get('pa')} PA) {side[field]:.3f} vs own {overall:.3f}"


# ── Pitcher role and rest ────────────────────────────────────────────────────
RELIEVER_GS_RATIO = 0.50     # starts / appearances below this = bullpen arm

# SHORT REST IS NOT A USABLE SIGNAL, MEASURED ON 1,143 START PAIRS FROM 106
# CURRENT STARTERS. It was going to be wired into the projection until the
# effect was checked, and there are two independent reasons not to:
#
#   1. It barely happens. Days BETWEEN starts:
#          3 days   0 starts
#          4 days   3 starts
#          5 days 381 starts   (the standard four-days-rest rotation)
#          6+     759 starts
#      A short-rest term would fire on roughly 0.3% of starts.
#
#   2. Where variation DOES exist there is no effect. Standard rotation against
#      extra rest: 23.07 vs 23.08 batters faced, 5.15 vs 5.14 strikeouts,
#      16.08 vs 16.19 outs. Not a small effect — none.
#
# days_rest() is therefore REPORTED as context and never multiplied into a
# projection. Applying it would add a term that does nothing on almost every
# start and fires on noise for the rest.
SHORT_REST_DAYS = 4


def role_profile(pitcher_id, season: int = None) -> dict:
    """{games, starts, gs_ratio, is_reliever} for a pitcher this season.

    CATCHES A FIRST-TIME OPENER, which the batters-faced test cannot. A club can
    name a reliever as the probable starter for a bullpen game, and until he
    throws that one inning his game log looks like nothing at all — the BF
    history that flagged Sean Newcomb only exists AFTER he has already opened
    once. Starts per appearance flags him beforehand: Newcomb is 2 starts in 45
    games (0.04), a bullpen arm, while a real starter sits at 1.00.
    """
    if not pitcher_id:
        return {}
    season = season or client._dt.date.today().year
    key = ("role", pitcher_id, season)
    if key in _cache:
        return _cache[key]
    try:
        d = client._get(f"/people/{pitcher_id}/stats", stats="season",
                        group="pitching", season=season)
        for b in (d.get("stats") or []):
            for s in (b.get("splits") or []):
                st = s.get("stat") or {}
                g, gs = st.get("gamesPlayed"), st.get("gamesStarted")
                if not g:
                    continue
                ratio = (gs or 0) / g
                out = {"games": g, "starts": gs or 0,
                       "gs_ratio": round(ratio, 3),
                       "is_reliever": ratio < RELIEVER_GS_RATIO}
                _cache[key] = out
                return out
        return {}
    except Exception as exc:  # noqa: BLE001
        log.warning("mlb role profile %s failed: %s", pitcher_id, str(exc)[:120])
        return {}


def days_rest(pitcher_id, game_date=None, season: int = None) -> dict:
    """Days since this pitcher's last start. {} when unknown.

    DIAGNOSTIC ONLY — never multiplied into a projection. The intuition that
    short rest shortens a start is reasonable and the data does not support it:
    see the note on SHORT_REST_DAYS above. Kept because "he is on nine days
    rest coming off the injured list" is worth SEEING next to a projection even
    though it does not earn an adjustment.
    """
    try:
        from . import pitcher_props as _pp
        import datetime as _d
        rows = _pp._start_rows(pitcher_id)
        if not rows:
            return {}
        dates = sorted(str(r.get("date"))[:10] for r in rows if r.get("date"))
        if not dates:
            return {}
        ref = str(game_date)[:10] if game_date else _d.date.today().isoformat()
        prior = [x for x in dates if x < ref]
        if not prior:
            return {}
        last = _d.date.fromisoformat(prior[-1])
        rest = (_d.date.fromisoformat(ref) - last).days
        return {"last_start": prior[-1], "days_rest": rest,
                "short_rest": rest < SHORT_REST_DAYS}
    except Exception as exc:  # noqa: BLE001
        log.warning("mlb days_rest %s failed: %s", pitcher_id, str(exc)[:120])
        return {}


def batter_home_away_factor(batter_id, is_home: bool, prop: str,
                            season: int = None):
    """(multiplier, basis) for a hitter's home/away tendency.

    Measured against his OWN combined rate, so it isolates the venue habit
    rather than restating his quality. Small by nature — a few percent — and
    shrunk hard, because a home/away split is one of the noisiest cuts there is
    and it is easy to mistake a hot month for a home-field skill.
    """
    if is_home is None:
        return 1.0, "home/away unknown"
    field = {"hits": "hit_rate", "singles": "hit_rate", "total_bases": "hit_rate",
             "doubles": "hit_rate", "triples": "hit_rate", "runs": "hit_rate",
             "rbis": "hit_rate", "hits_runs_rbis": "hit_rate",
             "hitter_fantasy_score": "hit_rate",
             "hitter_strikeouts": "k_rate", "walks": "bb_rate",
             "home_runs": "hr_rate"}.get(prop)
    if not field:
        return 1.0, "prop not venue-sensitive"
    sp = batter_splits(batter_id, season)
    side = sp.get("home" if is_home else "away")
    other = sp.get("away" if is_home else "home")
    if not side or not other:
        return 1.0, "home/away split unavailable"
    tot = (side.get("pa") or 0) + (other.get("pa") or 0)
    overall = (((side[field] * side["pa"]) + (other[field] * other["pa"])) / tot
               if tot else None)
    if not overall or overall <= 0:
        return 1.0, "home/away baseline unavailable"
    f = _shrink(side[field] / overall, side.get("pa") or 0, 1.0,
                SPLIT_PRIOR_BF * 2)      # extra-heavy: this split is very noisy
    f = max(0.90, min(1.10, f))
    return f, f"{'home' if is_home else 'away'} ({side.get('pa')} PA)"
