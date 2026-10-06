"""Deploy one Railway service, safely. The only way deploys happen from here on.

    python scripts/railway_deploy.py backend
    python scripts/railway_deploy.py bot
    python scripts/railway_deploy.py backend --force     # inside the window

Operator, 2026-10-05 ("deploy safety"), after two incidents in one session:

  THE WRONG SERVICE. A `railway up` run from backend/ deployed the BOT, because
  three tool calls ran in parallel, each `cd`-ing somewhere else, and they share
  one shell cwd -- so the deploy ran from the repo root, which is linked to the
  bot. Every deploy now PINS -p/-s/-e explicitly and prints the target before
  anything is uploaded. The link file is never trusted.

  THE WINDOW. The tennis cache pre-warm runs at 14:45 ET and the PrizePicks
  board posts at 15:00. A deploy restarts the container, which throws away the
  warmed cache and can land mid-scan, so nothing deploys 14:30-15:30 ET without
  --force. (The window moved with the board: it was 16:30-17:30 when the board
  posted at 17:00.)

Never run this in parallel with anything else. It is deliberately slow: it
confirms the target, it refuses the window, and it runs one upload.
"""
import datetime as _dt
import os
import subprocess
import sys

SERVICES = {
    "backend": {"dir": "backend",
                "project": "ac937057-cffe-4d0e-bee1-1546cdb054d1",
                "service": "fc24dfe5-5a75-455c-aad6-bf02b08a0860",
                "label": "baseline-backend / backend-production-84ab"},
    "bot":     {"dir": ".",
                "project": "a98a6b90-31b0-4044-b729-cff66387ffe9",
                "service": "40eec350-2998-4423-b6b8-6160ecb673ed",
                "label": "lovely-amazement / baseline-discord-bot"},
}
ENV = "production"
WINDOW = ((14, 30), (15, 30))      # ET, inclusive start, exclusive end


def _et_now() -> _dt.datetime:
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001
        return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=-4)))


def in_window(now: _dt.datetime = None) -> bool:
    now = now or _et_now()
    t = (now.hour, now.minute)
    return WINDOW[0] <= t < WINDOW[1]


def main(argv: list) -> int:
    if not argv or argv[0] not in SERVICES:
        print(f"usage: railway_deploy.py <{'|'.join(SERVICES)}> [--force]")
        return 2
    name, force = argv[0], "--force" in argv
    svc = SERVICES[name]
    now = _et_now()
    print(f"TARGET   {name}: {svc['label']}")
    print(f"PROJECT  {svc['project']}")
    print(f"SERVICE  {svc['service']}   ENV {ENV}")
    print(f"TIME     {now:%Y-%m-%d %H:%M} ET")
    if in_window(now) and not force:
        print("REFUSED  inside the 14:30-15:30 ET no-deploy window (pre-warm 14:45, "
              "board 15:00). Re-run after 15:30, or pass --force if it cannot wait.")
        return 3
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cwd = os.path.join(root, svc["dir"])
    args = ["up", "--detach", "-p", svc["project"], "-s", svc["service"], "-e", ENV]
    # THE CLI IS RUN BY scripts/deploy.sh, NOT BY THIS FILE. The Railway CLI is
    # an npm shim that the Git Bash shell resolves and a Python subprocess on
    # this machine cannot: Python's environment carries neither the npm
    # directory on PATH nor the same HOME, so shutil.which returned None for
    # every spelling and CreateProcess raised WinError 2 twice. Rather than
    # guess at environments, this script OWNS the policy (target, window) and
    # prints the exact command; the wrapper executes it where `railway` works.
    if "--print-cmd" in argv:
        print("CMD\t" + cwd + "\t" + "\t".join(args))
        return 0
    print("RUN      railway " + " ".join(args) + f"   (cwd {cwd})")
    print("NOTE     run through scripts/deploy.sh — a Python subprocess cannot "
          "resolve the npm shim on this machine")
    r = subprocess.run(["railway"] + args, cwd=cwd)
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
