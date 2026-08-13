"""
MLB pitcher props beyond strikeouts — outs, hits/walks/earned runs allowed,
and Fantasy Score.

All of these ride the SAME volume engine as strikeouts: a start's batters faced
(or outs) times a per-batter rate. That is why they were the cheap expansion —
mlb/strikeouts.py already solved the hard part (the rolling window, the sample
floor, the opponent term).

DISPERSIONS ARE FITTED, NOT INVENTED — 885 real starts from 56 qualified starters,
2026-08-08. They differ sharply by prop and that difference is the point:

    prop            mean     sd   var/mean
    outs           17.06   3.49     0.715    under-dispersed; start length is
                                             far more predictable than K counts
    hits allowed    4.92   2.15     0.935
    walks allowed   1.72   1.29     0.963
    earned runs     2.08   1.85     1.634    lumpy — runs cluster in innings
    strikeouts      5.65   2.58     1.177

Using one shared dispersion would have made earned runs look twice as certain as
it is and outs half as certain.

KNOWN LIMITATION, same as strikeouts: these are POOLED across pitchers, which
includes between-pitcher spread and therefore overstates the uncertainty about a
GIVEN pitcher's next start. The within-pitcher figure for strikeouts measured
0.918 against a pooled 1.177. Correcting all of them is one change and should be
made on a proper sample, not per-prop by hand.

COMPOSITE PROPS USE THE EMPIRICAL DISTRIBUTION. Fantasy Score is a weighted sum
of correlated components — more outs means more strikeouts and usually fewer
earned runs — so combining component variances as if independent would understate
its spread badly. Instead the pitcher's own per-start Fantasy Score history is
computed and used directly.
"""

import logging

from . import client, strikeouts as _so
from core import odds as _odds

log = logging.getLogger("baseline.mlb.pitcher_props")

# Fitted 2026-08-08 on 885 starts. See module docstring.
DISPERSION = {
    "pitching_outs": 0.715,
    "hits_allowed": 0.935,
    "walks_allowed": 0.963,
    "earned_runs": 1.634,
    "strikeouts": 1.105,        # from the larger 6,092-start fit
}

# League per-start means, same sample — fallbacks only.
LEAGUE = {
    "pitching_outs": 17.06,
    "hits_allowed": 4.92,
    "walks_allowed": 1.72,
    "earned_runs": 2.08,
}

# PrizePicks pitcher Fantasy Score, per the published table.
FS_WIN, FS_QS, FS_ER, FS_K, FS_OUT = 6.0, 4.0, -3.0, 3.0, 1.0

SUPPORTED = ("pitching_outs", "hits_allowed", "walks_allowed",
             "earned_runs", "pitcher_fantasy_score")


def _start_rows(pitcher_id, as_of=None):
    """The same season-window start log strikeouts.pitcher_form uses, but with
    every counting stat kept rather than only K."""
    import datetime as _dt
    as_of = as_of or _dt.date.today()

    def _starts(season):
        out = []
        d = client._get(f"/people/{pitcher_id}/stats", stats="gameLog",
                        group="pitching", season=season)
        for b in (d.get("stats") or []):
            for s in (b.get("splits") or []):
                st = s.get("stat") or {}
                if not (st.get("gamesStarted") or 0):
                    continue
                try:
                    ip = float(st.get("inningsPitched") or 0)
                except (TypeError, ValueError):
                    continue
                outs = round(ip * 3)
                if not outs:
                    continue
                out.append({
                    "date": s.get("date"),
                    "game_pk": (s.get("game") or {}).get("gamePk"),
                    "outs": outs,
                    "bf": st.get("battersFaced") or 0,
                    "k": st.get("strikeOuts") or 0,
                    "h": st.get("hits") or 0,
                    "bb": st.get("baseOnBalls") or 0,
                    "er": st.get("earnedRuns") or 0,
                    "win": 1 if (st.get("wins") or 0) else 0,
                })
        return out

    rows = _starts(as_of.year)
    if len(rows) < _so.MIN_STARTS:
        rows = rows + _starts(as_of.year - 1)      # same early-season fallback
    return rows


def _fs_of(row: dict) -> float:
    """PrizePicks pitcher Fantasy Score for one completed start."""
    qs = 1.0 if (row["outs"] >= 18 and row["er"] <= 3) else 0.0
    return (FS_WIN * row["win"] + FS_QS * qs + FS_ER * row["er"]
            + FS_K * row["k"] + FS_OUT * row["outs"])


def project(pitcher_id, prop: str, opponent_team_id=None, line=None,
            as_of=None, **kw) -> dict:
    """Project one pitcher prop. {} when unsupported or the sample is too thin.

    Volume-driven props (hits/walks/earned runs) scale the pitcher's own per-BF
    rate by his expected batters faced, so they inherit the start-length risk that
    dominates every pitcher prop — the thing that broke the 8/7 strikeout board.

    Fantasy Score uses the EMPIRICAL per-start distribution rather than combining
    component variances, because the components are correlated.
    """
    try:
        if prop not in SUPPORTED:
            return {}
        rows = _start_rows(pitcher_id, as_of)
        if len(rows) < _so.MIN_STARTS:
            log.info("mlb %s: pitcher %s has %d starts (< %d) — no projection",
                     prop, pitcher_id, len(rows), _so.MIN_STARTS)
            return {}
        n = len(rows)
        bf = sum(r["bf"] for r in rows) or 1
        exp_bf = bf / n

        out = {"sport": "mlb", "prop": prop, "pitcher_id": pitcher_id,
               "starts_in_window": n, "expected_bf": round(exp_bf, 1)}

        if prop == "pitcher_fantasy_score":
            # Empirical: correlated components make an independence assumption
            # understate the spread.
            vals = [_fs_of(r) for r in rows]
            mu = sum(vals) / n
            var = sum((v - mu) ** 2 for v in vals) / n
            sd = var ** 0.5
            out.update({
                "projection": round(mu, 2), "sd": round(sd, 2),
                "qs_rate": round(sum(1 for r in rows
                                     if r["outs"] >= 18 and r["er"] <= 3) / n, 3),
                "win_rate": round(sum(r["win"] for r in rows) / n, 3),
                "model": "empirical per-start Fantasy Score (components correlated)",
            })
            if isinstance(line, (int, float)) and sd > 0:
                # Continuous-ish composite -> normal is appropriate here, unlike
                # the count props.
                import math
                z = (line - mu) / sd
                p_over = 0.5 * math.erfc(z / (2 ** 0.5))
                out.update({"line": line,
                            "p_over": round(p_over, 4),
                            "p_under": round(1 - p_over, 4),
                            "lean": "OVER" if p_over >= 0.5 else "UNDER"})
            return out

        field = {"pitching_outs": "outs", "hits_allowed": "h",
                 "walks_allowed": "bb", "earned_runs": "er"}[prop]
        total = sum(r[field] for r in rows)
        per_start = total / n
        out["projection"] = round(per_start, 2)
        out["per_bf_rate"] = round(total / bf, 4)
        out["model"] = f"negative-binomial, dispersion {DISPERSION[prop]}"
        if isinstance(line, (int, float)):
            r = _odds.count_over_under(per_start, line,
                                       dispersion=DISPERSION[prop])
            out.update({"line": line,
                        "p_over": round(r["p_over"], 4),
                        "p_under": round(r["p_under"], 4),
                        "p_push": round(r["p_push"], 4),
                        "lean": r["lean"],
                        "dispersion": DISPERSION[prop]})
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("mlb pitcher prop %s failed (%s): %s", prop, pitcher_id, exc)
        return {}
