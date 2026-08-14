"""
NFL game props — the four volume props.

    Receiving Yards · Receptions · Rush Yards · Pass Yards

These are 155 of the 200 standard lines on the PrizePicks NFL board. The rest
(Rush+Rec TDs, INT, Pass TDs, Sacks) are all offered at 0.5-1.5 and are
essentially "does it happen at all" questions dominated by red-zone and pressure
luck. They need a different engine and are deliberately not here.

EVERY PROJECTION IS VOLUME x RATE, with volume coming from the game-script
mixture and rate from shrunk player usage:

    receptions      = (team pass att x target share) x catch rate
    receiving yards = targets x yards per target
    rush yards      = carries x yards per carry
    pass yards      = attempts x yards per attempt

THE DISTRIBUTION IS THE OTHER HALF OF THE JOB, and it differs per prop:

  Receptions       counts, low variance, no tail. Negative binomial. The
                   best-behaved prop on the board and the one to trust first.
  Receiving yards  FAT RIGHT TAIL — one 60-yard catch rewrites the game. A
                   normal would systematically misprice the over. Gamma, whose
                   skew is set by the same coefficient of variation measured
                   from real games.
  Rush yards       same shape, slightly tighter; breakaway runs are rarer than
                   deep catches but hit harder.
  Pass yards       closest to symmetric of the four, because it is a sum of
                   ~35 attempts. Normal is defensible here and used.

CV values are FITTED per prop from weekly game logs, not assumed — the same
discipline as the MLB per-prop dispersions, where using one shared value would
have made earned runs look twice as certain as it is.
"""

import logging
import math

log = logging.getLogger("baseline.nfl.props")

SUPPORTED = ("receptions", "receiving_yards", "rush_yards", "pass_yards")

# Coefficient of variation (sd / mean) per prop, fitted on 2023-2025 weekly logs
# for players at realistic board volume. Set by fit_dispersion().
# CV IS A FUNCTION OF VOLUME, NOT A CONSTANT — the single most important guard
# against over-inflation here. Measured on 2023-2025 weekly logs, the spread of
# every one of these props narrows sharply as volume rises:
#
#     pass yards      CV 0.741 at 20 att/g  ->  0.322 at 32.5   (2.3x)
#     receiving yds   CV 0.914 at 4 tgt/g   ->  0.522 at 10
#     receptions      CV 0.713 at 4 tgt/g   ->  0.436 at 10
#     rush yards      CV 0.845 at 8 car/g   ->  0.524 at 18
#
# A single pooled CV therefore makes LOW-volume players look far more
# predictable than they are, which is the dangerous direction: it turns a
# coin-flip on a 4.5-yard receiving line into a confident lean. It also makes
# high-volume players look less predictable than they are, throwing away real
# edge. Same pooled-vs-within error as the MLB dispersions, but much larger.
#
# Points are the measured tier midpoints; values in between are interpolated and
# the ends are clamped. Deliberately not a fitted power law — four tiers is not
# enough to justify a functional form, and interpolation cannot extrapolate into
# nonsense.
CV_BY_VOLUME = {
    "pass_yards":      [(20.0, 0.741), (27.5, 0.447), (32.5, 0.322)],
    "receiving_yards": [(4.0, 0.914), (6.0, 0.698), (8.0, 0.579), (10.0, 0.522)],
    "receptions":      [(4.0, 0.713), (6.0, 0.555), (8.0, 0.478), (10.0, 0.436)],
    "rush_yards":      [(8.0, 0.845), (12.5, 0.583), (18.0, 0.524)],
}

# Pooled fallbacks, used only when the volume driver is unavailable.
CV = {
    "receiving_yards": 0.7716,
    "rush_yards": 0.6635,
    "pass_yards": 0.4604,
    "receptions": 0.6324,
}


def cv_for(prop: str, volume: float = None) -> float:
    """Coefficient of variation for this prop at this volume.

    `volume` is the prop's own driver: pass attempts, targets or carries.
    Clamped at both ends — a 2-target receiver is not more volatile than the
    lowest measured tier in any way we have evidence for, and pretending
    otherwise would invent uncertainty as readily as understating it.
    """
    pts = CV_BY_VOLUME.get(prop)
    if not pts or not isinstance(volume, (int, float)) or volume <= 0:
        return CV.get(prop, 0.65)
    if volume <= pts[0][0]:
        return pts[0][1]
    if volume >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= volume <= x1:
            t = (volume - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return CV.get(prop, 0.65)


# Receptions are a count; the rest are yardage totals.
COUNT_PROPS = ("receptions",)


def _gamma_sf(x: float, mean: float, cv: float) -> float:
    """P(X > x) for a gamma with this mean and coefficient of variation.

    Gamma rather than normal because yardage is non-negative and right-skewed:
    a receiver's floor is zero but his ceiling is a 70-yard touchdown. Using a
    symmetric distribution overstates the under on every line above the mean.
    """
    if mean <= 0 or cv <= 0:
        return 0.0
    k = 1.0 / (cv * cv)                 # shape
    theta = mean / k                    # scale
    if x <= 0:
        return 1.0
    # Regularised lower incomplete gamma by series/continued fraction.
    a, xx = k, x / theta
    if xx < a + 1.0:
        term = 1.0 / a
        total = term
        n = a
        for _ in range(500):
            n += 1.0
            term *= xx / n
            total += term
            if abs(term) < abs(total) * 1e-12:
                break
        p = total * math.exp(-xx + a * math.log(xx) - math.lgamma(a))
        return max(0.0, min(1.0, 1.0 - p))
    # Continued fraction for the upper tail.
    tiny = 1e-300
    b = xx + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    q = math.exp(-xx + a * math.log(xx) - math.lgamma(a)) * h
    return max(0.0, min(1.0, q))


def _nb_over(mu: float, line: float, cv: float) -> dict:
    """P(over/under/push) for a count prop via negative binomial."""
    from core import odds as _odds
    var = (cv * mu) ** 2
    dispersion = max(1.0001, var / mu) if mu > 0 else 1.05
    return _odds.count_over_under(mu, line, dispersion=dispersion)


def project(player: str, prop: str, line: float = None, game: dict = None,
            season: int = None) -> dict:
    """Project one NFL game prop.

    `game` is a schedule row from client.get_schedule() — it supplies the spread
    and total the mixture needs, plus which side the player is on. Without it the
    projection still runs on league-neutral volume and SAYS SO, rather than
    silently dropping the market term.

    Returns {} when usage is too thin. Never raises.
    """
    from . import usage as _usage, volume as _vol, ratings as _rat
    try:
        if prop not in SUPPORTED:
            return {}
        u = _usage.player_usage(player, season=season)
        if not u:
            return {}

        team = (game or {}).get("player_team")
        spread = (game or {}).get("player_spread")
        total = (game or {}).get("total")
        opponent = (game or {}).get("opponent_team")
        tend = _usage.team_tendency(team, season) if team else {}

        # ── OPPONENT ADJUSTMENT — RATE ONLY ─────────────────────────────────
        # The single most important guard against over-inflation in this model.
        # The volume term already reflects the opponent, because the spread that
        # weights the script mixture is itself a statement about how good they
        # are. Multiplying volume by a defensive rating as well would count that
        # once for the market and again for the ratings. So the factor is
        # applied to the RATE — yards per target, yards per carry, completion
        # rate — and nowhere else.
        #
        # Ratings are league-relative and shrunk, so an average defence
        # multiplies by exactly 1.000 and the whole adjustment is a no-op.
        opp = (_rat.opponent_factor(opponent, prop) if opponent
               else {"factor": 1.0, "basis": "no opponent supplied"})
        of = opp.get("factor", 1.0)
        vol = _vol.team_volume(spread, total,
                               own_pass_rate=tend.get("pass_rate"),
                               own_plays=tend.get("plays_per_game"))

        # ── volume x rate ────────────────────────────────────────────────────
        # SCRIPT RATIO — how far this game's volume sits from the team's own
        # neutral baseline. Applying it to a PLAYER's measured per-game volume
        # keeps him anchored to what he actually does, instead of handing him
        # 100% of the team's snaps.
        _neutral = (vol["plays"] * vol["pass_rate"]) or 1.0
        script_ratio = vol["pass_att"] / _neutral

        if prop == "pass_yards":
            # A QB gets HIS OWN attempts scaled by the script, not the team's
            # total. Assigning the whole team's pass volume over-projected every
            # quarterback by 17 yards a game (+8%) in backtest: it silently
            # credits him with snaps taken by a backup, and with the attempts he
            # never made in games he left early.
            att = (u["pass_att_per_game"] * script_ratio
                   if u.get("pass_att_per_game") else vol["pass_att"])
            # Decomposed so the defence hits accuracy, which is what a
            # secondary actually suppresses, rather than a blended Y/A.
            comp_pct = u["completion_pct"] * of
            ypc_ = u["yards_per_completion"]
            mu = att * comp_pct * ypc_
            drivers = {"pass_attempts": round(att, 1),
                       "completion_pct": round(comp_pct, 4),
                       "yards_per_completion": ypc_,
                       "yards_per_attempt": round(comp_pct * ypc_, 3)}
        elif prop == "rush_yards":
            # A back's carries scale with his team's rush attempts, which is
            # exactly the term the mixture moves MOST — and in the opposite
            # direction to the passing props on the same team.
            share = (u["carries_per_game"] /
                     (tend.get("plays_per_game", 63.0) *
                      (1 - tend.get("pass_rate", 0.57)))) if tend else None
            share = share if share and 0 < share < 1 else (
                u["carries_per_game"] / 27.0)
            carries = vol["rush_att"] * min(0.95, max(0.0, share))
            ypc_adj = u["yards_per_carry"] * of
            mu = carries * ypc_adj
            drivers = {"carries": round(carries, 1),
                       "carry_share": round(share, 3),
                       "yards_per_carry": round(ypc_adj, 3)}
        else:
            targets = vol["pass_att"] * u["target_share"]
            if prop == "receptions":
                cr = u["catch_rate"] * of
                mu = targets * cr
                drivers = {"targets": round(targets, 1),
                           "target_share": u["target_share"],
                           "catch_rate": round(cr, 4)}
            else:
                ypt = u["yards_per_target"] * of
                mu = targets * ypt
                drivers = {"targets": round(targets, 1),
                           "target_share": u["target_share"],
                           "yards_per_target": round(ypt, 3)}

        # Volume driver for this prop — the term CV scales with.
        _vol_driver = (drivers.get("pass_attempts") or drivers.get("targets")
                       or drivers.get("carries"))
        cv = cv_for(prop, _vol_driver)
        out = {
            "sport": "nfl", "prop": prop, "player": u["player"],
            "position": u["position"], "projection": round(mu, 2),
            "sd": round(mu * cv, 2), "cv": round(cv, 4),
            "cv_volume": round(_vol_driver, 2) if _vol_driver else None,
            "games_in_window": u["games"], "window": u["window"],
            "drivers": drivers,
            "volume": {k: vol[k] for k in
                       ("pass_att", "rush_att", "pass_att_sd", "rush_att_sd",
                        "weights", "market_known")},
            "market": vol["market"],
            "opponent": opponent,
            "opponent_factor": round(of, 4),
            "opponent_basis": opp.get("basis"),
            # Stated, never implied: a projection built without a spread has no
            # script mixture behind it and is a weaker claim.
            "script_applied": vol["market_known"],
        }

        if isinstance(line, (int, float)):
            if prop in COUNT_PROPS:
                r = _nb_over(mu, line, cv)
                out.update({"line": line, "p_over": round(r["p_over"], 4),
                            "p_under": round(r["p_under"], 4),
                            "p_push": round(r["p_push"], 4), "lean": r["lean"]})
            else:
                # EMPIRICAL FIRST. The gamma is kept only as a fallback for a
                # prop or volume the table does not cover; on everything it does
                # cover it was measurably too skewed toward the under.
                from . import distributions as _dist
                e = _dist.p_over(prop, mu, float(line), _vol_driver)
                if e:
                    out.update({"line": line,
                                "p_over": round(e["p_over"], 4),
                                "p_under": round(e["p_under"], 4),
                                "lean": e["lean"],
                                "dist_basis": e["basis"]})
                else:
                    pg = _gamma_sf(float(line), mu, cv)
                    out.update({"line": line, "p_over": round(pg, 4),
                                "p_under": round(1 - pg, 4),
                                "lean": "OVER" if pg >= 0.5 else "UNDER",
                                "dist_basis": "gamma fallback"})
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl projection failed (%s %s): %s", player, prop, exc)
        return {}


def fit_dispersion(seasons: list = None, min_games: int = 8) -> dict:
    """Fit CV per prop from weekly game logs.

    Restricted to players at realistic BOARD volume — PrizePicks does not post a
    receiving line for a player averaging one target, and including those would
    measure the noise of bench players rather than the spread of the props we
    actually price.
    """
    from . import client
    import pandas as pd
    try:
        seasons = seasons or [2023, 2024, 2025]
        frames = [client.load("stats_player_week", y) for y in seasons]
        frames = [f for f in frames if len(f)]
        if not frames:
            return {}
        df = pd.concat(frames, ignore_index=True)
        df = df[df.get("season_type") == "REG"]
        out = {}
        specs = {
            "receiving_yards": ("receiving_yards", "targets", 3.0),
            "receptions": ("receptions", "targets", 3.0),
            "rush_yards": ("rushing_yards", "carries", 6.0),
            "pass_yards": ("passing_yards", "attempts", 15.0),
        }
        for prop, (col, vol_col, floor) in specs.items():
            d = df[["player_id", col, vol_col]].dropna()
            g = d.groupby("player_id").agg(n=(col, "size"),
                                           vol=(vol_col, "mean"),
                                           mean=(col, "mean"),
                                           sd=(col, "std"))
            g = g[(g["n"] >= min_games) & (g["vol"] >= floor) & (g["mean"] > 0)]
            if not len(g):
                continue
            cv = (g["sd"] / g["mean"]).median()
            out[prop] = {"cv": round(float(cv), 4), "players": int(len(g)),
                         "mean_of_means": round(float(g["mean"].mean()), 2)}
        log.info("nfl fit_dispersion: %s", out)
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("nfl fit_dispersion failed: %s", exc)
        return {}
