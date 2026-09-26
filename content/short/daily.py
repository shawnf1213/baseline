"""The whole daily job in one command: pick -> narrate -> render -> upload.

This is what the scheduler runs. Every step is skippable by flag so a failed run
can be resumed from the middle rather than re-synthesising and re-rendering
three minutes of video to retry a thirty-second upload.

    python daily.py                 build, render, upload
    python daily.py --no-upload     build and render only
    python daily.py --upload-only   upload what is already in out/

EXIT CODES MATTER because Task Scheduler reports them: 0 done, 1 a real failure,
2 nothing to post today (no picks on the board). 2 is deliberately NOT an error —
a quiet day is a normal outcome, and a scheduler that emails on it every time
gets muted, which is how a genuine failure goes unnoticed.
"""

import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


# Same isolation for the step processes — a console event reaching daily.py
# must not be able to kill build/render/upload mid-flight.
_FLAGS = (0x08000000 | 0x00000200) if os.name == "nt" else 0


def run(cmd, label):
    print(f"\n── {label} " + "─" * max(0, 56 - len(label)))
    t0 = time.time()
    r = subprocess.run(cmd, cwd=HERE, creationflags=_FLAGS)
    print(f"-- {label}: exit {r.returncode} in {time.time() - t0:.0f}s")
    return r.returncode


def main() -> int:
    args = set(sys.argv[1:])
    do_build = "--upload-only" not in args
    do_upload = "--no-upload" not in args

    if do_build:
        rc = run([sys.executable, "build.py"], "narration")
        if rc == 1:
            print("\nnothing on the board today — nothing to post.")
            return 2
        if rc != 0:
            return 1
        if run(["node", "render.js"], "render") != 0:
            return 1

    if do_upload:
        if run([sys.executable, "upload.py"], "upload") != 0:
            return 1

    print("\ndone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
