"""
Outcome distributions for NFL props — empirical, not assumed.

P(over) reads off a table of measured outcome ratios rather than a fitted
family. The reason is a calibration check that a parametric model failed:

    prop / volume band     actual P(X > mean)     gamma model said
    receiving yds  4-7           42.9%                 39.6%
    rush yards     8-13          43.5%                 40.5%
    pass yards    25-31          51.8%                 44.4%

A gamma with the correctly fitted CV was still 3pp too skewed on receiving and
rushing yards and 7pp too skewed on pass yards. A uniform bias toward the UNDER
on every line is not a rounding error, it is a losing model — and pass yards
turn out to be almost exactly symmetric, which no right-skewed family
reproduces.

So the shape is taken from the data: for each prop and volume band, the
percentiles of (actual / that player's own mean) across 2023-2025. The mean
still comes from the projection; only the SHAPE around it comes from here.

Counts (receptions) still go through the negative binomial, because a discrete
prop needs a discrete distribution to handle pushes on whole-number lines
correctly.
"""

import logging

from ._ratios import RATIO_TABLE

log = logging.getLogger("baseline.nfl.distributions")


def _band(prop: str, volume: float):
    """The volume band a projection belongs to, or the nearest one."""
    bands = RATIO_TABLE.get(prop)
    if not bands:
        return None
    if not isinstance(volume, (int, float)):
        return bands[len(bands) // 2]
    for b in bands:
        if b["lo"] <= volume < b["hi"]:
            return b
    # Outside every band: use the closest by midpoint rather than refusing.
    return min(bands, key=lambda b: abs(b["mid"] - volume))


def p_over(prop: str, mu: float, line: float, volume: float = None) -> dict:
    """P(result > line) for a continuous NFL prop.

    Works on the RATIO line/mu, so one table serves every player at that volume:
    a 40-yard receiver and a 90-yard receiver have the same shape, just a
    different scale.

    Returns {} when the prop has no table — the caller must then fall back
    rather than silently receive a made-up number.
    """
    b = _band(prop, volume)
    if not b or not mu or mu <= 0:
        return {}
    q = b["q"]                       # 1st..99th percentile of actual/mean
    ratio = float(line) / float(mu)
    n = len(q)
    # Percentile position of `ratio` within the measured distribution.
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
        "p_over": po, "p_under": 1.0 - po,
        "lean": "OVER" if po >= 0.5 else "UNDER",
        "basis": f"empirical {prop} vol {b['lo']}-{b['hi']} "
                 f"({b['n_games']} games)",
        "median_ratio": q[49],
    }


def refit(seasons: list = None, min_games: int = 8) -> dict:
    """Regenerate RATIO_TABLE from weekly logs. Returns the table; writing it to
    nfl/_ratios.py is a deliberate manual step, not a side effect."""
    from . import client
    import pandas as pd
    import numpy as np
    try:
        seasons = seasons or [2023, 2024, 2025]
        frames = [client.load("stats_player_week", y) for y in seasons]
        df = pd.concat([f for f in frames if len(f)], ignore_index=True)
        df = df[df.get("season_type") == "REG"]
        spec = {
            "receiving_yards": ("receiving_yards", "targets", [(3, 6), (6, 9), (9, 99)]),
            "receptions": ("receptions", "targets", [(3, 6), (6, 9), (9, 99)]),
            "rush_yards": ("rushing_yards", "carries", [(6, 11), (11, 16), (16, 99)]),
            "pass_yards": ("passing_yards", "attempts", [(15, 27), (27, 33), (33, 99)]),
        }
        table = {}
        for prop, (col, volcol, bands) in spec.items():
            d = df[["player_id", col, volcol]].dropna()
            g = d.groupby("player_id").agg(n=(col, "size"), vol=(volcol, "mean"),
                                           mean=(col, "mean"))
            g = g[(g["n"] >= min_games) & (g["mean"] > 0)]
            rows = []
            for lo, hi in bands:
                ids = g[(g["vol"] >= lo) & (g["vol"] < hi)].index
                if len(ids) < 8:
                    continue
                sub = d[d["player_id"].isin(ids)].merge(g[["mean"]], on="player_id")
                r = (sub[col] / sub["mean"]).replace(
                    [np.inf, -np.inf], np.nan).dropna().values
                rows.append({"lo": lo, "hi": hi,
                             "mid": round(float(g.loc[ids, "vol"].mean()), 2),
                             "n_games": int(len(r)),
                             "q": [round(float(np.quantile(r, p / 100)), 4)
                                   for p in range(1, 100)]})
            table[prop] = rows
        return table
    except Exception as exc:  # noqa: BLE001
        log.exception("nfl distributions refit failed: %s", exc)
        return {}
