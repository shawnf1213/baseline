"""
Pre-deploy smoke test: does every prop still return a projection?

WHY THIS EXISTS. On 2026-09-01 a bulk edit that added two arguments to
get_match_moneyline_prob also matched the get_match_total_games_line call, which
takes three. Every Total Games request returned HTTP 500 for over an hour --
the highest-volume prop on the board -- and it was found by accident, while
tracing something else. The projection maths was fine; the wiring was not.

This checks the one thing that is cheap to check and catastrophic to miss: that
each prop type still answers. It is deliberately NOT a correctness test. It
asserts nothing about whether a number is good, only that a number arrives,
because a smoke test that also has opinions about accuracy becomes a test that
gets muted.

USAGE
    python backend/smoke_test.py                 # against production
    python backend/smoke_test.py http://localhost:8000

Exits 0 if every prop answers, 1 otherwise -- so it can gate a deploy.
"""
from __future__ import annotations

import sys
import time

import requests

DEFAULT_API = "https://backend-production-84ab.up.railway.app"

# A fixed, well-known matchup. Two established players on the main tour, so the
# stat pipeline has plenty to work with and a failure is the CODE, not thin data.
# If either retires, swap them -- the point is stability, not who they are.
PLAYER, OPPONENT, TOUR = "Taylor Fritz", "Frances Tiafoe", "ATP"

# line values are irrelevant to the test -- they only need to be plausible so
# nothing trips a sanity guard.
PROPS = [
    ("Aces", 8.5),
    ("Double Faults", 3.5),
    ("Break Points Won", 4.5),
    ("Total Games", 22.5),
    ("Player Total Games Won", 11.5),
    ("Fantasy Score", 22.5),
]

TIMEOUT = 180


def _resolve(api: str, name: str, tour: str):
    r = requests.get(f"{api}/api/search", params={"query": name, "tour": tour},
                     timeout=90)
    r.raise_for_status()
    data = r.json()
    if not (isinstance(data, list) and data):
        return None, None
    return str(data[0].get("id")), data[0].get("name")


def main() -> int:
    api = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_API).rstrip("/")
    print(f"smoke test -> {api}")

    try:
        pid, pname = _resolve(api, PLAYER, TOUR)
        oid, oname = _resolve(api, OPPONENT, TOUR)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  search unreachable: {str(exc)[:120]}")
        return 1
    if not (pid and oid):
        print(f"  FAIL  could not resolve {PLAYER} / {OPPONENT} — search is broken "
              f"or these players are gone (swap them at the top of this file)")
        return 1
    print(f"  matchup: {pname} vs {oname}\n")

    failures = []
    for prop, line in PROPS:
        t0 = time.time()
        try:
            r = requests.post(f"{api}/api/prop/calculate", json={
                "player_id": pid, "opponent_id": oid,
                "player_name": pname, "opponent_name": oname,
                "tour": TOUR, "surface": "Hard",
                "court": "US Open, New York, USA",
                "prop_type": prop, "prop_line": line,
            }, timeout=TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            failures.append((prop, f"request failed: {str(exc)[:90]}"))
            print(f"  FAIL  {prop:24} request failed: {str(exc)[:60]}")
            continue

        ms = int((time.time() - t0) * 1000)
        if r.status_code != 200:
            # The 500's message is the whole point -- print it, don't swallow it.
            detail = ""
            try:
                detail = str(r.json().get("detail") or "")[:110]
            except Exception:  # noqa: BLE001
                detail = r.text[:110]
            failures.append((prop, f"HTTP {r.status_code}: {detail}"))
            print(f"  FAIL  {prop:24} HTTP {r.status_code}  {detail}")
            continue

        d = r.json()
        proj, conf, lean = d.get("model_projection"), d.get("confidence"), d.get("lean")
        # data_unavailable is a legitimate answer (upstream outage), not a code
        # fault -- flag it loudly but do not fail the deploy on it.
        if d.get("data_unavailable"):
            print(f"  WARN  {prop:24} data unavailable upstream — not a code failure")
            continue
        if proj is None or conf is None or lean is None:
            failures.append((prop, f"null result proj={proj} conf={conf} lean={lean}"))
            print(f"  FAIL  {prop:24} answered 200 but returned "
                  f"proj={proj} conf={conf} lean={lean}")
            continue
        print(f"  ok    {prop:24} proj={proj:<7} lean={lean:<6} conf={conf:<5} {ms:>5}ms")

    print()
    if failures:
        print(f"SMOKE TEST FAILED — {len(failures)} of {len(PROPS)} props broken:")
        for prop, why in failures:
            print(f"   {prop}: {why}")
        return 1
    print(f"SMOKE TEST PASSED — all {len(PROPS)} props answered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
