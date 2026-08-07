"""
MLB board posting — self-contained.

WHY THIS DUPLICATES SHAPE FROM discord-bot/bot.py RATHER THAN REUSING IT
------------------------------------------------------------------------
NORTH_STAR says the core owns posting. It does not yet: the embed builders live
in discord-bot/bot.py and are tennis-shaped — 53 hardcoded tennis prop-name
literals, per-prop stat blocks, and `surface`/`tournament`/`court` read directly.
Reusing them would mean adding MLB branches inside tennis's file, which is what
Rule 2 forbids.

The correct end state is: core owns the embed SHELL (title, ranking, confidence,
result badge — all genuinely sport-agnostic) and each sport module supplies
`stat_block(pick)`. That refactor touches tennis's display layer, and Rule 1
requires a byte-identical check against a cached tennis slate first. That fixture
does not exist yet.

So this module is deliberately standalone: MLB posts its own embeds to its own
channel, and nothing in tennis is touched. When the fixture exists, the generic
half of build_board_embed() lifts into core and this file keeps only
_stat_block(). That is a refactor, not a rewrite.

SHADOW SEMANTICS: posting here goes to MLB_CHANNEL_ID, a channel Shawn keeps
hidden from members. Rule 4's requirement is that no new prop is visible to
members before review, which a hidden channel satisfies while still exercising
the real posting path. Nothing here writes to the picks/results tables — Rule 3's
sport-column gate is still unmet, so MLB persists nothing.
"""

import os
import logging

log = logging.getLogger("baseline.mlb.post")

# Separate channel from the tennis board. No default: without an explicit ID this
# module refuses to post rather than guessing at a channel.
MLB_CHANNEL_ID = int(os.getenv("MLB_CHANNEL_ID", "0") or 0)

COLOR = 0x1D428A          # MLB blue — visually distinct from the tennis boards
MAX_PLAYS = 12            # same board size as tennis, for comparability


def _fmt_line(row: dict) -> str:
    """One play. Mirrors the tennis board's rhythm so the two are comparable at a
    glance, but reads MLB fields only."""
    proj = row.get("projection")
    line = row.get("line")
    lean = (row.get("lean") or "").upper()
    dot = "🟢" if lean == "OVER" else "🔴" if lean == "UNDER" else "⚪"
    head = f"**{row.get('pitcher', '?')}**"
    if line is None:
        # No book line fetched — projection only. Say so rather than implying a
        # market we did not read.
        return f"{head}\n⚪ **PROJ {proj:.1f} K** · vs {row.get('opponent', '?')}"
    pct = row.get("p_over") if lean == "OVER" else row.get("p_under")
    conf = f" · {pct * 100:.0f}%" if isinstance(pct, (int, float)) else ""
    return (f"{head}\n{dot} **{lean} {line} K** · Proj {proj:.1f}{conf}\n"
            f"_vs {row.get('opponent', '?')}_")


def _stat_block(row: dict) -> str:
    """The MLB equivalent of the tennis per-prop stat block. This is the part
    that stays sport-specific after the core refactor."""
    bits = []
    if isinstance(row.get("k_rate"), (int, float)):
        bits.append(f"K% **{row['k_rate'] * 100:.1f}**")
    if isinstance(row.get("adjusted_k_rate"), (int, float)):
        bits.append(f"adj **{row['adjusted_k_rate'] * 100:.1f}**")
    if isinstance(row.get("expected_bf"), (int, float)):
        bits.append(f"BF/start **{row['expected_bf']:.1f}**")
    if isinstance(row.get("opponent_k_rate"), (int, float)):
        bits.append(f"opp K% **{row['opponent_k_rate'] * 100:.1f}**")
    if row.get("starts_in_window"):
        bits.append(f"n=**{row['starts_in_window']}**")
    return " · ".join(bits)


def build_board_embed(rows: list, date_label: str, shadow: bool = True) -> dict:
    """Discord embed (as a plain dict) for an MLB board.

    Returns a dict rather than a discord.Embed so this module never imports
    discord — it stays testable and importable outside the bot process.
    """
    rows = rows[:MAX_PLAYS]
    body = "\n\n".join(f"**{i}. {_fmt_line(r)[2:]}" if False else
                       f"**{i}.** {_fmt_line(r)}" for i, r in enumerate(rows, 1))
    if not rows:
        body = "_No qualifying starters — every probable was below the sample floor._"
    desc = body
    if shadow:
        desc = ("⚠️ **SHADOW — not a released board.** Projections only, nothing "
                "logged to the record.\n\n" + desc)
    return {
        "title": f"⚾ {date_label} MLB Board — Strikeouts",
        "description": desc[:4000],
        "color": COLOR,
        "footer": {"text": "Baseline MLB · shadow" if shadow else "Baseline MLB"},
    }


def build_detail_embed(row: dict) -> dict:
    """Per-pitcher detail — the stat block behind one projection."""
    return {
        "title": f"⚾ {row.get('pitcher', '?')} — Strikeouts",
        "description": (f"vs **{row.get('opponent', '?')}**\n"
                        f"Projection **{row.get('projection')}** K\n\n"
                        f"{_stat_block(row)}"),
        "color": COLOR,
        "footer": {"text": f"window: {row.get('window', '?')}"},
    }


def post_board(rows: list, date_label: str, token: str = None,
               channel_id: int = None, shadow: bool = True) -> dict:
    """POST the board to the MLB channel over Discord's REST API.

    Uses REST rather than a discord.py channel object so this module has no
    dependency on the bot's event loop and can be run standalone for testing.

    Returns {ok, status, message_id, reason}. NEVER raises — Rule 2: an MLB
    posting failure cannot surface anywhere near the tennis pipeline.
    """
    import requests
    cid = channel_id or MLB_CHANNEL_ID
    tok = token or os.getenv("DISCORD_BOT_TOKEN", "")
    if not cid:
        return {"ok": False, "reason": "MLB_CHANNEL_ID not set — refusing to guess"}
    if not tok:
        return {"ok": False, "reason": "no DISCORD_BOT_TOKEN"}
    try:
        r = requests.post(
            f"https://discord.com/api/v10/channels/{cid}/messages",
            headers={"Authorization": f"Bot {tok}",
                     "Content-Type": "application/json"},
            # No @everyone, ever, from a shadow board.
            json={"embeds": [build_board_embed(rows, date_label, shadow=shadow)],
                  "allowed_mentions": {"parse": []}},
            timeout=30)
        ok = r.status_code < 300
        if not ok:
            log.warning("mlb post failed %s: %s", r.status_code, r.text[:300])
            return {"ok": False, "status": r.status_code, "reason": r.text[:300]}
        return {"ok": True, "status": r.status_code,
                "message_id": (r.json() or {}).get("id")}
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb post_board failed: %s", exc)
        return {"ok": False, "reason": str(exc)[:200]}
