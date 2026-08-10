"""
MLB line movement — one channel, BOTH books, every alert labelled.

FORMAT MATCHES THE TENNIS LINE ALERTS deliberately, down to the two-line shape,
the verdict wording and the Edge transition:

    📉 `PrizePicks` **Tarik Skubal** — K **6.5 → 7.5**
    ✅ **OVER holds** · Proj 6.9 · 🔻 Edge 0.4 → 0.6

One plain message per move, not a batched embed. A subscriber reading this
channel should not have to learn a second convention because the sport changed.

THE ONE ADDITION IS THE BOOK, and it is not decoration. PrizePicks and Underdog
post DIFFERENT lines on the same pitcher — one may have Skubal at 6.5 K while
the other has 7.5 — and both feed this single channel. "Skubal 6.5 → 7.5"
without a book is a real number attached to a market it may not belong to, and
acting on it means betting the wrong book. Tennis does not need this because its
alert channel carries one book; MLB does. It leads the line so it cannot be
missed, and no code path emits a move without it.

WHAT COUNTS AS A MOVE. The board's stored line is the ORIGINAL — the number the
projection was judged against when the play was posted. Drift is measured from
that, never from the previous alert, so a line walking 5.5 → 6.0 → 6.5 reports
one move of +1.0 rather than three unrelated nudges.

THE FIRST PASS ADOPTS SILENTLY, exactly as the tennis monitor does and for the
same reason: this loop is rebuilt on every restart, so announcing pre-existing
departures would re-post moves a prior instance already sent — the same alert
firing again minutes after a redeploy. Departures present on the first pass are
recorded and not announced; only ones that appear later are.

WATCHES PENDING ROWS, NOT "TODAY". The primary board runs at 11:30 PM and boards
TOMORROW, so anchoring to today's date would watch a finished card and ignore the
live one.
"""

import logging
import os

log = logging.getLogger("baseline.mlb.linemonitor")

# Shared by both books — which is exactly why every alert names its book.
LINE_CHANGE_CHANNEL_ID = int(
    os.getenv("MLB_LINE_CHANGE_CHANNEL_ID", "1536214940288024587") or 0)

MOVE_THRESHOLD = float(os.getenv("MLB_LINE_MOVE_THRESHOLD", "0.5") or 0.5)
INTERVAL_MINUTES = int(os.getenv("MLB_LINE_CHECK_MINUTES", "30") or 30)

# Below this remaining edge the play is a coin flip and the advice changes —
# the one case where a line move is genuinely actionable rather than noise.
# Tennis uses 0.5 on props scaled like Aces and Total Games; MLB props run from
# earned runs (~2.5) to pitching outs (~17), so a single absolute number cannot
# mean the same thing across them. Kept modest and per-prop-overridable.
COINFLIP_EDGE = float(os.getenv("MLB_LINE_COINFLIP_EDGE", "0.25") or 0.25)

BOOK_LABEL = {"prizepicks": "PrizePicks", "underdog": "Underdog"}

# (book, player, prop) -> line value most recently alerted on.
_alerted = {}
# Whether this process has completed a pass yet. See the docstring: the first
# pass seeds state without announcing.
_seeded = False


def reset_memory() -> None:
    """Forget what has been alerted, and re-arm the silent first pass."""
    global _seeded
    _alerted.clear()
    _seeded = False


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

    Returns move dicts. Reads only — it never rewrites the stored line, because
    that value is the original the play was posted at and overwriting it would
    erase the very thing drift is measured from.

    Never raises: a line-check failure must not disturb boards or grading.
    """
    from . import store as _store, lines as _lines
    global _seeded
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
                    if abs(now - opened) < MOVE_THRESHOLD:
                        # Back near the open — re-arm so a later departure alerts.
                        _alerted.pop((book, who, prop), None)
                        continue
                    key = (book, who, prop)
                    if _alerted.get(key) == now:
                        continue          # already reported at this value
                    _alerted[key] = now
                    if not _seeded:
                        log.info("mlb lines: adopted existing departure %s %s "
                                 "%s %g->%g (first pass — no alert)",
                                 book, who, prop, opened, now)
                        continue
                    proj = r.get("projection")
                    out.append({
                        "book": book, "player": who, "prop": prop,
                        "opened": opened, "now": now, "delta": now - opened,
                        "projection": proj,
                        "posted_lean": r.get("lean"),
                        "current_lean": _lean_for(proj, now),
                        "opponent": r.get("opponent"),
                        "slate_date": r.get("slate_date"),
                    })
            except Exception as exc:  # noqa: BLE001 — one book must not stop the other
                log.exception("mlb line check failed for %s: %s", book, exc)
        _seeded = True
        if out:
            log.info("mlb lines: %d move(s) across %s", len(out),
                     sorted({m.get("slate_date") for m in out}))
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb line check failed: %s", exc)
        return []


def format_alert(m: dict) -> str:
    """One move, in the tennis line-alert shape plus the book.

        📉 `PrizePicks` **Tarik Skubal** — K **6.5 → 7.5**
        ✅ **OVER holds** · Proj 6.9 · 🔻 Edge 0.4 → 0.6

    Same conventions as the tennis alerts: short prop name, bold play, verdict
    first, no narration. A reader should not have to learn a second format
    because the sport changed.
    """
    from .post import PROP_LABEL
    unit = PROP_LABEL.get(m["prop"], (m["prop"], ""))[0]
    book = BOOK_LABEL.get(m["book"], m["book"])
    proj = m.get("projection")
    posted = (m.get("posted_lean") or "").upper()
    new_lean = m.get("current_lean")

    if new_lean and posted and new_lean != posted:
        verdict = f"⚠️ **FLIPPED → {new_lean}**"
    elif new_lean:
        verdict = f"✅ **{new_lean} holds**"
    else:
        verdict = ""

    bits = []
    if verdict:
        bits.append(verdict)
    if isinstance(proj, (int, float)):
        old_e = abs(proj - m["opened"])
        new_e = abs(proj - m["now"])
        bits.append(f"Proj {proj:.1f}")
        if new_e < COINFLIP_EDGE:
            # The one case where the advice itself changes, so it replaces the
            # verdict rather than sitting beside it.
            bits = ["🛑 **COIN FLIP — AVOID**", f"Proj {proj:.1f}",
                    f"Edge {new_e:.2f}"]
        elif abs(new_e - old_e) >= 0.05:
            arrow = "🔻" if new_e < old_e else "🔺"
            bits.append(f"{arrow} Edge {old_e:.1f} → {new_e:.1f}")

    return (f"📉 `{book}` **{m['player']}** — {unit} "
            f"**{m['opened']:g} → {m['now']:g}**\n" + " · ".join(bits))


def post_moves(moves: list, slate_date: str = None, token: str = None,
               channel_id: int = None) -> dict:
    """POST each move as its OWN message, matching the tennis alerts.

    One message per move rather than a digest: each is a separate decision about
    a separate play, and a batch buries the one that flipped among four that
    held. Never raises.
    """
    import requests
    if not moves:
        return {"ok": False, "reason": "no moves"}
    cid = channel_id or LINE_CHANGE_CHANNEL_ID
    tok = token or os.getenv("DISCORD_BOT_TOKEN", "")
    if not cid:
        return {"ok": False, "reason": "no line-change channel configured"}
    if not tok:
        return {"ok": False, "reason": "no DISCORD_BOT_TOKEN"}
    sent, failed = 0, []
    for m in moves:
        try:
            r = requests.post(
                f"https://discord.com/api/v10/channels/{cid}/messages",
                headers={"Authorization": f"Bot {tok}",
                         "Content-Type": "application/json"},
                json={"content": format_alert(m)[:1900],
                      "allowed_mentions": {"parse": []}},
                timeout=30)
            if r.status_code >= 300:
                log.warning("mlb line alert failed %s: %s", r.status_code,
                            r.text[:200])
                failed.append(f"{m['player']} {r.status_code}")
                continue
            sent += 1
            log.info("mlb line alert posted: %s %s %s %g->%g", m["book"],
                     m["player"], m["prop"], m["opened"], m["now"])
        except Exception as exc:  # noqa: BLE001 — one alert must not stop the rest
            log.exception("mlb line alert post failed: %s", exc)
            failed.append(f"{m.get('player')} {str(exc)[:60]}")
    return {"ok": sent > 0, "moves": sent, "failed": failed,
            "channel_id": cid}


def check_and_post(slate_date: str = None, token: str = None) -> dict:
    """One full pass: compare, then post anything that moved. Never raises."""
    try:
        moves = check_once(slate_date)
        if not moves:
            return {"ok": False, "moves": 0, "reason": "no qualifying moves"}
        return post_moves(moves, slate_date, token=token)
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb line check_and_post failed: %s", exc)
        return {"ok": False, "reason": str(exc)[:200]}
