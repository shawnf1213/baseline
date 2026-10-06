"""Flip or read member-gate enforcement on the backend — instantly, no deploy.

    railway run python scripts/enforce.py status
    railway run python scripts/enforce.py on
    railway run python scripts/enforce.py off
    railway run python scripts/enforce.py clear      # back to PREMIUM_ENFORCE

`railway run` (pinned to the BACKEND project/service) injects ADMIN_TOKEN into
this process; the token is never printed. The override is stored in the
backend's durable cache and picked up within 20 seconds.
"""
import json
import os
import sys
import urllib.request

API = os.getenv("BASELINE_API_URL", "https://backend-production-84ab.up.railway.app").rstrip("/")


def main():
    tok = (os.getenv("ADMIN_TOKEN") or "").strip()
    if not tok:
        print("ADMIN_TOKEN is not in the environment — run through `railway run` on the backend service")
        return 2
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    body = None
    if cmd == "on":
        body = {"on": True}
    elif cmd == "off":
        body = {"on": False}
    elif cmd == "clear":
        body = {"on": None}
    elif cmd != "status":
        print(__doc__)
        return 2
    req = urllib.request.Request(f"{API}/api/admin/enforce",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Admin-Token": tok, "Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        print(json.dumps(json.load(r), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
