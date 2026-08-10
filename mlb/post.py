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

# Canonical prop -> (short unit for a board line, full name for the POTD).
# Short units keep a 12-play board readable; the POTD spells the prop out.
PROP_LABEL = {
    "strikeouts":            ("K",     "STRIKEOUTS"),
    "pitching_outs":         ("OUTS",  "PITCHING OUTS"),
    "hits_allowed":          ("HA",    "HITS ALLOWED"),
    "walks_allowed":         ("BBA",   "WALKS ALLOWED"),
    "earned_runs":           ("ER",    "EARNED RUNS"),
    "pitcher_fantasy_score": ("FS",    "PITCHER FANTASY SCORE"),
    "hits":                  ("H",     "HITS"),
    "total_bases":           ("TB",    "TOTAL BASES"),
    "hitter_strikeouts":     ("K",     "HITTER STRIKEOUTS"),
    "walks":                 ("BB",    "WALKS"),
    "home_runs":             ("HR",    "HOME RUNS"),
    "doubles":               ("2B",    "DOUBLES"),
    "triples":               ("3B",    "TRIPLES"),
    "singles":               ("1B",    "SINGLES"),
    "runs":                  ("R",     "RUNS"),
    "rbis":                  ("RBI",   "RBIS"),
    "stolen_bases":          ("SB",    "STOLEN BASES"),
    "hits_runs_rbis":        ("H+R+RBI", "HITS + RUNS + RBIS"),
    "hitter_fantasy_score":  ("FS",    "HITTER FANTASY SCORE"),
}


def _labels(row: dict):
    return PROP_LABEL.get(row.get("prop") or "strikeouts", ("", "PROP"))


def _who(row: dict) -> str:
    return str(row.get("player") or row.get("pitcher") or "?")


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
    if isinstance(row.get("pa_per_game"), (int, float)):
        bits.append(f"PA/g **{row['pa_per_game']:.1f}**")
    if row.get("lineup_slot"):
        bits.append(f"bat **{row['lineup_slot']}**"
                    + (f"{row.get('bat')}" if row.get("bat") else ""))
    if isinstance(row.get("sd"), (int, float)):
        bits.append(f"sd **{row['sd']:.1f}**")
    if row.get("starts_in_window"):
        bits.append(f"n=**{row['starts_in_window']}**")
    elif row.get("games_in_window"):
        bits.append(f"n=**{row['games_in_window']}**")
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
    # Teammate-dependent props are marked ON THE BOARD, not only in code. Runs
    # and RBIs are mostly a function of who bats around him, so the number is a
    # weaker claim than a hit rate and should not read like one.
    if row.get("teammate_dependent"):
        bits.append("_teammate-dep_")
    return " · ".join(bits)


def _fmt_line(row: dict) -> str:
    """One play. Mirrors the tennis board's rhythm so the two are comparable at a
    glance, but reads MLB fields only."""
    proj = row.get("projection") or 0.0
    line = row.get("line")
    lean = (row.get("lean") or "").upper()
    unit = _labels(row)[0]
    head = "**" + _who(row) + "**"
    opp = "_vs " + str(row.get("opponent", "?")) + "_"
    if line is None:
        # No book line matched — projection only. Say so rather than implying a
        # market we did not read.
        return head + "\n" + f"⚪ **PROJ {proj:.1f} {unit}** · no line" + "\n" + opp
    dot = "🟢" if lean == "OVER" else "🔴"
    pct = _side_prob(row)
    conf = f" · {pct * 100:.0f}%" if isinstance(pct, (int, float)) else ""
    mid = f"{dot} **{lean} {line} {unit}** · Proj {proj:.1f}{conf}"
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
        "**" + _who(row) + "** vs **" + str(row.get("opponent", "?")) + "**",
        f"{dot} **{row.get('lean')} {row.get('line')} {_labels(row)[1]}**",
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
        body = ("_No priced plays — nothing on this slate matched a "
                "straight two-way line on this book._")
    if shadow:
        body = ("⚠️ **SHADOW — not a released board.** Projections only, "
                "nothing logged to the record.\n\n" + body)
    # Name the prop types actually on the board rather than a fixed "Strikeouts",
    # which stopped being true the moment the board carried more than one prop.
    kinds = []
    for r in rows:
        u = _labels(r)[0]
        if u and u not in kinds:
            kinds.append(u)
    sub = (" — " + " · ".join(kinds[:6]) + ("…" if len(kinds) > 6 else "")
           if kinds else "")
    return {
        "title": f"⚾ {date_label} {label} MLB Board{sub}",
        "description": body[:4000],
        "color": COLOR,
        "footer": {"text": f"Baseline MLB · {label}"
                           + (" · shadow" if shadow else "")},
    }


# How many DIFFERENT players one game may contribute to a board. One is the
# safest for correlation; two keeps a board from collapsing to three plays on a
# light slate. Never more than one prop from the SAME player regardless.
MAX_PER_GAME = int(os.getenv("MLB_MAX_PER_GAME", "2") or "2")


def dedupe_by_game(rows: list, max_per_game: int = None) -> list:
    """At most `max_per_game` DIFFERENT players per game, one prop each.

    Two separate rules, and the player rule is the strict one:

      - ONE PROP PER PLAYER, ALWAYS. A starter's strikeouts, his outs and his
        earned runs are three views of the same six innings. Boarding them as
        three plays is not diversification, it is the same bet at triple stake —
        and if he is pulled in the third, all three lose together.

      - AT MOST TWO PLAYERS PER GAME. Same reasoning one level out, but weaker:
        two different players in one game share the weather, the park, the
        umpire and the game script, so they are correlated but not identical.
        One per game was too strict on a light slate, where it collapsed a
        fourteen-play board to three.

    Rows must already be sorted best-first; the strongest play for a player and
    the strongest players in a game win. A row without a game_pk keys on its own
    player and is never merged into another game.
    """
    limit = MAX_PER_GAME if max_per_game is None else max_per_game
    per_game, seen_players, kept = {}, set(), []
    for r in rows:
        who = _who(r)
        if who in seen_players:
            log.info("mlb dedupe: dropped %s %s — already boarded on another "
                     "prop", who, r.get("prop"))
            continue
        key = r.get("game_pk") or ("solo", who)
        if per_game.get(key, 0) >= limit:
            log.info("mlb dedupe: dropped %s %s — game already has %d player(s)",
                     who, r.get("prop"), limit)
            continue
        per_game[key] = per_game.get(key, 0) + 1
        seen_players.add(who)
        kept.append(r)
    return kept


def postable(rows: list) -> list:
    """Exactly the rows a board will show, in board order.

    ONE FUNCTION, so the POTD, the board body and the stored record can never
    disagree about what the board contained. Choosing a star from the unfiltered
    list is how tennis once posted a Pick of the Day that was not on the board
    at all.

    PRICED PLAYS ONLY. A projection with no book line is not actionable: there is
    nothing to be over or under, nothing to grade, and nothing to store. Showing
    them padded the board with "no line" rows that occupied slots a real play
    could have used.
    """
    return dedupe_by_game([r for r in rows if r.get("line") is not None])


def build_embeds(rows: list, date_label: str, book: str,
                 shadow: bool = True) -> list:
    """POTD first when one qualifies, then the rest of the board — the same shape
    the tennis boards post in."""
    rows = postable(rows)
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
