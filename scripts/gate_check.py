"""Verify the member gate from the outside — prints status codes only.

    railway run -p <backend project> -s <backend service> -e production -- python scripts/gate_check.py

With `railway run` the service token is in the environment, so the script can
show that a service caller still reaches restricted and premium routes while
an anonymous caller is refused. Nothing secret is printed.
"""
import json
import os
import sys
import urllib.error
import urllib.request

API = os.getenv("BASELINE_API_URL", "https://backend-production-84ab.up.railway.app").rstrip("/")
SVC = (os.getenv("BASELINE_SERVICE_TOKEN") or "").strip()


def code(path, headers=None, method="GET", body=None):
    req = urllib.request.Request(API + path, headers=headers or {}, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:  # noqa: BLE001
        return f"ERR {str(e)[:40]}"


CHECKS = [
    ("public", "GET", "/api/results/summary", 200),
    ("public", "GET", "/api/sports", 200),
    ("public", "GET", "/api/billing/config", 200),
    ("public", "GET", "/api/auth/me", 200),
    ("premium", "GET", "/api/results/record", 401),
    ("premium", "GET", "/api/board/live?book=prizepicks", 401),
    ("premium", "GET", "/api/nba/board", 401),
    ("premium", "GET", "/api/nfl/board", 401),
    ("premium", "GET", "/api/search?query=sinner&tour=ATP", 401),
    ("restricted", "GET", "/api/billing/subscribers", 401),
    ("restricted", "GET", "/api/results/pending", 401),
    ("restricted", "GET", "/api/nba/diag", 401),
    ("restricted", "GET", "/api/cache/clear", 401),
]


def main():
    print(f"{'class':10s} {'route':44s} anon  expect | service")
    bad = 0
    for cls, method, path, expect in CHECKS:
        anon = code(path, method=method)
        svc = code(path, {"X-Service-Token": SVC}, method=method) if SVC else "n/a"
        ok = (anon == expect) and (svc in (200, "n/a") or cls == "public")
        if not ok:
            bad += 1
        print(f"{cls:10s} {path[:44]:44s} {str(anon):5s} {expect:<6d} | {svc}   {'' if ok else '<-- CHECK'}")
    print("service token in env:", bool(SVC))
    print("RESULT:", "OK" if bad == 0 else f"{bad} mismatch(es)")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
