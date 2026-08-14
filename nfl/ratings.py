"""
NFL team ratings — offense AND defense, separately.

WHY BOTH SIDES, SEPARATELY
--------------------------
A team is not one number. A club can throw for 280 a game and give up 280; one
of those facts helps its own receivers and the other helps the opponent's. Every
rating here is computed twice — what a team DOES on offense and what it ALLOWS
on defense — because a projection needs the opponent's defensive rating, not its
reputation.

THESE RATINGS EXIST TO PREVENT OVER-INFLATION, WHICH IS THEIR MAIN JOB.
Three rules make that true:

1. RELATIVE TO LEAGUE, ALWAYS. Every rating is a ratio against the league mean,
   so an average opponent multiplies by exactly 1.000 and the adjustment is a
   no-op. An adjustment measured in absolute yards would inflate every
   projection the moment the league's passing environment shifted.

2. SHRUNK. A defense's 17 games is the same thin sample a player's is. Raw
   allowed-rates swing wildly on schedule luck, so each is blended toward 1.000
   with an empirical-Bayes prior. A "best pass defense" that faced four backup
   quarterbacks does not earn a full-strength multiplier.

3. ADJUSTMENTS APPLY TO THE RATE, NOT TO BOTH RATE AND VOLUME. The volume term
   already carries the game script from the spread, which itself already encodes
   that the opponent is good. Multiplying volume by a defensive rating too would
   count the same information twice — the classic way these models over-inflate.

Fox Sports' rankings page (yards/game, red-zone rate, third-down rate, sacks
allowed, pass/rush splits) is a subset of what is computed here, and it is
offense-only. Everything on it is derived below from play-by-play, alongside the
defensive counterpart it does not publish.
"""

import logging

log = logging.getLogger("baseline.nfl.ratings")

# Prior strength in PLAYS. A defense needs this many snaps of its own before its
# rating outweighs "league average". ~1000 plays is most of a season, which is
# deliberately heavy: defensive rate stats are famously schedule-dependent.
PRIOR_PLAYS = 900.0

# Ratings are clipped to this band. Nothing in football is 40% better than
# average over a season, and an unclipped ratio built on a small denominator
# eventually produces one.
CLIP = (0.82, 1.18)

_cache = {}


def _shrink_ratio(obs, n, k=PRIOR_PLAYS):
    """Blend a ratio toward 1.000 by sample size, then clip."""
    if obs is None or not n or n <= 0:
        return 1.0
    r = ((obs * n) + (1.0 * k)) / (n + k)
    return max(CLIP[0], min(CLIP[1], r))


def team_ratings(seasons: list = None) -> dict:
    """{team: {offense: {...}, defense: {...}}}, every value RELATIVE to league.

    A value of 1.00 is exactly league average. Above 1.00 always means "more of
    the thing" — so for a DEFENSE, above 1.00 means it ALLOWS more, i.e. it is
    worse. That direction is uniform on purpose: a projection multiplies by the
    opponent's defensive rating without needing to remember a sign.

    Never raises; returns {} if play-by-play is unavailable.
    """
    from . import client
    import pandas as pd
    key = tuple(seasons or [client.current_season() - 1])
    if key in _cache:
        return _cache[key]
    try:
        frames = [client.load("play_by_play", y) for y in key]
        frames = [f for f in frames if len(f)]
        if not frames:
            return {}
        pbp = pd.concat(frames, ignore_index=True)
        p = pbp[pbp["play_type"].isin(["pass", "run"])
                & (pbp.get("qb_kneel") != 1) & (pbp.get("qb_spike") != 1)].copy()
        p["is_pass"] = (p["play_type"] == "pass").astype(float)
        p["is_run"] = (p["play_type"] == "run").astype(float)
        p["cmp"] = p["complete_pass"].fillna(0)
        p["att"] = ((p["is_pass"] == 1) & (p["sack"].fillna(0) == 0)).astype(float)

        def side(group_col: str) -> dict:
            """Aggregate for offense (posteam) or defense (defteam)."""
            g = p.groupby(group_col)
            agg = g.apply(lambda d: pd.Series({
                "plays": len(d),
                "pass_att": d["att"].sum(),
                "rush_att": d["is_run"].sum(),
                "pass_yards": d.loc[d["is_pass"] == 1, "yards_gained"].sum(),
                "rush_yards": d.loc[d["is_run"] == 1, "yards_gained"].sum(),
                "completions": d["cmp"].sum(),
                "sacks": d["sack"].fillna(0).sum(),
                "epa_play": d["epa"].mean(),
                "success": d["success"].fillna(0).mean(),
                "explosive": (d["yards_gained"] >= 20).mean(),
            }), include_groups=False)
            agg["ypa"] = agg["pass_yards"] / agg["pass_att"].replace(0, 1)
            agg["ypc"] = agg["rush_yards"] / agg["rush_att"].replace(0, 1)
            agg["comp_pct"] = agg["completions"] / agg["pass_att"].replace(0, 1)
            agg["sack_rate"] = agg["sacks"] / (agg["pass_att"] + agg["sacks"]).replace(0, 1)
            return agg

        off = side("posteam")
        de = side("defteam")

        # League means, used as the denominator for every ratio.
        lg = {c: float(pd.concat([off[c], de[c]]).mean())
              for c in ("ypa", "ypc", "comp_pct", "sack_rate", "success",
                        "explosive")}
        lg_epa = 0.0        # EPA/play is already centred near zero

        out = {}
        teams = sorted(set(off.index) | set(de.index))
        for t in teams:
            if t is None or (isinstance(t, float) and pd.isna(t)):
                continue
            rec = {}
            for label, frame in (("offense", off), ("defense", de)):
                if t not in frame.index:
                    continue
                r = frame.loc[t]
                n_pass = float(r["pass_att"])
                n_rush = float(r["rush_att"])
                rec[label] = {
                    "plays": int(r["plays"]),
                    # Relative ratings — 1.00 = league average.
                    "pass_yds_per_att": round(_shrink_ratio(
                        r["ypa"] / lg["ypa"], n_pass), 4),
                    "rush_yds_per_att": round(_shrink_ratio(
                        r["ypc"] / lg["ypc"], n_rush), 4),
                    "completion_pct": round(_shrink_ratio(
                        r["comp_pct"] / lg["comp_pct"], n_pass), 4),
                    "sack_rate": round(_shrink_ratio(
                        r["sack_rate"] / lg["sack_rate"], n_pass), 4),
                    "success_rate": round(_shrink_ratio(
                        r["success"] / lg["success"], r["plays"]), 4),
                    "explosive_rate": round(_shrink_ratio(
                        r["explosive"] / lg["explosive"], r["plays"]), 4),
                    # Raw, for display and sanity checks — NOT used in maths.
                    "raw": {
                        "ypa": round(float(r["ypa"]), 2),
                        "ypc": round(float(r["ypc"]), 2),
                        "comp_pct": round(float(r["comp_pct"]), 4),
                        "epa_per_play": round(float(r["epa_play"]), 4),
                        "sack_rate": round(float(r["sack_rate"]), 4),
                    },
                }
            if rec:
                out[t] = rec
        _cache[key] = out
        log.info("nfl ratings: %d teams from %d plays (%s)", len(out), len(p), key)
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("nfl team_ratings failed: %s", exc)
        return {}


def opponent_factor(defense_team: str, prop: str, seasons: list = None) -> dict:
    """The multiplier a projection should apply for facing this defense.

    Returns {"factor": float, "basis": str}. 1.000 with basis "unknown" when the
    team has no rating — an unrecognised opponent must not silently become an
    average one without saying so.

    ONE FACTOR PER PROP FAMILY, applied to the RATE only. The volume side already
    reflects the opponent through the spread, so applying a defensive rating
    there as well would double-count the same information — which is exactly how
    these projections over-inflate.
    """
    from .client import normalize_team
    r = (team_ratings(seasons) or {}).get(normalize_team(defense_team)) or {}
    d = r.get("defense")
    if not d:
        return {"factor": 1.0, "basis": "unknown"}
    field = {
        "receiving_yards": "pass_yds_per_att",
        "receptions": "completion_pct",
        "rush_yards": "rush_yds_per_att",
        "pass_yards": "pass_yds_per_att",
    }.get(prop)
    if not field:
        return {"factor": 1.0, "basis": "unmapped prop"}
    return {"factor": float(d[field]), "basis": f"def {field}",
            "raw": d.get("raw", {})}


def rankings(metric: str = "pass_yds_per_att", side: str = "defense",
             seasons: list = None) -> list:
    """[(team, rating)] sorted best-defence-first / best-offence-first.

    For a defence, LOWER is better (it allows less), so the sort flips. Reading
    a defensive table sorted the wrong way is an easy way to adjust a projection
    in precisely the wrong direction.
    """
    r = team_ratings(seasons)
    rows = [(t, v[side][metric]) for t, v in r.items() if side in v]
    return sorted(rows, key=lambda x: x[1], reverse=(side == "offense"))
