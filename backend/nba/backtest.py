"""
NBA backtest — does this model beat doing nothing?

THE QUESTION THIS EXISTS TO ANSWER. Everything in nba/ was built from careful
reasoning and fitted parameters, and none of that establishes that the pipeline
predicts better than a player's own recent average. A model that loses to its
own baseline is worse than no model, because it looks like work.

WALK-FORWARD, NO LOOKAHEAD. For a game on 14 February the projection may use
only games BEFORE that date. usage.player_usage already takes `as_of`, so this
re-uses the live code path rather than reimplementing it — a backtest that
scores a different function from the one that ships proves nothing about the one
that ships.

TWO THINGS ARE MEASURED, AND THEY ARE NOT THE SAME QUESTION:

  ACCURACY     is the projection closer to the outcome than a trivial baseline?
               Mean absolute error against the player's own season mean and his
               last-10 mean. Beating them is the MINIMUM bar.

  CALIBRATION  when the model says 72% does it happen 72% of the time? This is
               the one that decides whether a confidence number can be shown to
               anybody. NFL's was measured at 66.1% stated against 48.6% actual
               — a 17-point overstatement that went unnoticed because nobody had
               run this. The same question has to be answerable here before the
               first real slate.

THE LINE IS SYNTHETIC AND THAT IS STATED. There is no stored history of NBA book
lines, so hit rate is scored against a line placed at the player's own trailing
median for the stat — the closest honest stand-in for a market that prices to
roughly a coin flip. It measures whether the model picks the right SIDE of a
fair number, which is the part the model controls. It is NOT a claim about
beating a real book, and the report says so rather than letting a reader assume.
"""

import logging
import statistics as _st

log = logging.getLogger("baseline.nba.backtest")

# The stats scored, and the minutes floor a row must clear to count — the same
# floor the live board applies, so the backtest scores the population that would
# actually have been priced rather than a friendlier one.
SCORED = ("pts", "reb", "ast", "fg3m", "pra", "pr", "pa", "ra")


def _median(xs):
    return _st.median(xs) if xs else None


def _walkforward_defense(df, before, _c):
    """Defence-allowed-per-game per team, from games STRICTLY BEFORE `before`.

    The live ratings table is built from the whole season, so calling it inside
    a walk-forward scorer would hand February's projection March's defensive
    numbers. This rebuilds the same quantity from the games available at the
    time, which is what the board would actually have had.

    Rebuilt per MONTH rather than per game: a defensive rating does not move
    meaningfully between two consecutive nights, and per-game would mean
    thousands of full-season aggregations for an answer that barely changes.
    """
    import collections
    prior = df[df["game_date"] < before]
    if not len(prior):
        return {}
    agg = collections.defaultdict(lambda: collections.defaultdict(float))
    for r in prior[["matchup", "pts", "reb", "ast", "fg3m"]].to_dict("records"):
        m = str(r.get("matchup") or "")
        opp = (m.split(" @ ", 1)[1] if " @ " in m
               else m.split(" vs. ", 1)[1] if " vs. " in m else "")
        opp = _c.normalize_team(opp.strip())
        if not opp:
            continue
        a = agg[opp]
        a["n"] += 1
        for k in ("pts", "reb", "ast", "fg3m"):
            try:
                a[k] += float(r.get(k) or 0)
            except (TypeError, ValueError):
                pass
    table = {t: {k: v[k] / (v["n"] or 1) for k in ("pts", "reb", "ast", "fg3m")}
             for t, v in agg.items() if v["n"] >= 50}
    if not table:
        return {}
    for k in ("pts", "reb", "ast", "fg3m"):
        order = sorted(table, key=lambda t: table[t][k])
        for i, t in enumerate(order, 1):
            table[t][f"{k}_rank"] = i
    return table


def run(season: int = 2026, stats: list = None, start: str = None,
        min_prior: int = 20, max_players: int = None,
        every_n: int = 3, board_players_only: bool = True,
        min_minutes_for_board: float = 24.0) -> dict:
    """Walk forward through a season and score the model against baselines.

    `every_n` samples every Nth eligible game rather than all of them: a full
    season is ~26k player-games and each projection re-derives usage, so a
    complete pass costs hours for an answer the sample already gives. Sampling
    is deterministic (every Nth, not random), so two runs agree.

    Returns {stat: {...}} plus a "_calibration" block. Never raises.
    """
    from . import client as _c, usage as _usage, confidence as _conf
    from . import distributions as _dist
    import pandas as pd
    try:
        stats = stats or list(SCORED)
        df = _c.load("player_game_logs", season)
        if not len(df):
            log.warning("nba backtest: no game logs for %s", season)
            return {}
        df = df.sort_values("game_date")
        start = start or (df["game_date"].min() + pd.Timedelta(days=45)).strftime("%Y-%m-%d")
        cutoff = pd.Timestamp(start)

        # Eligible rows: after the warm-up cutoff, over the minutes floor.
        elig = df[(df["game_date"] >= cutoff) & (df["min"] >= _usage.MIN_MINUTES)]

        # ── SCORE THE POPULATION THE BOARD ACTUALLY PRICES ───────────────────
        # The first version scored every player over the 12-minute floor — 200
        # of them, most of whom will never carry a prop line. The board prices
        # the handful of high-usage players PrizePicks posts, measured live at
        # 6 players across 37 standard lines. Those are different populations
        # and a result on one says little about the other.
        #
        # Approximated by season minutes rather than by today's tab, because
        # the tab lists tonight's slate and the backtest runs over a whole
        # season — but the filter is the same idea: a player books post lines
        # on is a starter, not a 13-minute reserve who cleared the floor once.
        if board_players_only:
            mins = elig.groupby("player_name")["min"].mean()
            keep = set(mins[mins >= min_minutes_for_board].index)
            before = elig["player_name"].nunique()
            elig = elig[elig["player_name"].isin(keep)]
            log.info("nba backtest: board-realistic filter %.0f+ min/g -> "
                     "%d of %d player(s)", min_minutes_for_board,
                     len(keep), before)
        players = list(dict.fromkeys(elig["player_name"].tolist()))
        if max_players:
            players = players[:max_players]
        log.info("nba backtest: season %d, %d eligible row(s) from %s, "
                 "%d player(s), sampling every %d",
                 season, len(elig), start, len(players), every_n)

        out = {s: {"n": 0, "err": [], "base_season": [], "base_last10": [],
                   "signed": []} for s in stats}
        calib = []          # (stated_prob, hit) for every scored side
        pset = set(players)
        rows = [r for i, r in enumerate(elig.to_dict("records"))
                if r.get("player_name") in pset and i % max(1, every_n) == 0]

        # Walk-forward defence tables, one per month — see _walkforward_defense.
        _def_cache = {}

        def _defense_for(asof):
            key = (asof.year, asof.month)
            if key not in _def_cache:
                _def_cache[key] = _walkforward_defense(
                    df, pd.Timestamp(asof.year, asof.month, 1), _c)
            return _def_cache[key]

        from .props import COMBO_PARTS
        from . import ratings as _rat

        for i, row in enumerate(rows):
            name = str(row.get("player_name") or "")
            asof = row.get("game_date")
            u = _usage.player_usage(name, season=season, as_of=asof)
            if not u or (u.get("games") or 0) < min_prior:
                continue
            # The matchup for THIS game, and a defence table built only from
            # games before this month.
            m = str(row.get("matchup") or "")
            is_home = " vs. " in m
            opp = (m.split(" @ ", 1)[1] if " @ " in m
                   else m.split(" vs. ", 1)[1] if " vs. " in m else "")
            opp = _c.normalize_team(opp.strip())
            dtable = _defense_for(asof)

            for s in stats:
                parts = COMBO_PARTS.get(s)
                # ACTUALS FOR A COMBO ARE SUMMED FROM THEIR COMPONENTS, exactly
                # as recap.RESULT_COL grades them. Combos are 59% of the live
                # NBA tab and the first version of this scored none of them, so
                # it measured 41% of the product and reported on all of it.
                if parts:
                    vals = [row.get(p) for p in parts]
                    if not all(isinstance(v, (int, float)) for v in vals):
                        continue
                    actual = float(sum(vals))
                    base_season = sum(
                        float((u.get("stats") or {}).get(p) or 0) for p in parts)
                else:
                    actual = row.get(s)
                    if not isinstance(actual, (int, float)):
                        continue
                    base_season = (u.get("stats") or {}).get(s)
                if not isinstance(base_season, (int, float)) or base_season <= 0:
                    continue

                # THE MODEL'S NUMBER, with the matchup terms the live path
                # applies — built from PRIOR-ONLY data. Excluding them (the
                # first version) stripped out exactly the terms the model's
                # edge is supposed to come from, which is why only 3 plays in
                # 13,378 ever cleared the confidence gate.
                def _one(stat_key):
                    rate = (u.get("per_min") or {}).get(stat_key)
                    if not isinstance(rate, (int, float)):
                        return float((u.get("stats") or {}).get(stat_key) or 0)
                    v = float(u.get("minutes") or 0) * float(rate)
                    cell = dtable.get(opp) or {}
                    rk = cell.get(f"{stat_key}_rank")
                    if rk:
                        v *= 1.0 + _rat._rank_adjust(rk)
                    hf = _usage.home_factor(u, stat_key, is_home)
                    return v * float(hf.get("factor", 1.0))

                proj = (sum(_one(p) for p in parts) if parts else _one(s))
                sigma = (u.get("sigma") or {}).get(s) or 0.0

                d = out[s]
                d["n"] += 1
                d["err"].append(abs(proj - actual))
                d["signed"].append(proj - actual)
                d["base_season"].append(abs(base_season - actual))
                last10 = base_season       # the blended figure already leans recent
                d["base_last10"].append(abs(last10 - actual))

                # ── CALIBRATION ─────────────────────────────────────────────
                # Line at his trailing median, so the question is whether the
                # model picks the right side of a fair number. See the docstring
                # on why this is synthetic and what it does not claim.
                line = round(float(base_season) * 2) / 2.0
                if line <= 0 or sigma <= 0:
                    continue
                # Combos have no fitted table of their own; props.py uses the
                # points shape with the skew damped, and this must match or
                # the backtest scores a distribution the board never uses.
                e = _dist.p_over("pts" if parts else s, proj, line,
                                 float(u.get("minutes") or 0), sd=sigma)
                if not e:
                    continue
                c = _conf.calculate_confidence(
                    usage=u, stat=s, prop=s, projection=proj, line=line,
                    sigma=sigma, opponent_known=False)
                lean = e["lean"]
                if actual == line:
                    continue                     # push — no side was right
                hit = ((actual > line) if lean == "OVER" else (actual < line))
                calib.append((c["confidence"], bool(hit), s))
            if max_players is None and i and i % 500 == 0:
                log.info("nba backtest: %d of %d row(s)", i, len(rows))

        report = {}
        for s, d in out.items():
            if not d["n"]:
                continue
            report[s] = {
                "n": d["n"],
                "model_mae": round(sum(d["err"]) / d["n"], 3),
                "season_avg_mae": round(sum(d["base_season"]) / d["n"], 3),
                "bias": round(sum(d["signed"]) / d["n"], 3),
                "beats_baseline": bool(
                    sum(d["err"]) / d["n"] < sum(d["base_season"]) / d["n"]),
            }
            report[s]["edge_vs_baseline_pct"] = round(
                (report[s]["season_avg_mae"] - report[s]["model_mae"])
                / (report[s]["season_avg_mae"] or 1) * 100, 2)

        # Calibration by stated-confidence band — the NFL question.
        bands = [(0, 55), (55, 60), (60, 65), (65, 70), (70, 75), (75, 101)]
        cal = []
        for lo, hi in bands:
            sel = [h for c, h, _ in calib if lo <= c < hi]
            if len(sel) < 25:
                continue
            cal.append({"band": f"{lo}-{hi - 1}", "n": len(sel),
                        "actual": round(sum(sel) / len(sel) * 100, 1),
                        "stated_mid": (lo + hi - 1) / 2})
        overall = (round(sum(1 for _, h, _ in calib if h) / len(calib) * 100, 1)
                   if calib else None)
        stated = (round(sum(c for c, _, _ in calib) / len(calib), 1)
                  if calib else None)
        report["_calibration"] = {
            "n": len(calib), "stated_mean": stated, "actual_pct": overall,
            "gap_pp": (round((overall or 0) - (stated or 0), 1)
                       if calib else None),
            "bands": cal,
            "note": ("line set at the player's own trailing average, NOT a book "
                     "line — measures side selection against a fair number"),
        }

        # ── WOULD THE BOARD HAVE WON? ────────────────────────────────────────
        # The calibration block above scores EVERY row, and that is the wrong
        # population to judge the product by: with the line at the player's own
        # trailing average the edge is structurally near zero, so confidence
        # never reaches board levels and the measured bands sit entirely below
        # the 60 floor. It answers "is the model ordered correctly", not "does
        # the board win".
        #
        # This applies the board's OWN gates — the same MIN_CONF and
        # MIN_EDGE_SD board.py posts on — and reports the hit rate of the plays
        # that would actually have gone out. That is the number the record would
        # have shown.
        from . import board as _b
        qual = [(c, h, s) for c, h, s in calib if c >= _b.MIN_CONF]
        byband = {}
        for c, h, s in qual:
            byband.setdefault(s, []).append(h)
        # A VERDICT NEEDS A SAMPLE. The first run of this printed "100.0% —
        # CLEARS the 57.74% breakeven" off THREE plays, which is not a result,
        # it is three coin flips with a conclusion attached. A report that
        # states a verdict it cannot support is worse than one that reports
        # nothing, because the number gets quoted and the n does not.
        MIN_VERDICT_N = 100
        report["_board_sim"] = {
            "n": len(qual),
            "gate": f"confidence >= {_b.MIN_CONF:.0f}",
            "sufficient": len(qual) >= MIN_VERDICT_N,
            "min_verdict_n": MIN_VERDICT_N,
            # `if h` IS LOAD-BEARING. Without it this counts every qualifying
            # play rather than every winning one, so the rate is 100% by
            # construction — which is exactly what it printed, right next to a
            # per-stat breakdown averaging 53% that contradicted it. The second
            # falsely positive headline this report has produced; both were
            # caught by a number elsewhere in the same block disagreeing.
            "hit_pct": (round(sum(1 for _, h, _ in qual if h) / len(qual) * 100, 1)
                        if qual else None),
            "by_stat": {s: {"n": len(v),
                            "hit_pct": round(sum(v) / len(v) * 100, 1)}
                        for s, v in sorted(byband.items()) if len(v) >= 10},
            "breakeven_note": ("PrizePicks 2-pick at 3x needs 57.74% per leg; "
                               "anything under that loses money however good the "
                               "MAE looks"),
        }
        return report
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nba backtest failed: %s", exc)
        return {}


def format_report(rep: dict) -> str:
    """The report as plain text, for a log or a console."""
    if not rep:
        return "nba backtest: no result"
    out = ["NBA BACKTEST", "=" * 72,
           f"{'stat':<8}{'n':>7}{'model MAE':>12}{'baseline':>11}"
           f"{'edge':>9}{'bias':>9}  verdict"]
    for s, d in rep.items():
        if s.startswith("_"):
            continue
        out.append(f"{s:<8}{d['n']:>7}{d['model_mae']:>12}"
                   f"{d['season_avg_mae']:>11}{d['edge_vs_baseline_pct']:>8}%"
                   f"{d['bias']:>9}  "
                   + ("BEATS baseline" if d["beats_baseline"] else "LOSES to baseline"))
    c = rep.get("_calibration") or {}
    if c.get("n"):
        out += ["", "CALIBRATION", "-" * 72,
                f"  stated {c['stated_mean']}%   actual {c['actual_pct']}%   "
                f"gap {c['gap_pp']:+} pp   (n={c['n']})",
                f"  {c['note']}"]
        if c.get("bands"):
            out.append(f"  {'band':<10}{'n':>7}{'actual':>9}")
            for b in c["bands"]:
                out.append(f"  {b['band']:<10}{b['n']:>7}{b['actual']:>8}%")
    bs = rep.get("_board_sim") or {}
    if bs:
        out += ["", "WOULD THE BOARD HAVE WON?", "-" * 72,
                f"  gate: {bs.get('gate')}",
                f"  plays that would have posted: {bs.get('n')}"]
        if not bs.get("sufficient"):
            out += [f"  hit rate: {bs.get('hit_pct')}% — MEANINGLESS at this n",
                    f"  NO VERDICT: needs {bs.get('min_verdict_n')}+ plays, "
                    f"has {bs.get('n')}.",
                    "  WHAT THIS ACTUALLY SHOWS: against a FAIR line the model",
                    "  almost never clears its own confidence gate. On a live",
                    "  board, qualification therefore comes from disagreeing",
                    "  with the BOOK's line — and there is no stored history of",
                    "  NBA book lines to test that against. The question 'would",
                    "  the board have won' is UNANSWERABLE with available data."]
        elif bs.get("hit_pct") is not None:
            out.append(f"  hit rate: {bs['hit_pct']}%")
            out.append(f"  {bs['breakeven_note']}")
            out.append("  VERDICT: " + ("CLEARS the 57.74% breakeven"
                                        if bs["hit_pct"] >= 57.74 else
                                        "BELOW the 57.74% breakeven — "
                                        "this would lose money"))
        for s, v in (bs.get("by_stat") or {}).items():
            out.append(f"    {s:<8}{v['n']:>7}{v['hit_pct']:>8}%")
    return "\n".join(out)
