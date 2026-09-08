"""
NFL line-movement watch.

Mirrors discord-bot/line_monitor.py (tennis) in shape and in its two hard-won
behaviours, but is a SEPARATE module rather than an import: the tennis one lives
in the tennis codebase and pulls `_norm` from pick_of_day, and pointing an NFL
task at it would give this sport a dependency on tennis code. That is the
coupling direction Rule 2 exists to prevent, and it is the same call mlb/ made.

WHAT IT WATCHES
---------------
Whatever rows it is handed — the posted board, or any list carrying
{player, prop, line, projection, lean}. It re-fetches the book, compares, and
reports moves of MOVE_THRESHOLD or more.

THE FIRST PASS NEVER ANNOUNCES
------------------------------
Inherited deliberately from the tennis monitor, where it was a real bug. The
task is rebuilt from scratch on every bot restart and every board re-arm, each
time with fresh state. Announcing on the first pass therefore re-posted moves a
previous instance had already announced — the same alert firing again minutes
after a redeploy. So the first pass SEEDS state silently and only departures
that first appear on a later pass are announced.

A MOVE IS NOT NEWS; A FLIP IS
-----------------------------
A line drifting half a yard is noise on a 70-yard receiving prop and everything
on a 4.5-reception prop, which is why the threshold is expressed in standard
deviations where an sd is known. What actually matters is whether the move
crossed OUR projection — because that is the moment the bet we liked stops being
the bet on offer. Those are flagged, and the rest are informational.
"""

import asyncio
import logging
import time

log = logging.getLogger("baseline.nfl.linemonitor")

INTERVAL_SECONDS = int(__import__("os").getenv("NFL_LINE_CHECK_SECONDS",
                                               str(30 * 60)) or 30 * 60)
# Absolute floor, in the prop's own units — below this nothing is reported even
# if the sd-based test would fire, because books round and a 0.5 tick on a
# reception line is a genuine move while 0.5 on pass yards is a rounding burp.
MOVE_THRESHOLD = 0.5
# Or this many standard deviations, whichever is LARGER. Keeps a 1-yard move on
# a 90-yard line from being called news.
MOVE_THRESHOLD_SD = 0.10
MAX_RUNTIME_SECS = 12 * 3600


def _recompute_lean(projection, line):
    if projection is None or line is None:
        return None
    if projection > line:
        return "OVER"
    if projection < line:
        return "UNDER"
    return "PUSH"


def _is_move(old, new, sd=None) -> bool:
    d = abs((new or 0) - (old or 0))
    if d < MOVE_THRESHOLD:
        return False
    if sd and d < MOVE_THRESHOLD_SD * sd:
        return False
    return True


def detect(watched: list, current: dict) -> list:
    """Pure comparison — no I/O, so it is testable without a bot or a network.

    watched: rows with {player, prop, line, projection, lean, sd, book, _key}
    current: {(norm_player, prop): {"line": float, ...}} from nfl.lines
    Returns a list of alert dicts.
    """
    alerts = []
    for w in watched or []:
        cur = current.get(w["_key"])
        if not cur:
            continue
        new_line = cur.get("line")
        if new_line is None:
            continue
        old_line = w.get("line")
        if not _is_move(old_line, new_line, w.get("sd")):
            continue
        old_lean = w.get("lean") or _recompute_lean(w.get("projection"), old_line)
        new_lean = _recompute_lean(w.get("projection"), new_line)
        alerts.append({
            "player": w.get("player"), "prop": w.get("prop"),
            "old_line": old_line, "new_line": new_line,
            "projection": w.get("projection"),
            "old_lean": old_lean, "new_lean": new_lean,
            "flipped": bool(old_lean and new_lean and old_lean != new_lean),
            "book": w.get("book"),
        })
    return alerts


async def monitor(rows: list, book: str, post_alert,
                  interval: int = INTERVAL_SECONDS):
    """Watch `rows` for movement until the interval budget runs out.

    post_alert: async callable(alert_dict). Injected, so a Discord failure
    cannot reach the model and a test can pass a list append.

    Never raises — Rule 2.
    """
    from . import lines as _lines
    try:
        watched = []
        for r in rows or []:
            if r.get("line") is None or not r.get("player"):
                continue
            watched.append({
                "player": r["player"], "prop": r.get("prop"),
                "line": float(r["line"]), "projection": r.get("projection"),
                "lean": r.get("lean"), "sd": r.get("sd"), "book": book,
                "_key": (_lines._norm(r["player"]), r.get("prop")),
            })
        if not watched:
            log.info("nfl line monitor (%s): nothing to watch", book)
            return
        started = time.time()
        log.info("nfl line monitor (%s): watching %d play(s) every %ds",
                 book, len(watched), interval)

        first = True
        while watched:
            if not first:
                await asyncio.sleep(interval)
            if time.time() - started > MAX_RUNTIME_SECS:
                log.info("nfl line monitor (%s): runtime cap reached", book)
                return
            try:
                current = await asyncio.to_thread(_lines.fetch_lines, book)
            except Exception:  # noqa: BLE001 — Rule 2
                log.exception("nfl line monitor (%s): fetch failed", book)
                first = False
                continue
            if not current:
                first = False
                continue

            alerts = detect(watched, current)
            # Adopt the new lines regardless of whether we announced, so a move
            # is reported once and not on every pass after it.
            for a in alerts:
                for w in watched:
                    if w["player"] == a["player"] and w["prop"] == a["prop"]:
                        w["line"] = a["new_line"]
                        w["lean"] = a["new_lean"]
            if first:
                # Seed only — see module docstring.
                if alerts:
                    log.info("nfl line monitor (%s): seeded %d existing "
                             "departure(s) without announcing", book, len(alerts))
                first = False
                continue
            for a in alerts:
                try:
                    await post_alert(a)
                except Exception:  # noqa: BLE001 — one bad post must not stop the watch
                    log.exception("nfl line monitor (%s): alert post failed", book)
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl line monitor (%s) failed: %s", book, exc)
