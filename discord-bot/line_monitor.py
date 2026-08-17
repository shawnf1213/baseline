"""
Feature 2 — Line Movement Awareness (bot-internal, fully automated).

No user can trigger or view this. After the midnight picks are generated and
logged, the bot starts monitor() as a background task. Every 30 minutes it
re-checks the PrizePicks board for the three logged props and, if a line has
moved >= 0.5 from its midnight original, posts a Line Alert to the line-changes
channel and says whether the lean still holds or has flipped against the new line.

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


def _same_match(pick_opp: str, board_opp: str) -> bool:
    """Is the board entry the SAME match this pick was made on?

    The two names come from different places — ours is the resolved Sofascore
    name, the board's is PrizePicks' description string — so they agree on the
    surname far more reliably than on the full name. Surname match, or either
    name containing the other, is the same test resolve_pick uses to pair a
    pick with a completed match.

    Unknown on either side returns True: the caller treats that as "cannot
    tell", and a monitor that refuses to alert whenever it lacks an opponent
    would go silent on the restart path, which is worse than the bug being
    fixed. The caller logs that case instead.
    """
    a, b = (pick_opp or "").strip(), (board_opp or "").strip()
    if not a or not b:
        return True
    if a == b or a in b or b in a:
        return True
    return a.split()[-1:] == b.split()[-1:]


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
    post_alert: async callable(text) that posts to the line-changes channel.
    """
    try:
        active = []
        for p in picks:
            if p.get("original_line") is None:
                continue
            active.append({
                "pick": p,
                "key": (_norm(p.get("pp_player") or p.get("player", "")), p.get("prop_type")),
                # The MATCH this pick belongs to. The board key is player+prop,
                # which a player's NEXT match reuses verbatim, so the opponent is
                # what separates "the line moved" from "different match".
                "opponent": _norm(p.get("opponent") or ""),
                "original": float(p["original_line"]),
                "alerted": False,        # have we alerted for the current departure?
            })
        if not active:
            return

        started_at = time.time()
        log.info("Line monitor started for %d picks", len(active))

        # First pass runs IMMEDIATELY (no 30-min blind window at the start), but it
        # only ADOPTS whatever departures already exist — it does NOT announce them.
        # The monitor is rebuilt from scratch on every bot restart (redeploy/reconnect)
        # and on every board re-arm (8 PM board, 8 AM second wave), each time with a
        # fresh alerted=False. Announcing on the first pass therefore re-posted moves a
        # prior instance had already announced — the SAME alert firing repeatedly within
        # minutes of a redeploy or re-arm. Now the first pass seeds state silently and
        # only departures that first appear on a LATER pass are announced. At a fresh
        # board post current == original, so nothing is seeded either.
        first = True
        while active:
            if not first:
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

            _next_active = []
            for a in active:
                cur = lines.get(a["key"])
                if cur is None:
                    _next_active.append(a)
                    continue
                # The board now carries {"line", "opponent"}. A bare number is a
                # stale feed shape — treat it as unscoped rather than crashing.
                if isinstance(cur, dict):
                    board_opp = cur.get("opponent") or ""
                    cur = cur.get("line")
                else:
                    board_opp = ""
                if cur is None:
                    _next_active.append(a)
                    continue
                # A DIFFERENT OPPONENT UNDER THE SAME KEY IS THE NEXT MATCH, NOT
                # A LINE MOVE. The pick's match is over (its board entry has been
                # replaced), so there is nothing left to watch — stop, do not
                # compare, and never alert. This is the bug that had Swiatek's
                # already-played match compared against tomorrow's Fantasy Score
                # line and reported as holding.
                if not _same_match(a["opponent"], board_opp):
                    log.info("Line monitor: dropping %s %s — board now shows a "
                             "different opponent (%s, pick was %s); that match is "
                             "over, not a line move",
                             a["pick"].get("player"), a["pick"].get("prop_type"),
                             board_opp or "?", a["opponent"] or "?")
                    continue
                if not a["opponent"] or not board_opp:
                    log.info("Line monitor: %s %s has no opponent on one side "
                             "(pick=%r board=%r) — cannot confirm it is the same "
                             "match; alerting on name+prop alone",
                             a["pick"].get("player"), a["pick"].get("prop_type"),
                             a["opponent"], board_opp)
                _next_active.append(a)
                cur = float(cur)
                moved = abs(cur - a["original"])
                if moved < MOVE_THRESHOLD:
                    a["alerted"] = False     # back near original — re-arm
                    continue
                if a["alerted"]:
                    continue                 # already alerted for this departure
                a["alerted"] = True
                # First-pass departures are adopted silently (see the block comment
                # above): a rebuilt/re-armed monitor must not re-announce a move a prior
                # instance already posted.
                if first:
                    log.info("Line monitor: adopted existing departure %s %s %g->%g "
                             "(first pass — no alert)",
                             a["pick"].get("player"), a["pick"].get("prop_type"),
                             a["original"], cur)
                    continue

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

            # Picks whose match has been replaced on the board were not carried
            # into _next_active — they are finished and stop being watched here.
            active = _next_active
            first = False   # later passes announce genuinely-new departures

        log.info("Line monitor finished — all matches started.")
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — never let this crash anything
        log.exception("Line monitor crashed")
