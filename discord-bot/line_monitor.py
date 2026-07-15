"""
Feature 2 — Line Movement Awareness (bot-internal, fully automated).

No user can trigger or view this. After the midnight picks are generated and
logged, the bot starts monitor() as a background task. Every 30 minutes it
re-checks the PrizePicks board for the three logged props and, if a line has
moved >= 0.5 from its midnight original, posts a Line Alert to the picks channel
and says whether the lean still holds or has flipped against the new line.

Isolated: no discord import. The bot passes in get_lines() and an async
post_alert(text) callback, so a failure here only affects this one task.
"""

import asyncio
import logging
import time

from pick_of_day import _norm   # accent-folding only; no discord dependency

log = logging.getLogger("baseline-bot.linemonitor")

INTERVAL_SECONDS = 30 * 60       # check every 30 minutes
MOVE_THRESHOLD   = 0.5           # only alert on moves of this size or larger
MAX_RUNTIME_SECS = 20 * 3600     # safety cap if a start time is unknown
COINFLIP_EDGE    = 0.5           # |proj - new line| below this = coin flip → avoid

# Prop names shortened for alerts — same convention as the pick posts, and the
# same reason: the full name is most of the line width on a phone.
_SHORT_PROP = {
    "Break Points Won":       "BP Won",
    "Player Total Games Won": "Games Won",
    "Total Games":            "Total Games",
    "Double Faults":          "DFs",
    "Aces":                   "Aces",
}


def _recompute_lean(projection, line):
    if projection is None or line is None:
        return None
    if projection > line:
        return "OVER"
    if projection < line:
        return "UNDER"
    return "PUSH"


async def monitor(picks: list, get_lines, post_alert, interval: int = INTERVAL_SECONDS):
    """Watch ``picks`` for line movement until each match starts.

    picks: dicts with pp_player, prop_type, original_line, projection, lean,
           player, start_timestamp.
    get_lines: callable returning {(norm_player, prop_type): line}.
    post_alert: async callable(text) that posts to the picks channel.
    """
    try:
        active = []
        for p in picks:
            if p.get("original_line") is None:
                continue
            active.append({
                "pick": p,
                "key": (_norm(p.get("pp_player") or p.get("player", "")), p.get("prop_type")),
                "original": float(p["original_line"]),
                "alerted": False,        # have we alerted for the current departure?
            })
        if not active:
            return

        started_at = time.time()
        log.info("Line monitor started for %d picks", len(active))

        while active:
            await asyncio.sleep(interval)
            now = time.time()

            # Drop picks whose match has started (or the safety cap elapsed).
            still = []
            for a in active:
                st = a["pick"].get("start_timestamp")
                if (st and now >= st) or (now - started_at > MAX_RUNTIME_SECS):
                    log.info("Line monitor: stopping %s (match started)", a["pick"].get("player"))
                    continue
                still.append(a)
            active = still
            if not active:
                break

            lines = await asyncio.to_thread(get_lines) if not asyncio.iscoroutinefunction(get_lines) else await get_lines()
            if not lines:
                continue

            for a in active:
                cur = lines.get(a["key"])
                if cur is None:
                    continue
                cur = float(cur)
                moved = abs(cur - a["original"])
                if moved < MOVE_THRESHOLD:
                    a["alerted"] = False     # back near original — re-arm
                    continue
                if a["alerted"]:
                    continue                 # already alerted for this departure
                a["alerted"] = True

                # ── Alert copy: two lines, no narration ──────────────────────
                # Was four sentences explaining what a line move is and that the
                # model was recalculating — process commentary a subscriber does
                # not need. They need: which play, where the line went, whether the
                # lean survives, and what it did to the edge. Everything else was
                # words. Same short-prop / bold-play conventions as the pick posts.
                p = a["pick"]
                orig_lean = (p.get("lean") or "").upper()
                new_lean = _recompute_lean(p.get("projection"), cur)
                proj = p.get("projection")

                if new_lean and orig_lean and new_lean != orig_lean:
                    verdict = f"⚠️ **FLIPPED → {new_lean}**"
                elif new_lean:
                    verdict = f"✅ **{new_lean} holds**"
                else:
                    verdict = ""

                # Re-evaluate against the NEW line: a bump toward the projection
                # shrinks the edge; if it collapses into the coin-flip band, say so
                # plainly — that is the one case where the advice changes.
                bits = []
                if verdict:
                    bits.append(verdict)
                if isinstance(proj, (int, float)):
                    old_e = abs(proj - a["original"])
                    new_e = abs(proj - cur)
                    bits.append(f"Proj {proj:.1f}")
                    if new_e < COINFLIP_EDGE:
                        verdict = "🛑 **COIN FLIP — AVOID**"
                        bits = [verdict, f"Proj {proj:.1f}", f"Edge {new_e:.1f}"]
                    elif abs(new_e - old_e) >= 0.05:
                        arrow = "🔻" if new_e < old_e else "🔺"
                        bits.append(f"{arrow} Edge {old_e:.1f} → {new_e:.1f}")

                _prop_short = _SHORT_PROP.get(p.get("prop_type"), p.get("prop_type") or "")
                msg = (
                    f"📉 **{p.get('player')}** — {_prop_short} "
                    f"**{a['original']:g} → {cur:g}**\n"
                    + " · ".join(bits)
                )
                try:
                    await post_alert(msg)
                    log.info("Line alert posted: %s %s %.1f->%.1f",
                             p.get("player"), p.get("prop_type"), a["original"], cur)
                except Exception:  # noqa: BLE001
                    log.exception("post_alert failed")

        log.info("Line monitor finished — all matches started.")
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — never let this crash anything
        log.exception("Line monitor crashed")
