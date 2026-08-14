"""
NFL player usage — the share of his team's volume a player actually gets.

THIS IS THE HARD PART OF FOOTBALL, and it is what makes NFL props different from
MLB. A starting pitcher faces about 22 batters whatever happens; a receiver's
targets swing with the depth chart, the script and who else is healthy. So the
volume term is itself uncertain, and pretending otherwise is the main way an NFL
projection goes wrong.

SHRINKAGE IS NOT OPTIONAL AT 17 GAMES. MLB's MIN_STARTS=5 is 3% of a season;
five NFL games is nearly a third of one. A target share off four games is mostly
noise, so every rate here is blended toward the player's prior season and then
toward a position baseline, with the weight set by how much we have actually
seen. Same empirical-Bayes construction the tennis side uses, just with a much
heavier prior because the samples are so much smaller.

RECENCY MATTERS MORE THAN IN OTHER SPORTS. A role change — a trade, an injury
ahead of him, a coordinator change — makes early-season usage actively
misleading rather than merely noisy. Recent games are weighted up, and the
window is reported so a caller can see how thin it is.
"""

import logging

log = logging.getLogger("baseline.nfl.usage")

# Games below this and the rate is a rumour. Kept low because the NFL season is
# short and the alternative is projecting nobody — but the shrinkage below does
# the real work of keeping a thin sample honest.
MIN_GAMES = 4

# Empirical-Bayes prior strength, in games.
#
# Set to 4, not 8, and the difference matters. The position prior is the ALL-WR
# average, which includes every WR3 on the field for twelve snaps — so it is a
# poor prior for a number-one receiver, and a heavy k drags every star toward a
# role he does not have. At k=4 a full 17-game season keeps ~81% of its own
# signal, which is the right balance when the prior itself is that crude.
#
# The first pass used k=8 AND passed the recency-weight sum (~6.5 for a full
# season) as the sample size, so the prior actually outweighed seventeen games:
# Jaxon Smith-Njigba's measured 0.360 target share came out at 0.254. Shrinkage
# should temper a thin sample, not overrule a complete one.
PRIOR_GAMES = 4.0

# Half-life for recency weighting, in games. A game 6 back counts half as much
# as last week's.
RECENCY_HALFLIFE = 6.0

# Position baselines, used as the prior when a player has no history. Measured
# from 2023-2025 regular season, players with >= 8 games.
POSITION_PRIOR = {
    "WR":  {"target_share": 0.150, "catch_rate": 0.640, "yards_per_target": 8.10,
            "carry_share": 0.010, "yards_per_carry": 6.20},
    "TE":  {"target_share": 0.115, "catch_rate": 0.700, "yards_per_target": 7.40,
            "carry_share": 0.005, "yards_per_carry": 3.50},
    "RB":  {"target_share": 0.075, "catch_rate": 0.760, "yards_per_target": 6.20,
            "carry_share": 0.480, "yards_per_carry": 4.30},
    "QB":  {"target_share": 0.000, "catch_rate": 0.000, "yards_per_target": 0.00,
            "carry_share": 0.090, "yards_per_carry": 4.60},
}


def _shrink(obs, n, prior, k=PRIOR_GAMES):
    """Empirical-Bayes blend of an observed rate toward a prior."""
    if obs is None or not n or n <= 0:
        return prior
    return ((obs * n) + (prior * k)) / (n + k)


def _weighted(vals, weights):
    tot = sum(weights)
    return (sum(v * w for v, w in zip(vals, weights)) / tot) if tot else None


def player_usage(player: str, season: int = None, position: str = None,
                 before_week: int = None) -> dict:
    """A player's usage and efficiency rates, shrunk and recency-weighted.

    Returns {} when there is not enough history to say anything. Never raises.

    The returned `games` and `window` are not decoration — a caller showing a
    projection built on five games should be able to say so.

    `before_week` restricts the CURRENT season to weeks strictly before it. It
    exists for backtesting: a projection evaluated against week 9 must be built
    only from weeks 1-8, or the test measures nothing but hindsight. Prior
    seasons are always fully available, which is what a real Sunday looks like.
    """
    from . import client
    import pandas as pd
    try:
        season = season or client.current_season()
        frames = []
        # Current season first, then the prior one. In August the current season
        # has not started, so the prior season IS the sample — that is normal,
        # not a fallback, and it is reported in `window`.
        for yr, tag in ((season, "current"), (season - 1, "prior")):
            df = client.load("stats_player_week", yr)
            if not len(df):
                continue
            d = df[(df.get("season_type") == "REG")
                   & (df.get("player_display_name") == player)]
            if before_week and tag == "current":
                d = d[d["week"] < before_week]
            if len(d):
                frames.append(d.assign(_src=tag, _season=yr))
        if not frames:
            log.info("nfl usage: no rows for %r", player)
            return {}
        hist = pd.concat(frames, ignore_index=True)
        hist = hist.sort_values(["_season", "week"])
        n = len(hist)
        if n < MIN_GAMES:
            log.info("nfl usage: %s has %d game(s) (< %d)", player, n, MIN_GAMES)
            return {}

        pos = position or (hist["position"].dropna().iloc[-1]
                           if hist["position"].notna().any() else "WR")
        prior = POSITION_PRIOR.get(pos, POSITION_PRIOR["WR"])

        # Recency weights: most recent game weight 1, halving every
        # RECENCY_HALFLIFE games back.
        order = list(range(n - 1, -1, -1))          # 0 = most recent
        w = [0.5 ** (i / RECENCY_HALFLIFE) for i in order]

        def wsum(col):
            if col not in hist:
                return 0.0
            vals = hist[col].fillna(0).tolist()
            return sum(v * ww for v, ww in zip(vals, w))

        wn = sum(w)
        tgt, rec = wsum("targets"), wsum("receptions")
        rec_yds = wsum("receiving_yards")
        car, rush_yds = wsum("carries"), wsum("rushing_yards")
        att, pass_yds = wsum("attempts"), wsum("passing_yards")
        cmp_ = wsum("completions")
        pass_tds, ints = wsum("passing_tds"), wsum("passing_interceptions")

        # target_share is precomputed per game by nflverse; average it the same
        # recency-weighted way rather than recomputing from team totals.
        ts_vals = hist.get("target_share")
        ts = (_weighted(ts_vals.fillna(0).tolist(), w)
              if ts_vals is not None else None)

        out = {
            "player": player, "position": pos,
            "games": n,
            "window": ("current season" if (hist["_src"] == "current").all()
                       else "current + prior season"
                       if (hist["_src"] == "current").any()
                       else "prior season only"),
            "effective_n": round(wn, 1),
            # Raw, before shrinkage — shown so the shrink is visible.
            "raw": {
                "targets_per_game": round(tgt / wn, 2) if wn else None,
                "catch_rate": round(rec / tgt, 4) if tgt else None,
                "yards_per_target": round(rec_yds / tgt, 3) if tgt else None,
                "carries_per_game": round(car / wn, 2) if wn else None,
                "yards_per_carry": round(rush_yds / car, 3) if car else None,
                "pass_att_per_game": round(att / wn, 2) if wn else None,
                "yards_per_attempt": round(pass_yds / att, 3) if att else None,
                "completion_pct": round(cmp_ / att, 4) if att else None,
                "yards_per_completion": round(pass_yds / cmp_, 3) if cmp_ else None,
                "target_share": round(ts, 4) if ts else None,
            },
        }
        # Shrunk rates — these are what the projection uses.
        # n here is REAL GAMES, not the recency-weight sum. The weights decide
        # how the average is computed; they must not also shrink the sample.
        out["target_share"] = round(
            _shrink(ts, n, prior["target_share"]), 4)
        out["catch_rate"] = round(
            _shrink(rec / tgt if tgt else None, tgt, prior["catch_rate"],
                    k=25.0), 4)              # k in TARGETS, not games
        out["yards_per_target"] = round(
            _shrink(rec_yds / tgt if tgt else None, tgt,
                    prior["yards_per_target"], k=25.0), 3)
        out["yards_per_carry"] = round(
            _shrink(rush_yds / car if car else None, car,
                    prior["yards_per_carry"], k=40.0), 3)   # k in CARRIES
        out["carries_per_game"] = round(car / wn, 2) if wn else 0.0
        out["targets_per_game"] = round(tgt / wn, 2) if wn else 0.0
        out["pass_att_per_game"] = round(att / wn, 2) if wn else 0.0
        out["yards_per_attempt"] = round(
            _shrink(pass_yds / att if att else None, att, 7.05, k=120.0), 3)
        # QB accuracy, kept SEPARATE from yards per attempt on purpose.
        # Completion % is far more stable week to week than Y/A — the yardage
        # swings on a couple of deep shots, the accuracy does not — and a
        # defence suppresses the two differently. Splitting them lets the
        # opponent adjustment hit the term it actually moves.
        out["completion_pct"] = round(
            _shrink(cmp_ / att if att else None, att, 0.650, k=120.0), 4)
        out["yards_per_completion"] = round(
            _shrink(pass_yds / cmp_ if cmp_ else None, cmp_, 10.85, k=80.0), 3)
        out["pass_td_rate"] = round(pass_tds / att, 4) if att else 0.0
        out["int_rate"] = round(ints / att, 4) if att else 0.0
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("nfl usage failed for %r: %s", player, exc)
        return {}


def team_tendency(team: str, season: int = None) -> dict:
    """A team's own pace and pass rate, for feeding volume.team_volume().

    Falls back to league-neutral when the team has no rows — a new season before
    week 1 is the normal case for that, not an error.
    """
    from . import client
    try:
        season = season or client.current_season()
        for yr in (season, season - 1):
            df = client.load("stats_team_week", yr)
            if not len(df):
                continue
            d = df[(df.get("season_type") == "REG") & (df.get("team") == team)]
            if not len(d):
                continue
            att = float(d.get("attempts").fillna(0).sum())
            car = float(d.get("carries").fillna(0).sum())
            g = len(d)
            if not g or (att + car) <= 0:
                continue
            return {"team": team, "season": yr, "games": g,
                    "plays_per_game": round((att + car) / g, 2),
                    "pass_rate": round(att / (att + car), 4)}
        return {}
    except Exception as exc:  # noqa: BLE001
        log.warning("nfl team_tendency failed for %r: %s", team, str(exc)[:140])
        return {}
