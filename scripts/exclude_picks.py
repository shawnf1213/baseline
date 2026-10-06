"""Take picks out of a published record (or put them back) — no deploy.

    railway run python scripts/exclude_picks.py nba 1 2 3 4
    railway run python scripts/exclude_picks.py nba 1 2 3 4 --undo
    railway run python scripts/exclude_picks.py tennis 812 --undo

An excluded pick stays in its table for audit but leaves the record, the
recaps and (NBA) the grader. Nothing is deleted, so --undo restores it exactly.

`railway run` (pinned to the BACKEND project/service) injects ADMIN_TOKEN into
this process; the token is never printed.
"""
import json
import os
import sys
import urllib.request

API = os.getenv("BASELINE_API_URL", "https://backend-production-84ab.up.railway.app").rstrip("/")
ROUTES = {"tennis": "/api/results/exclude", "nba": "/api/nba/results/exclude"}


def main():
    tok = (os.getenv("ADMIN_TOKEN") or "").strip()
    if not tok:
        print("ADMIN_TOKEN is not in the environment — run through `railway run` on the backend service")
        return 2
    args = sys.argv[1:]
    undo = "--undo" in args
    args = [a for a in args if a != "--undo"]
    if len(args) < 2 or args[0] not in ROUTES or not all(a.isdigit() for a in args[1:]):
        print(__doc__)
        return 2
    sport, ids = args[0], [int(a) for a in args[1:]]
    req = urllib.request.Request(f"{API}{ROUTES[sport]}",
                                 data=json.dumps({"ids": ids, "excluded": not undo}).encode(),
                                 headers={"X-Admin-Token": tok, "Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"{sport} ids={ids} excluded={not undo} ->", json.dumps(json.load(r)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
