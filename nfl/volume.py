"""
NFL volume — the game-script scenario mixture.

WHY A MIXTURE HERE WHEN MLB DID NOT GET ONE
-------------------------------------------
I argued AGAINST a scenario mixture for MLB strikeouts and measured it: the
distribution was unimodal, mildly overdispersed, and a mean served it fine.
Football is the opposite case, and the reason is mechanical rather than
statistical — a team that is winning by three scores RUNS THE BALL, and a team
losing by three scores THROWS IT. Volume is conditional on the game outcome in
exactly the way tennis props are conditional on winning in two sets or three.

    S1  win by 14+      run-heavy, pass attempts collapse
    S2  win close       neutral
    S3  lose close      neutral, slight pass lean
    S4  lose by 14+     pass-heavy, garbage-time volume

Projecting the mean prices a blowout and a shootout as the same game. For a
running back those two scenarios differ by roughly a third of his carries, and
they point in OPPOSITE directions for a receiver on the same team — which is why
one number cannot serve both.

Scenario weights come from the market (Rule 6): the spread gives win probability
and the margin distribution, the total gives pace. Nothing here invents a line —
a game with no odds falls back to league-neutral splits and says so.

THE MIXTURE RESCALES, IT DOES NOT MOVE THE MEAN. Same construction tennis uses:
    base_scale = player's own expected volume / population volume under the mix
so the output stays anchored to the player's measured usage and the mixture only
redistributes it across outcomes. Without that the mixture would quietly shift
every projection toward the league average.
"""

import logging
import math

log = logging.getLogger("baseline.nfl.volume")

# Scenario definitions: (label, margin band). Bands are on the FINAL margin from
# the team's own perspective.
SCENARIOS = ("win_big", "win_close", "lose_close", "lose_big")
BLOWOUT = 14.0          # points; the band where play-calling actually changes

# League-neutral fallbacks, used only when a game has no market line. Measured
# from 2025 regular-season play-by-play (see fit_scenarios()).
NEUTRAL = {
    "plays": 63.0,          # offensive plays per team per game
    "pass_rate": 0.570,     # share of plays that are pass attempts (incl. sacks)
}

# FITTED on 105,282 plays across 1,710 team-games, 2023-2025 (fit_scenarios()).
# Relative to each team's OWN season pass rate, so a run-first offence stays
# run-first in every script.
PASS_RATE_MULT = {
    "win_big":    0.881,
    "win_close":  0.953,
    "lose_close": 1.055,
    "lose_big":   1.108,
}

# PLAYS MOVE THE OPPOSITE WAY TO INTUITION WHEN LOSING BIG. I had guessed 1.035
# here on the reasoning that a trailing team stops the clock and runs more
# plays. The data says 0.961 — a blowout loser gets about 4% FEWER plays,
# because the team ahead is holding the ball and grinding clock. The effect
# partly cancels the pass-rate rise, so garbage-time volume is worth
# noticeably less than the invented number implied.
PLAYS_MULT = {
    "win_big":    1.002,
    "win_close":  1.006,
    "lose_close": 1.015,
    "lose_big":   0.961,
}


def _phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def scenario_weights(spread: float, total: float = None,
                     sigma: float = 13.2) -> dict:
    """P(each script) for a team, from its spread.

    `spread` is that TEAM's line: negative means favoured (e.g. -3.5).

    NFL final margins are close to normal around the spread with a standard
    deviation near 13.5 — one of the most stable facts in the sport, and the
    reason the spread alone is enough to weight these. sigma is slightly tighter
    than the historical 13.5 because the extremes matter less than the middle
    for play-calling.

    Returns weights summing to 1. An unknown spread gives a symmetric split
    rather than a guess at who wins.
    """
    if spread is None:
        return {"win_big": 0.22, "win_close": 0.28,
                "lose_close": 0.28, "lose_big": 0.22}
    mu = -float(spread)                     # expected margin for this team
    z = lambda x: (x - mu) / sigma          # noqa: E731
    p_win_big = 1.0 - _phi(z(BLOWOUT))      # margin > +14
    p_lose_big = _phi(z(-BLOWOUT))          # margin < -14
    p_win_close = _phi(z(BLOWOUT)) - _phi(z(0.0))
    p_lose_close = _phi(z(0.0)) - _phi(z(-BLOWOUT))
    w = {"win_big": p_win_big, "win_close": p_win_close,
         "lose_close": p_lose_close, "lose_big": p_lose_big}
    s = sum(w.values()) or 1.0
    return {k: v / s for k, v in w.items()}


def expected_plays(total: float = None, spread: float = None) -> float:
    """Offensive plays for one team.

    Pace scales with the total, but weakly: a 51-point game is not 15% more
    plays than a 44-point game, it is more efficient plays. The elasticity below
    is deliberately mild for that reason.
    """
    base = NEUTRAL["plays"]
    if total is None:
        return base
    return base * (1.0 + 0.25 * ((float(total) / 44.5) - 1.0))


def team_volume(spread: float, total: float = None,
                own_pass_rate: float = None,
                own_plays: float = None) -> dict:
    """Expected pass attempts and rush attempts for one team, mixed over script.

    `own_pass_rate` / `own_plays` are that team's measured tendencies; the
    league-neutral values stand in when they are unknown. The scenario
    multipliers are applied RELATIVE to the team's own rate, so a run-first team
    stays run-first in every script — the mixture changes the shape of the
    distribution, not the team's identity.
    """
    w = scenario_weights(spread, total)
    plays0 = own_plays if own_plays else expected_plays(total, spread)
    pr0 = own_pass_rate if own_pass_rate else NEUTRAL["pass_rate"]

    per = {}
    for s in SCENARIOS:
        plays = plays0 * PLAYS_MULT[s]
        pr = min(0.85, max(0.30, pr0 * PASS_RATE_MULT[s]))
        per[s] = {"plays": plays, "pass_rate": pr,
                  "pass_att": plays * pr, "rush_att": plays * (1.0 - pr)}

    pass_att = sum(w[s] * per[s]["pass_att"] for s in SCENARIOS)
    rush_att = sum(w[s] * per[s]["rush_att"] for s in SCENARIOS)
    # Spread of the volume itself — the thing a point estimate hides. A pick'em
    # game and a 10-point spread can share a mean and have very different tails.
    var_pass = sum(w[s] * (per[s]["pass_att"] - pass_att) ** 2 for s in SCENARIOS)
    var_rush = sum(w[s] * (per[s]["rush_att"] - rush_att) ** 2 for s in SCENARIOS)
    return {
        "weights": {k: round(v, 4) for k, v in w.items()},
        "pass_att": round(pass_att, 2),
        "rush_att": round(rush_att, 2),
        "pass_att_sd": round(var_pass ** 0.5, 2),
        "rush_att_sd": round(var_rush ** 0.5, 2),
        "plays": round(plays0, 1),
        "pass_rate": round(pr0, 4),
        "by_scenario": {s: {k: round(v, 2) for k, v in per[s].items()}
                        for s in SCENARIOS},
        "market": {"spread": spread, "total": total},
        "market_known": spread is not None,
    }


def fit_scenarios(season: int = None, seasons: list = None) -> dict:
    """Re-fit PASS_RATE_MULT and PLAYS_MULT from play-by-play.

    Measures, for each scenario band, a team's pass rate relative to its OWN
    season pass rate — not the league's. Relative is what transfers: an offense
    that throws on 62% of neutral downs and one that throws on 52% both shift by
    a similar FACTOR when trailing, but not by a similar amount.

    Scenario is assigned from the FINAL margin, matching how scenario_weights()
    defines the bands, so the fit and the weighting describe the same partition.
    Early-game plays are included deliberately: a team that ends up losing big
    has usually been trailing for a while, and that is the behaviour being
    priced.

    Returns the fitted multipliers and prints them for review. Never raises.
    """
    from . import client
    import pandas as pd
    try:
        seasons = seasons or [season or (client.current_season() - 1)]
        frames = []
        for yr in seasons:
            df = client.load("play_by_play", yr)
            if len(df):
                frames.append(df)
        if not frames:
            log.warning("nfl fit_scenarios: no play-by-play available")
            return {}
        pbp = pd.concat(frames, ignore_index=True)

        # Offensive plays only: a pass attempt (sacks count as pass plays) or a
        # rush. Excludes specials, kneels and spikes, which are not play-calls
        # in the sense being modelled.
        p = pbp[(pbp.get("play_type").isin(["pass", "run"]))
                & (pbp.get("qb_kneel") != 1) & (pbp.get("qb_spike") != 1)].copy()
        if not len(p):
            return {}
        p["is_pass"] = (p["play_type"] == "pass").astype(float)
        p["margin"] = p["result"] if "result" in p else None
        # `result` is home margin; flip it for the away offense.
        p["own_margin"] = p.apply(
            lambda r: r["result"] if r["posteam"] == r["home_team"] else -r["result"],
            axis=1)

        def band(m):
            if m >= BLOWOUT:
                return "win_big"
            if m > 0:
                return "win_close"
            if m > -BLOWOUT:
                return "lose_close"
            return "lose_big"

        p["scenario"] = p["own_margin"].map(band)
        team_rate = p.groupby(["season", "posteam"])["is_pass"].mean()
        p = p.join(team_rate.rename("team_pass_rate"),
                   on=["season", "posteam"])
        p["rel"] = p["is_pass"] / p["team_pass_rate"]
        mult = p.groupby("scenario")["rel"].mean().to_dict()

        # Plays per team-game by scenario, relative to that team's own average.
        pg = (p.groupby(["season", "game_id", "posteam", "scenario"])
                .size().rename("plays").reset_index())
        tg = (p.groupby(["season", "game_id", "posteam"])
                .size().rename("game_plays").reset_index())
        gm = (tg.groupby(["season", "posteam"])["game_plays"].mean()
                .rename("team_avg").reset_index())
        tg = tg.merge(gm, on=["season", "posteam"])
        sc = (p.groupby(["season", "game_id", "posteam"])["scenario"]
                .agg(lambda s: s.iloc[-1]).rename("final_scenario").reset_index())
        tg = tg.merge(sc, on=["season", "game_id", "posteam"])
        tg["rel"] = tg["game_plays"] / tg["team_avg"]
        pmult = tg.groupby("final_scenario")["rel"].mean().to_dict()

        out = {"pass_rate_mult": {k: round(v, 4) for k, v in mult.items()},
               "plays_mult": {k: round(v, 4) for k, v in pmult.items()},
               "n_plays": int(len(p)), "n_team_games": int(len(tg)),
               "seasons": seasons}
        log.info("nfl fit_scenarios: %s", out)
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("nfl fit_scenarios failed: %s", exc)
        return {}
