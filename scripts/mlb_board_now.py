#!/usr/bin/env python
"""Run an MLB board on demand — scan, show, and optionally post.

WHY THIS EXISTS
---------------
The tennis board is triggered by env-var one-shots on Railway (POD_POST_ON_START,
POD_EXTRA_RUN_DATE/HOUR/MINUTE), which means a deploy per run. MLB_RUN_NOW gives
MLB the same capability, but a redeploy is a slow way to look at a board, and it
posts before you have seen it.

This script does the same work in-process. It DEFAULTS TO DRY RUN: it prints the
board and posts nothing. Posting requires --post, because a board that reaches a
channel is a thing people act on and should never be one flag-typo away.

CREDENTIALS COME FROM THE ENVIRONMENT, NEVER FROM ARGUMENTS. Passing a bot token
on a command line leaks it into shell history and the process table. Run it under
`railway run`, which injects the service's real variables into a local process:

    railway login                       # once; OAuth, opens a browser
    railway link                        # pick the BOT service
    railway run python scripts/mlb_board_now.py --post

Without --post no credentials are needed at all, so the dry run works anywhere.

SLATE SELECTION rolls to tomorrow when every game today has already started.
Both books put the next day's lines up late in the evening, so running this at
11pm means "board the slate that just went up", not "board fifteen final games".
Pass --date to override.
"""

import argparse
import datetime as dt
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ET = dt.timezone(dt.timedelta(hours=-4))          # America/New_York, MLB's day


def target_slate(explicit=None) -> str:
    """The slate to board. See the module docstring on why this rolls forward."""
    from mlb import client
    if explicit:
        return explicit
    now = dt.datetime.now(ET)
    today = now.strftime("%Y-%m-%d")
    games = client.get_schedule(today)
    if games and not any(g.get("abstract_state") in (None, "Preview")
                         for g in games):
        tomorrow = (now + dt.timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"  all {len(games)} game(s) on {today} have started "
              f"-> boarding {tomorrow}")
        return tomorrow
    return today


def show(rows: list, book: str) -> None:
    """Print exactly what would post, in board order."""
    from mlb import post
    keep = post.postable(rows)
    withheld = len(rows) - len(keep)
    print(f"\n### {book.upper()}  projected={len(rows)}  "
          f"on board={len(keep)}" + (f"  (deduped {withheld})" if withheld else ""))
    if not keep:
        print("    (nothing priced)")
        return
    for i, r in enumerate(keep, 1):
        who = r.get("player") or r.get("pitcher")
        side = r.get("p_over") if r.get("lean") == "OVER" else r.get("p_under")
        edge = r.get("edge_vs_market")
        e = f"  edge {edge * 100:+5.1f}pp" if isinstance(edge, (int, float)) else ""
        star = "*" if i == 1 else " "
        print(f"  {star}{i:2}. {who:22} {str(r.get('prop')):18} "
              f"{str(r.get('lean')):<5} {r.get('line'):>5}  "
              f"proj {r.get('projection'):>6.2f}  {(side or 0) * 100:5.1f}%{e}"
              f"  vs {r.get('opponent')}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--book", default="both",
                   choices=["both", "prizepicks", "underdog"])
    p.add_argument("--date", default=None, help="YYYY-MM-DD (default: auto)")
    p.add_argument("--only", default=None, choices=["pitcher", "batter"],
                   help="restrict the prop family (default: everything)")
    p.add_argument("--post", action="store_true",
                   help="actually post to the MLB channels (default: dry run)")
    p.add_argument("--store-only", action="store_true", dest="store_only",
                   help="persist the board WITHOUT posting. For repairing a run "
                        "that posted but could not reach the database — an "
                        "unstored board has nothing to grade and produces no "
                        "recap. Re-scans, so lines may have moved slightly "
                        "since the post; that is reported.")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()

    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING,
                        format="%(levelname)s | %(message)s")

    from mlb import board
    books = ["prizepicks", "underdog"] if a.book == "both" else [a.book]
    slate = target_slate(a.date)
    mode = "POST" if a.post else "dry run"
    print(f"MLB board — slate {slate} · {mode} · "
          f"{a.only or 'all props'} · {dt.datetime.now(ET):%H:%M ET}")

    if a.post and not os.getenv("DISCORD_BOT_TOKEN"):
        print("\n  REFUSING TO POST: DISCORD_BOT_TOKEN is not set.\n"
              "  Run under `railway run` so the service's own variables are\n"
              "  injected, rather than putting a token on the command line.",
              file=sys.stderr)
        return 2

    failures = 0
    for bk in books:
        if a.store_only:
            from mlb import post as _post, store as _store
            if not _store.available():
                print(f"\n  {bk}: STORE UNAVAILABLE — check MLB_DATABASE_URL "
                      f"and that SQLAlchemy is installed", file=sys.stderr)
                failures += 1
                continue
            rows = board.scan_all_props(slate, book=bk, only=a.only)
            rows.sort(key=lambda r: -((r.get("p_over") if r.get("lean") == "OVER"
                                       else r.get("p_under")) or 0))
            keep = _post.postable(rows)
            potd = _post.select_potd(keep)
            n = _store.log_board(
                keep, bk, slate,
                potd_key=((potd.get("player") or potd.get("pitcher"),
                           potd.get("prop")) if potd else None),
                shadow=True)
            print(f"\n### {bk.upper()}  stored={n} of {len(keep)} board rows "
                  f"(not posted)")
            continue
        if a.post:
            # run_daily scans, posts, then persists — and only persists after a
            # successful send, so an unposted board is never recorded as one.
            res = board.run_daily(bk, slate, True, True, a.only)
            ok = res.get("posted")
            print(f"\n### {bk.upper()}  projected={res.get('projections')} "
                  f"priced={res.get('priced')} posted={ok} "
                  f"stored={res.get('stored')} props={res.get('props')}")
            if not ok:
                failures += 1
                print(f"    NOT POSTED: {res.get('post_reason')}", file=sys.stderr)
        else:
            rows = board.scan_all_props(slate, book=bk, only=a.only)
            rows.sort(key=lambda r: -((r.get("p_over") if r.get("lean") == "OVER"
                                       else r.get("p_under")) or 0))
            show(rows, bk)

    if not (a.post or a.store_only):
        print("\n  dry run — nothing posted, nothing stored. Add --post to send.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
