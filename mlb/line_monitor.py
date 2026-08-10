"""
MLB line movement — one channel, BOTH books, every alert labelled.

Mirrors discord-bot/line_monitor.py in shape (no discord import; the caller
injects a post callback) but not in content: it watches the MLB store rather
than tennis picks, and it watches two books at once.

THE BOOK LABEL IS THE POINT, NOT DECORATION. PrizePicks and Underdog post
DIFFERENT LINES on the same pitcher — one may have Skubal at 6.5 K while the
other has 7.5 — and both feed this single channel. An alert reading
"Skubal 6.5 -> 7.0" is worse than useless without the book: it is a real number
attached to a market it may not belong to, and acting on it means betting the
wrong book. So the book is the first thing on every line, on its own emphasis,
and there is no code path that emits an alert without one.

WHAT COUNTS AS A MOVE. The board's stored line is the ORIGINAL — the number the
projection was judged against when the play was posted. Drift is measured from
that, never from the previous alert, so a line that walks 5.5 -> 6.0 -> 6.5
reports "+1.0 from open" rather than three unrelated half-point nudges.

WHAT IS SUPPRESSED. Re-alerting the same current line is pointless noise, so a
(book, player, prop) is only re-reported when the line reaches a NEW value.
That memory is in-process: a redeploy can re-report a still-moved line once.
Deliberate — the alternative is a schema migration for a cosmetic dedupe, and a
duplicate alert is cheaper than a missed one.

LEAN FLIPS ARE CALLED OUT. A line crossing the projection means the play we
posted is now on the wrong side of the market. That is the single most
actionable thing this channel produces, so it is stated in words rather than
left for the reader to infer from two numbers.
"""

import logging
import os

log = logging.getLogger("baseline.mlb.linemonitor")

# Shared by both books — which is exactly why every alert names its book.
LINE_CHANGE_CHANNEL_ID = int(
    os.getenv("MLB_LINE_CHANGE_CHANNEL_ID", "1536214940288024587") or 0)

MOVE_THRESHOLD = float(os.getenv("MLB_LINE_MOVE_THRESHOLD", "0.5") or 0.5)

# How close the projection has to sit to the new line before "the lean flipped"
# stops being a meaningful claim. A projection of 5.05 against a 5.0 line is
# technically an OVER, but reporting that as a flip overstates a rounding
# difference — the truthful reading is that the edge is gone either way.
COINFLIP_BAND = float(os.getenv("MLB_LINE_COINFLIP_BAND", "0.25") or 0.25)
INTERVAL_MINUTES = int(os.getenv("MLB_LINE_CHECK_MINUTES", "30") or 30)

BOOK_LABEL = {"prizepicks": "PrizePicks", "underdog": "Underdog"}

# (book, player, prop) -> the line value most recently alerted on.
_alerted = {}


def reset_memory() -> None:
    """Forget what has been alerted. For tests and for a deliberate re-arm."""
    _alerted.clear()


def _lean_for(projection, line):
    """Which side the projection favours against a line. None when unknowable."""
    if projection is None or line is None:
        return None
    if projection > line:
        return "OVER"
    if projection < line:
        return "UNDER"
    return "PUSH"


def check_once(slate_date: str = None) -> list:
    """Compare every UNSETTLED stored MLB play against the books' current lines.

    Works off pending rows rather than "today's slate" on purpose. The primary
    board runs at 11:30 PM and boards TOMORROW, so anchoring to et_today() would
    have the monitor watching a card that finished hours ago and ignoring the one
    just posted. Pending rows are whatever is actually live, whatever date it
    carries.

    Returns a list of move dicts. Reads only — it never rewrites the stored line,
    because that value is the original the play was posted at and overwriting it
    would erase the very thing drift is measured from.

    Never raises: a line-check failure must not disturb boards or grading.
    """
    from . import store as _store, lines as _lines
    out = []
    try:
        for book in ("prizepicks", "underdog"):
            try:
                rows = [r for r in _store.pending(book)
                        if (r.get("result") or "PENDING") == "PENDING"]
                if slate_date:
                    rows = [r for r in rows
                            if r.get("slate_date") == slate_date]
                if not rows:
                    continue
                current = _lines.fetch_lines(book)
                if not current:
                    log.info("mlb lines: %s returned nothing — skipping this "
                             "pass rather than reporting every play as pulled",
                             book)
                    continue
                for r in rows:
                    who = r.get("pitcher")
                    prop = r.get("prop_type")
                    opened = r.get("line")
                    if who is None or opened is None:
                        continue
                    m = current.get((_lines._norm(who), prop))
                    if not m:
                        continue          # off the board now; not a line move
                    now = m.get("line")
                    if not isinstance(now, (int, float)):
                        continue
                    delta = now - opened
                    if abs(delta) < MOVE_THRESHOLD:
                        continue
                    key = (book, who, prop)
                    if _alerted.get(key) == now:
                        continue          # already reported at this value
                    _alerted[key] = now
                    proj = r.get("projection")
                    out.append({
                        "book": book, "player": who, "prop": prop,
                        "opened": opened, "now": now, "delta": delta,
                        "projection": proj,
                        "posted_lean": r.get("lean"),
                        "current_lean": _lean_for(proj, now),
                        "opponent": r.get("opponent"),
                        "slate_date": r.get("slate_date"),
                    })
            except Exception as exc:  # noqa: BLE001 — one book must not stop the other
                log.exception("mlb line check failed for %s: %s", book, exc)
        if out:
            log.info("mlb lines: %d move(s) across %s", len(out),
                     sorted({m.get("slate_date") for m in out}))
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb line check failed: %s", exc)
        return []


def build_alert_embed(moves: list, slate_date: str) -> dict:
    """One embed for a batch of moves, grouped BY BOOK.

    Grouping rather than interleaving: the channel carries two markets, and a
    reader scanning it needs to know which book a number belongs to without
    parsing each line.
    """
    from .post import PROP_LABEL, COLOR
    if not moves:
        return {}
    blocks = []
    for book in ("prizepicks", "underdog"):
        mine = [m for m in moves if m["book"] == book]
        if not mine:
            continue
        lines = [f"__**{BOOK_LABEL.get(book, book)}**__"]
        for m in mine:
            unit = PROP_LABEL.get(m["prop"], ("", ""))[0]
            arrow = "🔺" if m["delta"] > 0 else "🔻"
            proj = m.get("projection")
            tail = ""
            if isinstance(proj, (int, float)):
                gap = abs(proj - m["now"])
                flipped = (m["posted_lean"] and m["current_lean"]
                           and m["current_lean"] != m["posted_lean"])
                # A "flip" of 0.05 is not a flip, it is rounding. Below the
                # coin-flip band the honest statement is that the edge is gone,
                # not that the play is now wrong — and projections print to two
                # decimals here so a 5.05 against a 5.0 line never renders as
                # the nonsensical "5.0 vs 5.0".
                if gap < COINFLIP_BAND:
                    tail = (f"\n   ⚠️ no edge left — projection {proj:.2f} sits "
                            f"on the new line")
                elif flipped:
                    tail = (f"\n   ⚠️ our **{m['posted_lean']}** is now the wrong "
                            f"side — projection {proj:.2f} vs {m['now']}")
                else:
                    tail = (f"\n   projection {proj:.2f} · **{m['posted_lean']}** "
                            f"still holds")
            lines.append(
                f"{arrow} **{m['player']}** {unit} "
                f"**{m['opened']} → {m['now']}** ({m['delta']:+.1f} from open)"
                + tail)
        blocks.append("\n".join(lines))
    try:
        _y, _m, _d = slate_date.split("-")
        label = f"{int(_m)}/{int(_d)}"
    except Exception:  # noqa: BLE001
        label = slate_date
    return {
        "title": f"📉 {label} MLB Line Movement",
        "description": "\n\n".join(blocks)[:4000],
        "color": COLOR,
        "footer": {"text": "Baseline MLB · lines move per book — always check "
                           "the book named above"},
    }


def post_moves(moves: list, slate_date: str, token: str = None,
               channel_id: int = None) -> dict:
    """POST one batch of moves. Returns {ok, ...}. Never raises."""
    import requests
    if not moves:
        return {"ok": False, "reason": "no moves"}
    cid = channel_id or LINE_CHANGE_CHANNEL_ID
    tok = token or os.getenv("DISCORD_BOT_TOKEN", "")
    if not cid:
        return {"ok": False, "reason": "no line-change channel configured"}
    if not tok:
        return {"ok": False, "reason": "no DISCORD_BOT_TOKEN"}
    embed = build_alert_embed(moves, slate_date)
    if not embed:
        return {"ok": False, "reason": "nothing to render"}
    try:
        r = requests.post(
            f"https://discord.com/api/v10/channels/{cid}/messages",
            headers={"Authorization": f"Bot {tok}",
                     "Content-Type": "application/json"},
            json={"embeds": [embed], "allowed_mentions": {"parse": []}},
            timeout=30)
        if r.status_code >= 300:
            log.warning("mlb line alert failed %s: %s", r.status_code,
                        r.text[:300])
            return {"ok": False, "status": r.status_code, "reason": r.text[:300]}
        return {"ok": True, "moves": len(moves), "channel_id": cid,
                "message_id": (r.json() or {}).get("id")}
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb line alert post failed: %s", exc)
        return {"ok": False, "reason": str(exc)[:200]}


def check_and_post(slate_date: str = None, token: str = None) -> dict:
    """One full pass: compare, then post anything that moved. Never raises.

    Posts ONE embed PER SLATE. Around midnight two cards can be live at once —
    tonight's late games still pending while tomorrow's board is already up — and
    a single embed would put two dates under one heading.
    """
    from . import recap as _recap
    try:
        moves = check_once(slate_date)
        if not moves:
            return {"ok": False, "moves": 0, "reason": "no qualifying moves"}
        slates = sorted({m.get("slate_date") or _recap.et_today()
                         for m in moves})
        results, sent = [], 0
        for d in slates:
            batch = [m for m in moves
                     if (m.get("slate_date") or _recap.et_today()) == d]
            res = post_moves(batch, d, token=token)
            results.append(res)
            if res.get("ok"):
                sent += len(batch)
        return {"ok": sent > 0, "moves": sent, "slates": slates,
                "results": results}
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb line check_and_post failed: %s", exc)
        return {"ok": False, "reason": str(exc)[:200]}
