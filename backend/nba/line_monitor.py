"""
NBA line-movement watch.

Mirrors nfl/line_monitor.py in shape and in its two hard-won behaviours, and is
a SEPARATE module rather than an import for the same reason that one is: the
tennis monitor pulls `_norm` from pick_of_day, so pointing an NBA task at it
would give this sport a dependency on tennis code. That is the coupling
direction Rule 2 exists to prevent.

THE FIRST PASS NEVER ANNOUNCES
------------------------------
Inherited deliberately, where it was a real bug. The task is rebuilt from
scratch on every bot restart and every board re-arm, each time with fresh state.
Announcing on the first pass therefore re-posted moves a previous instance had
already announced — the same alert firing again minutes after a redeploy. So the
first pass SEEDS state silently, and only departures that first appear on a
later pass are announced.

A MOVE IS NOT NEWS; A FLIP IS
-----------------------------
The threshold has to be relative, and on this board more than any other. A
half-point move is nothing on a 34.5 PRA line and is the entire bet on a 1.5
three-pointers line — a factor of twenty between the two extremes this module
watches. So the test is the LARGER of an absolute tick and a fraction of the
prop's own standard deviation.

What actually matters is whether the move crossed OUR projection, because that
is the moment the bet we liked stops being the bet on offer. Those are flagged;
the rest are informational.
"""

import asyncio
import logging
import os
import time

log = logging.getLogger("baseline.nba.linemonitor")

INTERVAL_SECONDS = int(os.getenv("NBA_LINE_CHECK_SECONDS", str(20 * 60))
                       or 20 * 60)
# Absolute floor, in the prop's own units. Books round to the half point on
# every NBA prop, so anything below this is not a move at all.
MOVE_THRESHOLD = 0.5
# Or this many standard deviations, whichever is LARGER — keeps a half-point
# drift on a 40-point PRA line from being announced as news.
MOVE_THRESHOLD_SD = 0.10
# NBA boards are posted a few hours before tip, not the night before, so the
# watch budget is shorter than NFL's twelve hours.
MAX_RUNTIME_SECS = int(os.getenv("NBA_LINE_WATCH_SECS", str(6 * 3600))
                       or 6 * 3600)


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
    current: {(norm_player, prop): {"line": float, ...}} from nba.lines
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
            "old": old_line, "new": new_line,       # post.build_line_alert_embed
            "projection": w.get("projection"),
            "old_lean": old_lean, "new_lean": new_lean,
            "flipped": bool(old_lean and new_lean and old_lean != new_lean),
            "book": w.get("book"),
        })
    return alerts


async def monitor(rows: list, book: str, post_alert,
                  interval: int = INTERVAL_SECONDS):
    """Watch `rows` for movement until the runtime budget runs out.

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
            log.info("nba line monitor (%s): nothing to watch", book)
            return
        started = time.time()
        log.info("nba line monitor (%s): watching %d play(s) every %ds",
                 book, len(watched), interval)

        first = True
        while watched:
            if not first:
                await asyncio.sleep(interval)
            if time.time() - started > MAX_RUNTIME_SECS:
                log.info("nba line monitor (%s): runtime cap reached", book)
                return
            try:
                current = await asyncio.to_thread(_lines.fetch_lines, book)
            except Exception:  # noqa: BLE001 — Rule 2
                log.exception("nba line monitor (%s): fetch failed", book)
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
                if alerts:
                    log.info("nba line monitor (%s): seeded %d existing "
                             "departure(s) without announcing", book, len(alerts))
                first = False
                continue
            for a in alerts:
                try:
                    await post_alert(a)
                except Exception:  # noqa: BLE001 — one bad post must not stop the watch
                    log.exception("nba line monitor (%s): alert post failed", book)
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nba line monitor (%s) failed: %s", book, exc)
