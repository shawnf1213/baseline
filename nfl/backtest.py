"""
NFL backtest — does this model beat doing nothing?

THE QUESTION THIS EXISTS TO ANSWER. Everything in nfl/ was built from careful
reasoning and fitted parameters, and none of that establishes that the pipeline
predicts better than a player's own season average. A model that loses to its
own baseline is worse than no model, because it looks like work.

WALK-FORWARD, NO LOOKAHEAD. For a week-9 game the projection may use only weeks
1-8 of that season plus prior seasons — which is what a real Sunday looks like.
Team ratings come from the PRIOR season for the same reason: on week 9 you do
not have the current season's finished defensive ratings, and using them would
flatter the model with information it could not have had.

MARKET LINES COME FROM THE GAMES THEMSELVES. nflverse carries the closing
spread and total on every play, so the script mixture is weighted by the line
that was actually up, not by hindsight.

BASELINES, both deliberately trivial:
    season_avg   the player's mean in the weeks before this one
    last4        his mean over the previous four games
Beating them on mean absolute error is the minimum bar. Not beating them means
the volume/rate/script machinery is adding noise, and the honest response is to
say so rather than ship it.
"""

import logging

log = logging.getLogger("baseline.nfl.backtest")

# Actual columns for each prop, and the volume field that gates board relevance.
ACTUAL = {
    "receiving_yards": ("receiving_yards", "targets", 3.0),
    "receptions": ("receptions", "targets", 3.0),
    "rush_yards": ("rushing_yards", "carries", 6.0),
    "pass_yards": ("passing_yards", "attempts", 15.0),
}


def run(season: int = 2025, props: list = None, first_week: int = 6,
        last_week: int = 18, min_prior: int = 4, max_players: int = None) -> dict:
    """Walk forward through a season and score the model against baselines.

    Returns {prop: {n, model_mae, season_avg_mae, last4_mae, ...}}.
    Never raises.
    """
    from . import client, usage as _usage, props as _props, ratings as _rat
    import pandas as pd
    import numpy as np
    try:
        props = props or list(ACTUAL)
        wk = client.load("stats_player_week", season)
        if not len(wk):
            log.warning("nfl backtest: no weekly stats for %s", season)
            return {}
        wk = wk[wk.get("season_type") == "REG"].copy()

        # Market lines and the home/away mapping, straight from play-by-play.
        pbp = client.load("play_by_play", season)
        if not len(pbp):
            return {}
        games = (pbp.groupby("game_id")
                    .agg(week=("week", "first"), home=("home_team", "first"),
                         away=("away_team", "first"),
                         spread_line=("spread_line", "first"),
                         total_line=("total_line", "first"))
                    .reset_index())
        # SIGN CHECK, not an assumption. Correlate the line with the realised
        # home margin; a positive correlation means spread_line is stated from
        # the home team's perspective as "points favoured".
        res = (pbp.groupby("game_id")["result"].first().reset_index())
        chk = games.merge(res, on="game_id").dropna(subset=["spread_line", "result"])
        corr = float(np.corrcoef(chk["spread_line"], chk["result"])[0, 1]) if len(chk) > 10 else 0.0
        home_fav_positive = corr > 0
        log.info("nfl backtest: spread_line vs result corr=%.3f -> home-favoured "
                 "is %s", corr, "positive" if home_fav_positive else "negative")

        gmap = {}
        for _, g in games.iterrows():
            sl = g["spread_line"]
            if pd.isna(sl):
                home_spread = None
            else:
                # Normalise to "this team's spread, negative = favoured".
                home_spread = -float(sl) if home_fav_positive else float(sl)
            gmap[(g["week"], g["home"])] = {
                "player_team": g["home"], "opponent_team": g["away"],
                "player_spread": home_spread, "total": g["total_line"]}
            gmap[(g["week"], g["away"])] = {
                "player_team": g["away"], "opponent_team": g["home"],
                "player_spread": (-home_spread if home_spread is not None else None),
                "total": g["total_line"]}

        # Ratings from the PRIOR season only — no lookahead.
        _rat.team_ratings([season - 1])

        out = {}
        for prop in props:
            col, volcol, floor = ACTUAL[prop]
            rows = []
            d = wk[["player_display_name", "team", "week", col, volcol]].dropna()
            # Board-relevant players only: PrizePicks does not post a line for a
            # man averaging one target, and scoring those measures nothing.
            elig = (d.groupby("player_display_name")[volcol].mean()
                     .pipe(lambda s: s[s >= floor]).index)
            if max_players:
                elig = list(elig)[:max_players]
            d = d[d["player_display_name"].isin(elig)]

            for name, grp in d.groupby("player_display_name"):
                grp = grp.sort_values("week")
                hist = []
                for _, r in grp.iterrows():
                    w = int(r["week"])
                    actual = float(r[col])
                    if w < first_week or w > last_week:
                        hist.append(actual)
                        continue
                    if len(hist) < min_prior:
                        hist.append(actual)
                        continue
                    game = gmap.get((w, r["team"]))
                    p = _props.project(name, prop, None, game=game,
                                       season=season)
                    # Rebuild usage with the cutoff — project() does not know
                    # about backtest time, so it is passed through here.
                    u = _usage.player_usage(name, season=season, before_week=w)
                    if not u or not p:
                        hist.append(actual)
                        continue
                    p = _project_with_usage(_props, u, prop, game, season)
                    if p is None:
                        hist.append(actual)
                        continue
                    rows.append({
                        "player": name, "week": w, "actual": actual,
                        "model": p,
                        "season_avg": float(np.mean(hist)),
                        "last4": float(np.mean(hist[-4:])),
                    })
                    hist.append(actual)

            if not rows:
                continue
            f = pd.DataFrame(rows)
            def mae(c):
                return float((f[c] - f["actual"]).abs().mean())
            def bias(c):
                return float((f[c] - f["actual"]).mean())
            out[prop] = {
                "n": len(f), "players": int(f["player"].nunique()),
                "actual_mean": round(float(f["actual"].mean()), 2),
                "model_mae": round(mae("model"), 2),
                "season_avg_mae": round(mae("season_avg"), 2),
                "last4_mae": round(mae("last4"), 2),
                "model_bias": round(bias("model"), 2),
                "season_avg_bias": round(bias("season_avg"), 2),
                "model_beats_season_avg": mae("model") < mae("season_avg"),
                "model_beats_last4": mae("model") < mae("last4"),
                "improvement_pct": round(
                    100.0 * (mae("season_avg") - mae("model")) / mae("season_avg"), 2),
            }
            log.info("nfl backtest %s: %s", prop, out[prop])
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("nfl backtest failed: %s", exc)
        return {}


def _project_with_usage(_props, u, prop, game, season):
    """Recreate props.project()'s maths from an already-cutoff usage dict.

    Deliberately duplicates the arithmetic rather than re-fetching usage, so the
    backtest can hold the sample at a point in time. Any change to project()
    must be mirrored here or the test stops measuring the shipped model — the
    trade-off accepted for a lookahead-free evaluation.
    """
    from . import volume as _vol, usage as _usage, ratings as _rat
    try:
        team = (game or {}).get("player_team")
        tend = _usage.team_tendency(team, season) if team else {}
        opp = (_rat.opponent_factor((game or {}).get("opponent_team"), prop)
               if (game or {}).get("opponent_team") else {"factor": 1.0})
        of = opp.get("factor", 1.0)
        v = _vol.team_volume((game or {}).get("player_spread"),
                             (game or {}).get("total"),
                             own_pass_rate=tend.get("pass_rate"),
                             own_plays=tend.get("plays_per_game"))
        neutral = (v["plays"] * v["pass_rate"]) or 1.0
        script_ratio = v["pass_att"] / neutral
        if prop == "pass_yards":
            att = (u["pass_att_per_game"] * script_ratio
                   if u.get("pass_att_per_game") else v["pass_att"])
            return att * (u["completion_pct"] * of) * u["yards_per_completion"]
        if prop == "rush_yards":
            share = (u["carries_per_game"] /
                     (tend.get("plays_per_game", 63.0) *
                      (1 - tend.get("pass_rate", 0.57)))) if tend else None
            share = share if share and 0 < share < 1 else u["carries_per_game"] / 27.0
            return v["rush_att"] * min(0.95, max(0.0, share)) * u["yards_per_carry"] * of
        targets = v["pass_att"] * u["target_share"]
        if prop == "receptions":
            return targets * u["catch_rate"] * of
        return targets * u["yards_per_target"] * of
    except Exception:  # noqa: BLE001
        return None
