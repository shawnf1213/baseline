"""
NFL recap — resolve pending picks and print the record.

    cd E:/baseline && railway run python scripts/nfl_recap.py
    railway run python scripts/nfl_recap.py --slate 2026-09-09
    railway run python scripts/nfl_recap.py --since 7 --no-resolve

PRINTS. DOES NOT POST. There is deliberately no Discord code here and no
--post flag to add one by accident: the operator asked for the recap built but
NOT wired to the track-record channel yet (2026-09-10). Wiring it later should
be a considered change, not the removal of a guard.

--backfill logs a board that was posted before the store existed, so the first
recap is not empty. It re-scans the slate and records what the board WOULD have
posted, which is honest only because the projections for a finished slate are
frozen — the inputs cannot have changed. Do not use it for a slate whose games
have not started.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slate", help="ET date YYYY-MM-DD (default: all)")
    ap.add_argument("--book", help="prizepicks | underdog")
    ap.add_argument("--since", type=int, help="only the last N days")
    ap.add_argument("--no-resolve", action="store_true",
                    help="report without grading anything first")
    ap.add_argument("--backfill", metavar="YYYY-MM-DD",
                    help="log a slate posted before the store existed")
    a = ap.parse_args()

    from nfl import recap as _r, store as _s

    if not _s.available():
        print("\n  NFL store is NOT configured (NFL_DATABASE_URL unset).\n"
              "  Nothing is being persisted, so there is no record to report.\n")
        return 2

    if a.backfill:
        from nfl import board as _b
        rows = _b.scan_board("prizepicks", day=_parse(a.backfill), window_days=0,
                             one_per_player=True)
        n = _s.log_board(rows[:8], "prizepicks", a.backfill,
                         potd_player=(rows[0]["player"] if rows else None))
        print(f"backfilled {n} pick(s) for {a.backfill}")

    if not a.no_resolve:
        res = _r.resolve(slate_date=a.slate, book=a.book)
        bits = [f"checked {res.get('checked', 0)}",
                f"graded {res.get('graded', 0)}",
                f"void {res.get('void', 0)}",
                f"pending {res.get('pending', 0)}"]
        print("resolve: " + " · ".join(bits))
        if res.get("error"):
            print("         " + res["error"])
        print()

    rec = _r.record(slate_date=a.slate, book=a.book, since_days=a.since)
    title = "NFL Recap" + (f" — {a.slate}" if a.slate else "")
    print(_r.format_recap(rec, title))
    return 0


def _parse(d: str):
    import datetime
    return datetime.date.fromisoformat(d)


if __name__ == "__main__":
    raise SystemExit(main())
