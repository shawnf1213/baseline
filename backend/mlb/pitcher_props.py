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
                    # WHO he faced. Every split carries it and nothing read it,
                    # which is why a rate built on weak lineups looked identical
                    # to one earned against real offences.
                    "opponent_id": (s.get("opponent") or {}).get("id"),
                    "is_home": bool(s.get("isHome")),
                    "outs": outs,
                    "bf": st.get("battersFaced") or 0,
                    "k": st.get("strikeOuts") or 0,
                    "h": st.get("hits") or 0,
                    "bb": st.get("baseOnBalls") or 0,
                    "er": st.get("earnedRuns") or 0,
                    # HR allowed — needed by the batter engine's home-run
                    # matchup term, which silently fell back to 1.0 without it.
                    "hr": st.get("homeRuns") or 0,
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
            as_of=None, is_home: bool = None, home_team_id=None,
            **kw) -> dict:
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
        # Same opener gate as strikeouts — outs, hits and earned runs allowed all
        # scale with batters faced, so a one-inning opener breaks every one of
        # them in the same way.
        _form = _so.pitcher_form(pitcher_id, as_of=as_of)
        if _form.get("opener_risk"):
            return {"skipped": True, "sport": "mlb", "prop": prop,
                    "pitcher_id": pitcher_id, "opener_risk": True,
                    "reason": _form.get("role_note")}
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
                p_over = _odds.normal_sf(line, mu, sd)
                out.update({"line": line,
                            "p_over": round(p_over, 4),
                            "p_under": round(1 - p_over, 4),
                            "lean": "OVER" if p_over >= 0.5 else "UNDER"})
            return out

        field = {"pitching_outs": "outs", "hits_allowed": "h",
                 "walks_allowed": "bb", "earned_runs": "er"}[prop]
        total = sum(r[field] for r in rows)
        per_start = total / n
        per_bf = total / bf

        # ── OPPONENT ADJUSTMENT ──────────────────────────────────────────────
        # These props had NO opponent term at all: project() accepted
        # opponent_team_id and never read it, so earned runs, hits, walks and
        # outs were pure season averages. A pitcher faced the best offence in
        # baseball and the worst with the same projection, on props that led
        # recent boards.
        #
        # Built as BF x per-BF rate x opponent, the same shape strikeouts uses,
        # so the two engines cannot disagree about what a matchup is worth.
        opp_field = {"hits_allowed": "hit_rate", "walks_allowed": "bb_rate",
                     "earned_runs": "run_rate", "pitching_outs": "run_rate"}[prop]
        tb = kw.get("team_batting")
        if tb is None:
            tb = client.get_team_batting()
        lg = client.league_batting(tb) if tb else {}
        opp = (tb or {}).get(opponent_team_id) or {}
        opp_rate, lg_rate = opp.get(opp_field), lg.get(opp_field)
        opp_factor, basis = 1.0, "none (opponent unknown)"
        if (isinstance(opp_rate, (int, float)) and isinstance(lg_rate, (int, float))
                and 0 < lg_rate < 1 and 0 < opp_rate < 1):
            if prop == "pitching_outs":
                # OUTS RUN THE OTHER WAY. A better offence does not lengthen a
                # start, it shortens it — more baserunners, higher pitch count,
                # an earlier hook. So the ratio is INVERTED here, and getting
                # that sign wrong would have made every start against a good
                # lineup look longer.
                # Damped square root, not the raw ratio: start length responds
                # to opponent quality but far less than one-for-one, because a
                # manager's hook is driven by pitch count and leverage, not only
                # by how good the lineup is.
                opp_factor = (lg_rate / opp_rate) ** 0.5
                basis = f"inverse opp {opp_field} (better offence = shorter start)"
            else:
                # log5 on the two rates against the league baseline — the same
                # parameter-free identity the strikeout model uses.
                opp_factor = _odds.log5(per_bf, opp_rate, lg_rate) / per_bf                     if per_bf > 0 else 1.0
                basis = f"log5 vs opp {opp_field}"
        # Bounded. An unclipped ratio on a small denominator eventually produces
        # a number no offence in baseball justifies.
        opp_factor = max(0.80, min(1.25, opp_factor))

        # ── PARK, PER METRIC ────────────────────────────────────────────────
        # A strikeout park is not a run park, so the factor is fetched for the
        # metric this prop actually is. Walks are left alone: parks move balls
        # in play, not the strike zone.
        from . import context as _ctx
        park, park_basis = 1.0, "none"
        _pm = {"earned_runs": "runs", "hits_allowed": "hits",
               "pitching_outs": "runs"}.get(prop)
        venue = home_team_id if home_team_id is not None else (
            opponent_team_id if is_home is False else None)
        if _pm and venue is not None:
            pf = _ctx.park_factors(metric=_pm)
            if venue in pf:
                park = pf[venue]
                if prop == "pitching_outs":
                    park = 1.0 / park      # a run-friendly park shortens starts
                park_basis = f"{_pm} park {pf[venue]:.3f}"
        park = max(0.88, min(1.14, park))

        # ── STRENGTH OF SCHEDULE — what the baseline was earned AGAINST ─────
        # The opponent term above prices the NEXT start. This corrects the
        # BASELINE for the teams already faced: a rate built on weak lineups
        # overstates the pitcher, and pricing a good matchup on an inflated
        # baseline gets the level wrong however good the matchup term is.
        #
        # MEASURED SMALL, and honestly so. Across 142 current starters the
        # factor spans 0.985-1.013 on hits (ZERO pitchers moved 2%), 0.973-1.032
        # on strikeouts and 0.960-1.038 on earned runs (21 moved 2%). MLB
        # schedules are balanced by design — over fourteen starts everyone faces
        # a similar mix — so this is nothing like the tennis draw effect, where
        # one player meets qualifiers and another meets seeds. It is applied
        # because it is real and self-neutralises to 1.000 where it is not.
        from . import strength as _str
        sched = _str.schedule_factor(rows, prop, tb, lg)
        sf = sched.get("factor", 1.0) or 1.0
        out["schedule_factor"] = round(sf, 4)
        out["schedule_basis"] = sched.get("basis")

        # Combined clip, same reasoning as the batter side: opponent and park
        # are each bounded, but their product reaches 0.70-1.43 and no matchup
        # plus venue justifies that on its own.
        # Divided, not multiplied: an EASY schedule (factor < 1) means the raw
        # rate flatters him, so the baseline must rise.
        _combined = (opp_factor * park) / sf
        _clipped = max(0.78, min(1.28, _combined))
        out["context_clipped"] = abs(_clipped - _combined) > 1e-9
        out["context_factor"] = round(_clipped, 4)
        out["projection"] = round(per_start * _clipped, 2)
        out["park_factor"] = round(park, 4)
        out["park_basis"] = park_basis
        out["unadjusted_projection"] = round(per_start, 2)
        out["per_bf_rate"] = round(per_bf, 4)
        out["opponent_team_id"] = opponent_team_id
        out["opponent_factor"] = round(opp_factor, 4)
        out["opponent_basis"] = basis
        out["opponent_rate"] = round(opp_rate, 4) if isinstance(opp_rate, (int, float)) else None
        out["league_rate"] = round(lg_rate, 4) if isinstance(lg_rate, (int, float)) else None
        out["model"] = f"negative-binomial, dispersion {DISPERSION[prop]}"
        if isinstance(line, (int, float)):
            r = _odds.count_over_under(out["projection"], line,
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
