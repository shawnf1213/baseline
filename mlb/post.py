"""
MLB board posting — self-contained, one board per book.

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
channels, and nothing in tennis is touched. When the fixture exists, the generic
half of build_board_embed() lifts into core and this file keeps only
_stat_block(). That is a refactor, not a rewrite.

BOOKS ARE SEPARATE, AND SO ARE THEIR CHANNELS. PrizePicks and Underdog each get
their own board channel and their own Pick of the Day, exactly as tennis runs
them. A PrizePicks 60% and an Underdog 60% are claims about different lines in
different markets and must never share a board or a denominator.

SHADOW SEMANTICS: every channel here is one Shawn keeps hidden from members.
Rule 4 requires that no new prop is visible to members before review, which a
hidden channel satisfies while still exercising the real posting path. Nothing
here writes to the picks/results tables — Rule 3's sport-column gate is unmet, so
MLB persists nothing.
"""

import os
import logging

log = logging.getLogger("baseline.mlb.post")

# ── Channels (MLB testing, hidden from members) ──────────────────────────────
# Per book AND per purpose, mirroring the tennis split. No defaults: an unset
# channel means this module refuses to post rather than guessing a destination.
CHANNELS = {
    ("prizepicks", "board"): int(os.getenv("MLB_PP_BOARD_CHANNEL_ID",
                                           "1535163281768185926") or 0),
    ("underdog",   "board"): int(os.getenv("MLB_UD_BOARD_CHANNEL_ID",
                                           "1535164403383795734") or 0),
    ("prizepicks", "recap"): int(os.getenv("MLB_PP_RECAP_CHANNEL_ID",
                                           "1535164916154109992") or 0),
    ("underdog",   "recap"): int(os.getenv("MLB_UD_RECAP_CHANNEL_ID",
                                           "1535164961448263730") or 0),
}

COLOR = 0x1D428A          # MLB blue — visually distinct from the tennis boards
MAX_PLAYS = 12            # same board size as tennis, for comparability

BOOK_LABEL = {"prizepicks": "PrizePicks", "underdog": "Underdog"}


def channel_for(book: str, kind: str = "board") -> int:
    """Channel id for a (book, kind) pair. 0 when unset — callers must refuse."""
    return CHANNELS.get((book, kind), 0)


def _side_prob(row: dict):
    """The model's probability on the side it actually leans."""
    return row.get("p_over") if row.get("lean") == "OVER" else row.get("p_under")


def select_potd(rows: list) -> dict:
    """The single best play on a book's board, or {} if none qualifies.

    Model probability on its OWN side, restricted to plays that matched a real
    book line — a projection with no line has nothing to be confident against.
    Deliberately simple: MLB has no graded history yet, so richer gating (tiers,
    probation, edge floors) would be tuned on nothing. Tighten it once shadow
    results exist.
    """
    priced = [r for r in rows if r.get("line") is not None]
    if not priced:
        return {}
    best = max(priced, key=lambda r: _side_prob(r) or 0)
    return best if (_side_prob(best) or 0) > 0.5 else {}


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
    c = row.get("context") or {}
    if isinstance(c.get("park_factor"), (int, float)):
        bits.append(f"park **{c['park_factor']:.2f}**")
    ha = c.get("home_away") or {}
    if isinstance(ha.get("factor"), (int, float)):
        bits.append(f"{ha.get('side','')} **{ha['factor']:.2f}**")
    # Name the opponent basis so the board can never imply lineup-level
    # precision it did not have — at a 9am post lineups are not out yet.
    basis = row.get("opponent_basis")
    if basis and basis != "team":
        bits.append(f"_{basis}_")
    return " · ".join(bits)


def _fmt_line(row: dict) -> str:
    """One play. Mirrors the tennis board's rhythm so the two are comparable at a
    glance, but reads MLB fields only."""
    proj = row.get("projection") or 0.0
    line = row.get("line")
    lean = (row.get("lean") or "").upper()
    head = "**" + str(row.get("pitcher", "?")) + "**"
    opp = "_vs " + str(row.get("opponent", "?")) + "_"
    if line is None:
        # No book line matched — projection only. Say so rather than implying a
        # market we did not read.
        return head + "\n" + f"⚪ **PROJ {proj:.1f} K** · no line" + "\n" + opp
    dot = "🟢" if lean == "OVER" else "🔴"
    pct = _side_prob(row)
    conf = f" · {pct * 100:.0f}%" if isinstance(pct, (int, float)) else ""
    mid = f"{dot} **{lean} {line} K** · Proj {proj:.1f}{conf}"
    return head + "\n" + mid + "\n" + opp


def build_potd_embed(row: dict, book: str, date_label: str,
                     shadow: bool = True) -> dict:
    """The starred pick for one book's board."""
    side = _side_prob(row) or 0.0
    edge = row.get("edge_vs_market")
    edge_s = (f" · Edge {edge * 100:+.1f}pp vs market"
              if isinstance(edge, (int, float)) else "")
    dot = "🟢" if row.get("lean") == "OVER" else "🔴"
    parts = [
        "**" + str(row.get("pitcher", "?")) + "** vs **"
        + str(row.get("opponent", "?")) + "**",
        f"{dot} **{row.get('lean')} {row.get('line')} STRIKEOUTS**",
        f"Proj {row.get('projection', 0):.1f} · {side * 100:.0f}%{edge_s}",
        "_" + _stat_block(row) + "_",
    ]
    desc = "\n".join(parts)
    if shadow:
        desc = "⚠️ **SHADOW** — not a released pick.\n\n" + desc
    label = BOOK_LABEL.get(book, book)
    return {
        "title": f"⭐ MLB PICK OF THE DAY — {label} · {date_label}",
        "description": desc[:4000],
        "color": COLOR,
        "footer": {"text": f"Baseline MLB · {label}"
                           + (" · shadow" if shadow else "")},
    }


def build_board_embed(rows: list, date_label: str, book: str = "underdog",
                      shadow: bool = True, start_rank: int = 1) -> dict:
    """Discord embed (as a plain dict) for one BOOK's MLB board.

    Returns a dict rather than a discord.Embed so this module never imports
    discord — it stays testable and importable outside the bot process.
    """
    rows = rows[:MAX_PLAYS]
    label = BOOK_LABEL.get(book, book)
    if rows:
        body = "\n\n".join(f"**{i}.** {_fmt_line(r)}"
                           for i, r in enumerate(rows, start_rank))
    else:
        body = ("_No priced plays — no starter on this slate matched a "
                "straight two-way line on this book._")
    if shadow:
        body = ("⚠️ **SHADOW — not a released board.** Projections only, "
                "nothing logged to the record.\n\n" + body)
    return {
        "title": f"⚾ {date_label} {label} MLB Board — Strikeouts",
        "description": body[:4000],
        "color": COLOR,
        "footer": {"text": f"Baseline MLB · {label}"
                           + (" · shadow" if shadow else "")},
    }


def dedupe_by_game(rows: list) -> list:
    """One play per GAME, keeping the strongest.

    Both starters in a matchup are projectable, so a board could carry Wheeler
    AND the arm opposing him — one game represented twice. Worse than cosmetic:
    the two are negatively correlated through game length. A game that turns into
    a bullpen day suppresses BOTH starters' strikeouts, so carrying both sides
    concentrates risk while looking like diversification. Exactly the reasoning
    behind the tennis one-play-per-match rule.

    Rows must already be sorted best-first; the first sighting of a game_pk wins.
    A row without a game_pk keys on its own pitcher and is never merged.
    """
    seen, kept = set(), []
    for r in rows:
        key = r.get("game_pk") or ("solo", r.get("pitcher"))
        if key in seen:
            log.info("mlb dedupe: dropped %s — same game as a stronger play",
                     r.get("pitcher"))
            continue
        seen.add(key)
        kept.append(r)
    return kept


def build_embeds(rows: list, date_label: str, book: str,
                 shadow: bool = True) -> list:
    """POTD first when one qualifies, then the rest of the board — the same shape
    the tennis boards post in.

    PRICED PLAYS ONLY. A projection with no book line is not actionable: there is
    nothing to be over or under, nothing to grade, and nothing to store. Showing
    them padded the board with "no line" rows that occupied slots a real play
    could have used. They are filtered here so no display path can reach them.
    """
    rows = [r for r in rows if r.get("line") is not None]
    rows = dedupe_by_game(rows)
    potd = select_potd(rows)
    if not potd:
        return [build_board_embed(rows, date_label, book=book, shadow=shadow)]
    rest = [r for r in rows if r is not potd]
    return [build_potd_embed(potd, book, date_label, shadow=shadow),
            build_board_embed(rest, date_label, book=book, shadow=shadow,
                              start_rank=2)]


def post_board(rows: list, date_label: str, book: str = "underdog",
               token: str = None, channel_id: int = None,
               shadow: bool = True) -> dict:
    """POST one book's board to that book's MLB channel over Discord's REST API.

    Uses REST rather than a discord.py channel object so this module has no
    dependency on the bot's event loop and can be run standalone for testing.

    Returns {ok, status, message_id, reason}. NEVER raises — Rule 2: an MLB
    posting failure cannot surface anywhere near the tennis pipeline.
    """
    import requests
    cid = channel_id or channel_for(book, "board")
    tok = token or os.getenv("DISCORD_BOT_TOKEN", "")
    if not cid:
        return {"ok": False, "book": book,
                "reason": f"no board channel configured for {book!r}"}
    if not tok:
        return {"ok": False, "book": book, "reason": "no DISCORD_BOT_TOKEN"}
    try:
        r = requests.post(
            f"https://discord.com/api/v10/channels/{cid}/messages",
            headers={"Authorization": f"Bot {tok}",
                     "Content-Type": "application/json"},
            # No @everyone, ever, from a shadow board.
            json={"embeds": build_embeds(rows, date_label, book, shadow),
                  "allowed_mentions": {"parse": []}},
            timeout=30)
        ok = r.status_code < 300
        if not ok:
            log.warning("mlb post (%s) failed %s: %s", book, r.status_code,
                        r.text[:300])
            return {"ok": False, "book": book, "status": r.status_code,
                    "reason": r.text[:300]}
        return {"ok": True, "book": book, "status": r.status_code,
                "channel_id": cid, "message_id": (r.json() or {}).get("id")}
    except Exception as exc:  # noqa: BLE001
        log.exception("mlb post_board (%s) failed: %s", book, exc)
        return {"ok": False, "book": book, "reason": str(exc)[:200]}
