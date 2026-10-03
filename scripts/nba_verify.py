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


def trace(player, prop, line, team, opponent, why):
    from nba import props as P
    game = {"player_team": team, "opponent_team": opponent,
            "home_abbr": team, "tipoff": AS_OF}
    r = P.project(player, prop, line=line, game=game, season=SEASON,
                  as_of=AS_OF, inj={})
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

    # ── REPRODUCIBILITY ──────────────────────────────────────────────────────
    # The same case twice must produce an identical number. Everything is served
    # from the cached frame, so a second run that disagreed would mean a fetch
    # race was deciding the projection.
    print(f"\n{'=' * 78}\nREPRODUCIBILITY — same inputs, second run\n{'=' * 78}")
    from nba import props as P
    ok = True
    for (player, prop), r0 in first.items():
        game = {"player_team": r0.get("team"), "opponent_team": r0.get("opponent"),
                "home_abbr": r0.get("team"), "tipoff": AS_OF}
        r1 = P.project(player, prop, line=r0.get("line"), game=game,
                       season=SEASON, as_of=AS_OF, inj={})
        same = (r1.get("projection") == r0.get("projection")
                and r1.get("confidence") == r0.get("confidence")
                and r1.get("sd") == r0.get("sd"))
        ok = ok and same
        print(f"  {a(player):<20} {prop:<5} "
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
