"""
NBA pipeline verification — the traces STEP 9 asks for, as a runnable script.

    python scripts/nba_verify.py

Three archetypes through the full chain with component-level traces, a
reproducibility check, and an isolation check. Read-only: fetches nothing it has
not already cached, posts nothing, writes nothing to the record.

WHY as_of MATTERS HERE. Run against today's date in the offseason, every player
is 170+ days inactive and the availability penalty (-22) dominates every score,
so the trace shows the gate firing and nothing else. Passing as_of puts the
model back inside the season, which is the state the board will actually run in
— and it is the same mechanism the backtest uses, so verifying through it
verifies the path that will be measured later.
"""

import os
import sys
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "backend"))

AS_OF = "2026-03-01"          # mid-season, so the inactivity gate is quiet
SEASON = 2026


def a(s):
    return unicodedata.normalize("NFKD", str(s or "")).encode(
        "ascii", "replace").decode()


def trace(player, prop, line, team, opponent, why, home=True, position=None):
    from nba import props as P
    game = {"player_team": team, "opponent_team": opponent,
            "home_abbr": team if home else opponent,
            "away_abbr": opponent if home else team, "tipoff": AS_OF}
    r = P.project(player, prop, line=line, game=game, season=SEASON,
                  as_of=AS_OF, inj={}, position=position)
    print(f"\n{'=' * 78}\n{a(player)} — {why}\n{'=' * 78}")
    if not r:
        print("  NO USAGE — not priced")
        return None
    if r.get("skipped"):
        print(f"  SKIPPED — {a(r.get('reason'))}")
        return None
    print(f"  {r['label']} line {line}")
    print(f"    minutes      {r['minutes']}  (cv {r['minutes_cv']}, "
          f"{r['rotation']})")
    print(f"    pace         x{r['pace_factor']}   {a(r['pace_basis'])}")
    for st, d in (r.get("drivers") or {}).items():
        print(f"    {st:<12} baseline {d['baseline']:<7} "
              f"per-min {d['per_minute']:<9} opp x{d['opponent_factor']} "
              f"({a(d['opponent_basis'])})")
    if r.get("components"):
        tot = sum(r["components"].values())
        print(f"    components   {r['components']}  sum={tot:.2f}")
        assert abs(tot - r["projection"]) < 0.02, \
            "COMBO MISMATCH — components do not sum to the projection"
        print("    [OK] components sum to the projection")
    print(f"    usage vacuum x{r['usage_vacuum']}  ({a(r['usage_vacuum_basis'])})")
    print(f"    back-to-back {r['back_to_back']}  rest={r['rest']}")
    for st, d in (r.get("drivers") or {}).items():
        print(f"    def_adj      {st}: {a(d.get('opponent_basis'))} "
              f"-> x{d.get('opponent_factor')} "
              f"[{'vs position' if d.get('opponent_by_position') else 'team-level'}]")
        print(f"    home_split   {st}: {d.get('home_avg')} home / "
              f"{d.get('away_avg')} away "
              f"({d.get('home_games')}/{d.get('away_games')} games) "
              f"-> x{d.get('home_factor')}  [{a(d.get('home_used'))}]")
    print(f"    FINAL        proj={r['projection']}  fair={r.get('fair_line')}  "
          f"sd={r['sd']}  edge={r.get('edge')} ({r.get('edge_sd')} sd)")
    print(f"    lean={r.get('lean')}  p={r.get('p_over')}/{r.get('p_under')}  "
          f"conf={r.get('confidence')}  evr={r.get('evr')}->{r.get('evr_scaled')}"
          f"  ceiling={r.get('data_ceiling')}  cap={r.get('cap_tag') or '-'}")
    print(f"    coin_flip={r.get('coin_flip')}")
    print("    CONFIDENCE COMPONENTS:")
    tot = 0
    for k, v in (r.get("confidence_breakdown") or {}).items():
        tot += v["score"]
        print(f"       {k:<20} {v['score']:>+5}  {a(v['label'])}")
    print(f"       {'raw total':<20} {tot:>+5}  -> {r.get('confidence')} "
          f"after the single clamp")
    return r


def main():
    import logging
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s | %(name)s | %(message)s")

    print(f"NBA pipeline verification — season {SEASON}, as_of {AS_OF}")

    cases = [
        ("Tyrese Maxey", "pts", 24.5, "PHI", "WAS",
         "STAR, stable minutes (cv 0.13)"),
        ("Luke Kennard", "ast", 2.5, "LAL", "BOS",
         "TRADED mid-season (ATL -> LAL), variable minutes"),
        ("Keshad Johnson", "pts", 4.5, "MIA", "WAS",
         "ROTATION RISK (cv 0.87) — expected to fail the minutes floor"),
        ("Tyrese Maxey", "pra", 34.5, "PHI", "WAS",
         "COMBO — must sum to its components"),
    ]
    first = {}
    for player, prop, line, team, opp, why in cases:
        r = trace(player, prop, line, team, opp, why)
        if r:
            first[(player, prop)] = r

    # ── THE DEFENCE + VENUE CASES (operator, 2026-10-03) ────────────────────
    # Picked FROM THE DATA rather than asserted: find the softest and hardest
    # defences against this player's position for his stat, so the two cases are
    # genuinely bottom-5 and top-5 rather than two teams I guessed at.
    from nba import ratings as _R
    from nba import client as _C
    from nba import usage as _U
    print(f"\n{'=' * 78}\nDEFENCE + VENUE — picking opponents from the data\n{'=' * 78}")
    pos = (_R.player_positions(SEASON) or {}).get(_C._norm_name("Tyrese Maxey"), "G")
    dvp = _R.defense_vs_position(SEASON)
    ranked = sorted(
        ((t, c["G"]["pts_rank"]) for t, c in dvp.items()
         if "G" in c and c["G"].get("pts_rank") and (c["G"].get("n") or 0) >= _R.DVP_MIN_GAMES),
        key=lambda x: x[1])
    hardest, softest = ranked[0][0], ranked[-1][0]
    print(f"  Maxey position={pos} | hardest vs G pts = {hardest} (rank "
          f"{ranked[0][1]}) | softest = {softest} (rank {ranked[-1][1]})")

    r_soft = trace("Tyrese Maxey", "pts", 24.5, "PHI", softest,
                   f"STAR at HOME vs bottom-5 defence ({softest}) — both "
                   f"multipliers should be POSITIVE", home=True, position=pos)
    r_hard = trace("Tyrese Maxey", "pts", 24.5, "PHI", hardest,
                   f"same player AWAY vs top-5 defence ({hardest}) — both "
                   f"multipliers should be NEGATIVE", home=False, position=pos)

    # BOTH MULTIPLIERS POSITIVE needs a player whose OWN split favours home.
    # Maxey's does not — he averages more on the road — so forcing his case to
    # show two positive numbers would be fitting the test to the expectation.
    # This finds a real player with a genuine home edge instead.
    print(f"\n{'=' * 78}\nBOTH MULTIPLIERS POSITIVE — finding a real home-split "
          f"player\n{'=' * 78}")
    best = None
    for nm in sorted({r["player"] for r in []} | {
            "Jalen Brunson", "LeBron James", "Cade Cunningham", "Luke Kennard",
            "Tyrese Maxey", "Jayson Tatum"}):
        uu = _U.player_usage(nm, season=SEASON, as_of=AS_OF)
        sp = ((uu or {}).get("splits") or {}).get("pts") or {}
        h, aw = sp.get("home"), sp.get("away")
        if (isinstance(h, (int, float)) and isinstance(aw, (int, float))
                and sp.get("home_games", 0) >= _U.HOME_SPLIT_MIN_GAMES
                and sp.get("away_games", 0) >= _U.HOME_SPLIT_MIN_GAMES
                and h > aw):
            gap = h - aw
            if best is None or gap > best[1]:
                best = (nm, gap, uu)
    if best:
        nm, gap, uu = best
        ppos = (_R.player_positions(SEASON) or {}).get(_C._norm_name(nm), "G")
        r_both = trace(nm, "pts", 20.5, uu.get("team") or "PHI", softest,
                       f"HOME-SPLIT player (+{gap:.1f} pts at home) at HOME vs "
                       f"bottom-5 — BOTH multipliers positive",
                       home=True, position=ppos)
        if r_both:
            d = (r_both.get("drivers") or {}).get("pts") or {}
            df, hf2 = d.get("opponent_factor", 1.0), d.get("home_factor", 1.0)
            print(f"  def x{df}  home x{hf2}  -> both > 1.0: "
                  f"{'[OK]' if df > 1 and hf2 > 1 else '[CHECK]'}")
            first[(nm, "both-pos")] = r_both
    else:
        print("  no sampled player has a positive home split with 10+ games "
              "each side — the asymmetry is reported, not manufactured")

    print(f"\n{'=' * 78}\nSTACKING AND CLAMPS\n{'=' * 78}")
    for lbl, rr in (("home vs bottom-5", r_soft), ("away vs top-5", r_hard)):
        if not rr:
            continue
        d = (rr.get("drivers") or {}).get("pts") or {}
        df, hf = d.get("opponent_factor", 1.0), d.get("home_factor", 1.0)
        print(f"  {lbl:<18} def x{df}  home x{hf}  combined x{df * hf:.4f}"
              f"   proj={rr['projection']}")
        assert abs(df - 1.0) <= _R.DEF_TOTAL_CLAMP + 1e-9, "defence clamp breached"
        assert abs(hf - 1.0) <= _U.HOME_CLAMP + 1e-9, "home clamp breached"
    print(f"  [OK] defence within +/-{_R.DEF_TOTAL_CLAMP:.0%}, "
          f"home within +/-6% — clamped independently, never as one budget")
    if r_soft and r_hard:
        print(f"  [OK] bottom-5 home projects {r_soft['projection']} vs "
              f"top-5 away {r_hard['projection']} "
              f"({r_soft['projection'] - r_hard['projection']:+.2f} swing)")
        first[("Tyrese Maxey", "pts-soft")] = r_soft

    # ── THE THIN-SPLIT FALLBACK ─────────────────────────────────────────────
    print(f"\n{'=' * 78}\nTHIN HOME SPLIT -> LEAGUE BASELINE FALLBACK\n{'=' * 78}")
    thin = None
    for nm in ("Keshad Johnson", "Luke Kennard", "Tyrese Maxey"):
        u = _U.player_usage(nm, season=SEASON, as_of=AS_OF)
        sp = ((u or {}).get("splits") or {}).get("pts") or {}
        hg, ag = sp.get("home_games", 0), sp.get("away_games", 0)
        hf = _U.home_factor(u, "pts", True)
        # EXACT, not a substring. The first version tested `"baseline" in used`,
        # and "player split 60/40 with baseline" contains it — so every player
        # using the blend was labelled FALLBACK, directly contradicting the
        # basis line printed underneath. A trace that mislabels itself is worse
        # than no trace.
        mark = ("player split" if (hf.get("used") or "").startswith("player split")
                else "FALLBACK -> baseline")
        print(f"  {a(nm):<20} home_games={hg:<3} away_games={ag:<3} -> {mark}")
        print(f"       {a(hf.get('basis'))}  -> x{hf.get('factor')}")
        if hg < _U.HOME_SPLIT_MIN_GAMES or ag < _U.HOME_SPLIT_MIN_GAMES:
            thin = nm
    print(f"  [{'OK' if thin else 'NOTE'}] "
          + (f"{a(thin)} has under {_U.HOME_SPLIT_MIN_GAMES} games on a side and "
             f"fell back to the league baseline, logged above"
             if thin else
             "every sampled player has 10+ each side; the fallback branch is "
             "exercised by the unit case below"))
    # Exercise the fallback deterministically, so this check cannot silently
    # stop testing anything when every real player has a full season.
    synth = {"splits": {"pts": {"home": 30.0, "away": 20.0,
                                "home_games": 4, "away_games": 3}}}
    hf = _U.home_factor(synth, "pts", True)
    print(f"  synthetic 4/3-game split (30.0 home / 20.0 away): "
          f"x{hf['factor']} via {a(hf['used'])}")
    assert "baseline" in hf["used"], "thin split did NOT fall back"
    assert abs(hf["factor"] - (1 + _U.HOME_BASELINE)) < 1e-6, \
        "fallback did not use the league baseline exactly"
    print(f"  [OK] a 4/3-game split is discarded entirely — a 10-point gap on "
          f"seven games does not move the projection")

    # ── REPRODUCIBILITY ──────────────────────────────────────────────────────
    # The same case twice must produce an identical number. Everything is served
    # from the cached frame, so a second run that disagreed would mean a fetch
    # race was deciding the projection.
    print(f"\n{'=' * 78}\nREPRODUCIBILITY — same inputs, second run\n{'=' * 78}")
    from nba import props as P
    ok = True
    for (player, _label), r0 in first.items():
        # THE PROP COMES OFF THE ROW, NOT OFF THE DICT KEY. The key carries a
        # label so two cases on the same player+prop can both be stored, and the
        # first version passed that label straight in as the prop name — so
        # "pts-soft" reached project(), matched nothing, returned {}, and the
        # run reported itself non-reproducible when nothing was wrong.
        prop = r0.get("prop")
        r1 = P.project(player, prop, line=r0.get("line"),
                       game={"player_team": r0.get("team"),
                             "opponent_team": r0.get("opponent"),
                             "home_abbr": (r0.get("team") if r0.get("home")
                                           else r0.get("opponent")),
                             "away_abbr": (r0.get("opponent") if r0.get("home")
                                           else r0.get("team")),
                             "tipoff": AS_OF},
                       season=SEASON, as_of=AS_OF, inj={},
                       position=r0.get("position"))
        same = (r1.get("projection") == r0.get("projection")
                and r1.get("confidence") == r0.get("confidence")
                and r1.get("sd") == r0.get("sd"))
        ok = ok and same
        print(f"  {a(player):<20} {str(_label):<10} "
              f"proj {r0.get('projection')} -> {r1.get('projection')}  "
              f"conf {r0.get('confidence')} -> {r1.get('confidence')}  "
              f"{'[OK] identical' if same else '[FAIL] DIFFERS'}")
    print(f"\n  {'[OK] reproducible' if ok else '[FAIL] NOT reproducible'}")

    # ── ISOLATION ────────────────────────────────────────────────────────────
    # An NBA data-source failure must not be able to reach tennis or NFL. The
    # strongest cheap check is that nba/ imports nothing from either.
    print(f"\n{'=' * 78}\nISOLATION\n{'=' * 78}")
    # PARSED, NOT GREPPED. A substring scan of the source cannot tell an import
    # from a sentence about one, and the first version of this check failed on
    # confidence.py's own docstring explaining that nfl/ and mlb/ import nothing
    # from src/calculations. A check that cries wolf on a comment gets ignored,
    # and then it is not a check. ast sees only real import statements.
    import ast
    import pathlib
    bad = []
    FORBIDDEN = ("src", "nfl", "mlb")
    nba_dir = pathlib.Path(__file__).resolve().parent.parent / "backend" / "nba"
    for f in sorted(nba_dir.glob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError as exc:
            bad.append(f"{f.name}: unparseable ({exc})")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    if n.name.split(".")[0] in FORBIDDEN:
                        bad.append(f"{f.name}: import {n.name}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if node.level == 0 and root in FORBIDDEN:
                    bad.append(f"{f.name}: from {node.module} import ...")
    if bad:
        print("  [FAIL] NBA reaches into another sport:")
        for b in bad:
            print("    ", b)
    else:
        print("  [OK] nba/ imports nothing from src/, nfl/ or mlb/")

    # And that a hard NBA failure returns empty rather than raising.
    r = P.project("Definitely Not A Player", "pts", line=10.0, season=SEASON)
    print(f"  [{'OK' if r == {} else 'FAIL'}] unknown player returns {r!r} "
          f"(must be empty, never an exception)")
    return 0 if ok and not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
