"""
Outcome distributions for NBA props — empirical, not assumed.

P(over) reads off a table of measured outcome ratios rather than a fitted
family, for the same reason nfl/distributions.py does: a parametric model with
the correctly fitted spread was still systematically biased toward one side, and
a uniform bias on every line is not a rounding error, it is a losing model.

THE SHAPES ON THIS BOARD ARE GENUINELY DIFFERENT FROM EACH OTHER:

  Points        the most symmetric. A sum of ~20 shot attempts, so the central
                limit theorem does most of the work. Mild right skew.
  Rebounds      right-skewed and lumpy — rebounds arrive in bunches when shots
                miss, and a player's total depends on how badly both teams shot.
  Assists       right-skewed; dependent on teammates CONVERTING, which is a
                second random variable the player does not control.
  3PM           THE ACES OF THIS BOARD. Discrete, low-count, streaky, and the
                single most swing-prone prop here. A 2.4-average shooter goes
                0-for-7 and 6-for-9 in the same week. Treated as a count, with
                the variance cap the EVR grade applies to high-variance props.
  Fantasy       a composite of everything above, so its spread compounds.

BANDED ON MINUTES, because minutes are this board's volume driver and the shape
changes sharply with them: a 12-minute player's rebound total is close to a
coin-flip between 1 and 4, while a 36-minute player's is a tight band. Same
discipline as the NFL volume bands, where a single pooled CV made low-volume
players look far more predictable than they are.

Counts (3PM) go through the negative binomial, because a discrete prop needs a
discrete distribution to handle pushes on whole-number lines correctly — and
PrizePicks posts 3PM lines at whole numbers often enough that getting the push
wrong would misprice a real share of the board.
"""

import logging
import math

log = logging.getLogger("baseline.nba.distributions")

try:
    from ._ratios import RATIO_TABLE
except Exception:  # noqa: BLE001 — the table is generated; absence is survivable
    RATIO_TABLE = {}

# Discrete props. 3PM is a count and must push correctly on whole lines.
COUNT_STATS = ("fg3m",)


def _band(stat: str, minutes: float):
    """The minutes band a projection belongs to, or the nearest one."""
    bands = RATIO_TABLE.get(stat)
    if not bands:
        return None
    if not isinstance(minutes, (int, float)):
        return bands[len(bands) // 2]
    for b in bands:
        if b["lo"] <= minutes < b["hi"]:
            return b
    # Outside every band: use the closest by midpoint rather than refusing.
    return min(bands, key=lambda b: abs(b["mid"] - minutes))


def _nb_over(mu: float, line: float, sd: float) -> dict:
    """Negative-binomial P(over/under/push) for a discrete count.

    Falls back to Poisson when the measured spread is at or below the Poisson
    floor (sd^2 <= mu), which happens for low-volume three-point shooters —
    a negative binomial cannot represent under-dispersion and silently
    returning a nonsense r is worse than using the simpler family.
    """
    try:
        mu = max(1e-6, float(mu))
        var = max(mu * 1.0001, float(sd) ** 2)
        # p, r parameterisation: mean mu, variance var
        p = mu / var
        r = mu * p / max(1e-9, (1 - p))
        # P(X = k) via the recurrence, summed to the line.
        k_max = int(math.floor(float(line)))
        if k_max < 0:
            return {"p_over": 1.0, "p_under": 0.0, "p_push": 0.0, "lean": "OVER"}
        pmf = (1 - p) ** 0 * p ** r if r > 0 else math.exp(-mu)
        # log-space for stability
        log_pmf = r * math.log(max(1e-12, p))
        cum = math.exp(log_pmf)
        at_line = cum if k_max == 0 else 0.0
        cur = log_pmf
        for k in range(1, k_max + 1):
            cur += math.log((r + k - 1) / k) + math.log(max(1e-12, 1 - p))
            term = math.exp(cur)
            cum += term
            if k == k_max:
                at_line = term
        cum = min(1.0, max(0.0, cum))
        is_whole = abs(float(line) - round(float(line))) < 1e-9
        p_push = at_line if is_whole else 0.0
        p_under = max(0.0, cum - p_push)
        p_over = max(0.0, 1.0 - cum)
        tot = p_over + p_under + p_push or 1.0
        p_over, p_under, p_push = p_over / tot, p_under / tot, p_push / tot
        return {"p_over": p_over, "p_under": p_under, "p_push": p_push,
                "lean": "OVER" if p_over >= p_under else "UNDER",
                "basis": f"negative binomial mu={mu:.2f} sd={sd:.2f}"}
    except Exception as exc:  # noqa: BLE001
        log.warning("nba _nb_over failed: %s", str(exc)[:120])
        return {"p_over": 0.5, "p_under": 0.5, "p_push": 0.0, "lean": "OVER",
                "basis": "degenerate"}


def p_over(stat: str, mu: float, line: float, minutes: float = None,
           sd: float = None) -> dict:
    """P(result > line) for one NBA stat.

    Works on the RATIO line/mu, so one table serves every player in a minutes
    band: a 12-point scorer and a 28-point scorer have the same shape, just a
    different scale.

    Returns {} when the stat has no table — the caller must then fall back
    rather than silently receive a made-up number.
    """
    if stat in COUNT_STATS:
        if not isinstance(sd, (int, float)) or sd <= 0:
            return {}
        r = _nb_over(mu, line, sd)
        r["median_ratio"] = 1.0        # a count's median sits at its mean band
        return r
    b = _band(stat, minutes)
    if not b or not mu or mu <= 0:
        return {}
    q = b["q"]                       # 1st..99th percentile of actual/mean
    ratio = float(line) / float(mu)
    n = len(q)
    if ratio <= q[0]:
        pct = 1.0
    elif ratio >= q[-1]:
        pct = 99.0
    else:
        pct = 99.0
        for i in range(n - 1):
            if q[i] <= ratio <= q[i + 1]:
                span = (q[i + 1] - q[i]) or 1e-9
                pct = (i + 1) + (ratio - q[i]) / span
                break
    po = max(0.005, min(0.995, 1.0 - pct / 100.0))
    return {
        "p_over": po, "p_under": 1.0 - po, "p_push": 0.0,
        "lean": "OVER" if po >= 0.5 else "UNDER",
        "basis": f"empirical {stat} {b['lo']}-{b['hi']} min "
                 f"({b['n_games']} games)",
        "median_ratio": q[49],
    }


def refit(seasons: list = None, min_games: int = 15) -> dict:
    """Regenerate RATIO_TABLE from real game logs.

    Returns the table; writing it to nba/_ratios.py is a deliberate manual step,
    not a side effect — the same discipline nfl/distributions.refit follows, so
    a scheduled job can never quietly change what the board is pricing against.
    """
    from . import client
    import numpy as np
    import pandas as pd
    try:
        seasons = seasons or [client.current_season() - 1,
                              client.current_season() - 2]
        frames = [client.load("player_game_logs", y) for y in seasons]
        df = pd.concat([f for f in frames if len(f)], ignore_index=True)
        if not len(df):
            log.warning("nba distributions refit: no data for %s", seasons)
            return {}
        bands = [(12, 20), (20, 28), (28, 34), (34, 48)]
        table = {}
        for stat in ("pts", "reb", "ast", "nba_fantasy_pts"):
            if stat not in df.columns:
                continue
            d = df[["player_id", stat, "min"]].dropna()
            d = d[d["min"] >= 12]
            g = d.groupby("player_id").agg(n=(stat, "size"),
                                           mins=("min", "mean"),
                                           mean=(stat, "mean"))
            g = g[(g["n"] >= min_games) & (g["mean"] > 0)]
            rows = []
            for lo, hi in bands:
                ids = g[(g["mins"] >= lo) & (g["mins"] < hi)].index
                if len(ids) < 8:
                    continue
                sub = d[d["player_id"].isin(ids)].merge(
                    g[["mean"]], on="player_id")
                r = (sub[stat] / sub["mean"]).replace(
                    [np.inf, -np.inf], np.nan).dropna().values
                if len(r) < 200:
                    continue
                rows.append({"lo": lo, "hi": hi,
                             "mid": round(float(g.loc[ids, "mins"].mean()), 2),
                             "n_games": int(len(r)),
                             "q": [round(float(np.quantile(r, p / 100)), 4)
                                   for p in range(1, 100)]})
            table[stat] = rows
        return table
    except Exception as exc:  # noqa: BLE001
        log.exception("nba distributions refit failed: %s", exc)
        return {}
