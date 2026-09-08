"""
NFL Discord presentation — embeds and channel routing.

Mirrors mlb/post.py. Building the embed here rather than in bot.py keeps the
Discord layer thin and means the NFL board can be rendered and eyeballed in a
plain Python shell, without a bot token.

CHANNELS (given by the user 2026-09-08):

    prizepicks board   1546942099268706417
    underdog board     1546942176259346482
    projections        1546942501812834414   /nflprop lives here
    line changes       1546943210574708848

Each is an env override so a channel move is a Railway variable, not a deploy.

MIND THE DUPLICATE NAMES. The BASELINE guild has THREE channels called
"#🥇・prizepicks" — the tennis POTD channel (1435683710329684039), an older one
(1535163281768185926), and the NFL one above. They are indistinguishable by name
in the client, and the first test post went to the wrong one and looked like a
silent failure. The four ids above were created as one batch (1546942.../1546943...)
and belong together; verify by id, never by channel name.

EVERY POST SAYS WHAT IT KNEW. A projection built on prior-season usage only, or
one whose volume was cut because the player was demoted, says so on the row.
That is not decoration: the week-1 board measured +25% relative error against
the market precisely on those rows, and a number that cannot explain itself is
how that ends up looking like edge.
"""

import logging
import os

log = logging.getLogger("baseline.nfl.post")

COLOR = 0x013369          # NFL navy — distinct from tennis and the MLB blue
COLOR_SHADOW = 0x4F545C   # grey, so a shadow board is obvious at a glance

# Newline as a name. These strings are assembled by tooling that has eaten a
# backslash escape more than once; a constant cannot be mangled that way.
NL = chr(10)

CHANNELS = {
    ("board", "prizepicks"): int(
        os.getenv("NFL_PP_CHANNEL_ID", "1546942099268706417") or 0),
    ("board", "underdog"): int(
        os.getenv("NFL_UD_CHANNEL_ID", "1546942176259346482") or 0),
    ("projections", None): int(
        os.getenv("NFL_PROJECTIONS_CHANNEL_ID", "1546942501812834414") or 0),
    ("lines", None): int(
        os.getenv("NFL_LINE_CHANGE_CHANNEL_ID", "1546943210574708848") or 0),
}

PROP_LABEL = {
    "pass_yards": "Pass Yards",
    "rush_yards": "Rush Yards",
    "receiving_yards": "Receiving Yards",
    "receptions": "Receptions",
}


def channel_for(kind: str = "board", book: str = None) -> int:
    """Channel id for a post kind. 0 when unset — callers must treat that as
    'do not post' rather than falling back to some other channel."""
    return CHANNELS.get((kind, book), 0)


def _fmt(v, nd=1):
    return f"{v:.{nd}f}" if isinstance(v, (int, float)) else "—"


def _side(row: dict) -> str:
    return (row.get("lean") or "").upper()


def _prob(row: dict):
    lean = _side(row)
    return row.get("p_over") if lean == "OVER" else row.get("p_under")


def _caveats(row: dict) -> list:
    """Everything the reader needs in order to discount this number."""
    out = []
    if row.get("prior_season_only"):
        out.append("⚠️ prior-season usage only — no current-season games")
    rc = row.get("role_change") or {}
    if rc.get("factor") and rc["factor"] < 1.0:
        out.append(f"📉 {rc.get('basis')}")
    if not row.get("script_applied"):
        out.append("no spread available — league-neutral volume")
    return out


# Tennis board conventions, reproduced exactly (bot.py::_ranked_line,
# _stamped_footer, LEAN_DOT). The two boards must read as the same kind of post,
# not as two products — user, 2026-09-08: "make the nfl format follow the tennis
# board scan format".
LEAN_DOT = {"OVER": "🟢", "UNDER": "🔴"}
FOOTER_PROJECTION = "Baseline · Model projections, not betting advice"

# Prop names shortened for the list view, same reason tennis shortens its own:
# the full name is a big share of the line width on a phone.
# The books' own casing. book.title() renders "Prizepicks", which is not how
# either the book or the tennis board writes it.
BOOK_LABEL = {"prizepicks": "PrizePicks", "underdog": "Underdog"}

PROP_SHORT = {
    "pass_yards": "Pass Yards",
    "rush_yards": "Rush Yards",
    "receiving_yards": "Rec Yards",
    "receptions": "Receptions",
}


def _ranked_line(row: dict, rank: int) -> str:
    """One ranked play — two short lines that do not wrap on a phone.

    Byte-for-byte the tennis layout: the player on line one, then the play in
    bold uppercase behind its lean dot, with the projection and confidence in
    plain weight beside it. THE PLAY IS THE HEADLINE; if everything is bold,
    nothing is.

    Edge is deliberately omitted, exactly as in tennis — it is projection minus
    line, so it is derivable from what is already shown and was costing the
    width that forced a third wrapped line.
    """
    lean = _side(row)
    proj = row.get("projection")
    conf = row.get("win_prob")
    l1 = f"**{rank}. {row.get('player')}**"
    play = (f"{lean} {row.get('line'):g} "
            f"{PROP_SHORT.get(row.get('prop'), row.get('prop') or '')}").upper()
    bits = [f"{LEAN_DOT.get(lean, '⚪')} **{play}**"]
    if isinstance(proj, (int, float)):
        bits.append(f"Proj {proj:.1f}")
    if isinstance(conf, (int, float)):
        bits.append(f"{conf * 100:.0f}%")
    return l1 + NL + " · ".join(bits)


def build_board_embed(rows: list, book: str, shadow: bool = True,
                      date_label: str = None, max_plays: int = 8,
                      when=None):
    """The board embed, in the tennis board's shape.

    Returns None when there is nothing to post — an EMPTY board is not an error
    and is not posted. See nfl.board's data-sufficiency gate.

    `shadow` no longer prints a banner over the plays. It still picks the colour
    and is still the thing that decides whether the scheduled task pings, but the
    board itself is now just the board: the user asked for the scan "without
    extra text or warnings", and the tennis board carries none either.
    """
    import datetime
    import discord
    if not rows:
        return None
    shown = rows[:max_plays]
    d = when or datetime.datetime.now()
    label = date_label or f"{d.month}/{d.day}"
    title = f"🏈 {label} {BOOK_LABEL.get(book, book.title())} Board"
    e = discord.Embed(
        title=title,
        color=COLOR_SHADOW if shadow else COLOR,
        description=(NL + NL).join(_ranked_line(r, i)
                                   for i, r in enumerate(shown, 1)))
    e.set_footer(text=f"{FOOTER_PROJECTION} • {label}")
    return e


def build_prop_embed(r: dict, line=None):
    """A single /nflprop answer — the projection plus what drove it."""
    import discord
    if not r:
        return None
    if r.get("skipped"):
        e = discord.Embed(title=f"{r.get('player')} · "
                                f"{PROP_LABEL.get(r.get('prop'), r.get('prop'))}",
                          color=COLOR_SHADOW,
                          description=f"Not priced — {r.get('reason')}")
        return e
    lean = _side(r)
    e = discord.Embed(
        title=f"{r.get('player')} · {PROP_LABEL.get(r.get('prop'), r.get('prop'))}",
        color=COLOR)
    e.add_field(name="Projection", value=f"**{_fmt(r.get('projection'))}**",
                inline=True)
    if r.get("line") is not None:
        e.add_field(name="Line", value=_fmt(r.get("line")), inline=True)
        prob = _prob(r)
        e.add_field(name="Lean",
                    value=f"**{lean}**" + (f" · {prob*100:.0f}%" if prob else ""),
                    inline=True)
    e.add_field(name="Spread ±", value=_fmt(r.get("sd")), inline=True)
    e.add_field(name="Games used",
                value=f"{r.get('games_in_window')} ({r.get('window')})",
                inline=True)
    if r.get("opponent"):
        e.add_field(name="Opponent",
                    value=f"{r['opponent']} · ×{_fmt(r.get('opponent_factor'), 3)}",
                    inline=True)
    d = r.get("drivers") or {}
    drv = [f"{k.replace('_',' ')}: {_fmt(v, 2)}" for k, v in list(d.items())[:6]
           if isinstance(v, (int, float))]
    if drv:
        e.add_field(name="Drivers", value="\n".join(drv), inline=False)
    cav = _caveats(r)
    if cav:
        e.add_field(name="Read this with care", value="\n".join(cav), inline=False)
    if r.get("dist_basis"):
        e.set_footer(text=str(r["dist_basis"]))
    return e


def build_line_alert_embed(alert: dict):
    """A line-movement alert. `alert` comes from nfl.line_monitor.

    Layout lives in core.alerts so tennis and NFL cannot drift — see that
    module's docstring.
    """
    import discord
    from core import alerts as _al
    parts = _al.line_alert(
        player=alert.get("player"),
        prop_label=PROP_LABEL.get(alert.get("prop"), alert.get("prop")),
        old_line=alert.get("old_line"), new_line=alert.get("new_line"),
        projection=alert.get("projection"),
        old_lean=alert.get("old_lean"), new_lean=alert.get("new_lean"),
        book=alert.get("book"), sport="NFL",
        flipped=alert.get("flipped"))
    return _al.to_embed(parts, discord)


def build_intro_embed():
    """The pinned 'how this works' post for the projections channel.

    Deliberately the SAME SHAPE as the tennis projections intro — numbered
    commands, the parameter list under each, one line on what it returns, a rule,
    then the props and the closing notes. Members have already learned that
    layout; a second sport should not make them learn a second one.

    Kept to the commands themselves (user, 2026-09-08). An earlier version
    carried "How a number is built" and "What it will not do" essays. They were
    true, and they belong in documentation rather than in the card someone opens
    to find a command.
    """
    import discord
    rule = "―" * 24
    e = discord.Embed(
        title="🏈 NFL Projections — How To Use",
        color=COLOR,
        description=(
            f"{rule}\n"
            "**Type `/` in this channel to see every command and its "
            "options.**\n\n"

            "1️⃣ `/nflprop` — Project any prop\n"
            "player · prop type · line\n"
            "Returns the projection, the lean, a confidence % and the edge vs "
            "your line.\n\n"

            "2️⃣ `/nflgame` — Game outcome\n"
            "team\n"
            "Win probability and the projected score.\n\n"

            "3️⃣ `/nflspread` — Point spread\n"
            "team · handicap\n"
            "Chance a team covers a handicap, e.g. -3.5.\n\n"

            "4️⃣ `/nflh2h` — Head-to-head record\n"
            "team1 · team2\n"
            "Past meetings and the scores.\n\n"

            "5️⃣ `/nflplayer` — Player profile\n"
            "player\n"
            "Depth-chart role, usage share and efficiency.\n\n"

            "6️⃣ `/nflform` — Current form\n"
            "player · games\n"
            "The last few games, line by line.\n\n"

            "7️⃣ `/nflhistory` — Over/under history\n"
            "player · prop · line\n"
            "How often he has cleared that number.\n\n"

            f"{rule}\n"
            "Props: Pass Yards · Rush Yards · Receiving Yards · Receptions\n"
            "Teams use abbreviations — `SEA`, `KC`, `PHI`.\n\n"

            "Replies are private — only you see them. Keep this channel for bot "
            "commands only.\n"
            "_Model projections, not betting advice._"))
    return e


# ── The tennis-parity command embeds ─────────────────────────────────────────
# One per NFL analogue of a tennis command. Same restraint as the prop embed:
# every number says what window it came from, and nothing is shown that the data
# does not actually support.

def _pct1(v):
    return f"{v * 100:.1f}%" if isinstance(v, (int, float)) else "—"


def build_player_embed(p: dict):
    """/nflplayer — the answer to tennis's /player."""
    import discord
    if not p:
        return None
    role = f"{p.get('depth_pos') or p.get('position') or '?'}" \
           f"{p.get('depth_rank') if p.get('depth_rank') else ''}"
    e = discord.Embed(title=f"{p.get('player')} · {role}", color=COLOR)
    pos = (p.get("position") or "").upper()
    if pos == "QB":
        rows = [("Pass att/game", _fmt(p.get("pass_att_per_game"))),
                ("Yards/attempt", _fmt(p.get("yards_per_attempt"), 2)),
                ("Completion %", _pct1(p.get("completion_pct")))]
    elif pos == "RB":
        rows = [("Carries/game", _fmt(p.get("carries_per_game"))),
                ("Yards/carry", _fmt(p.get("yards_per_carry"), 2)),
                ("Targets/game", _fmt(p.get("targets_per_game"))),
                ("Target share", _pct1(p.get("target_share")))]
    else:
        rows = [("Targets/game", _fmt(p.get("targets_per_game"))),
                ("Target share", _pct1(p.get("target_share"))),
                ("Catch rate", _pct1(p.get("catch_rate"))),
                ("Yards/target", _fmt(p.get("yards_per_target"), 2))]
    for name, val in rows:
        e.add_field(name=name, value=val, inline=True)
    r = p.get("role") or {}
    if r.get("adot") or r.get("air_yards_share"):
        e.add_field(name="Role",
                    value=(f"aDOT {_fmt(r.get('adot'))} · "
                           f"air-yards share {_pct1(r.get('air_yards_share'))}"),
                    inline=False)
    if p.get("snap_ratio"):
        e.add_field(name="Snap trend", value=_fmt(p.get("snap_ratio"), 2),
                    inline=True)
    rc = p.get("role_change") or {}
    if rc.get("factor") and rc["factor"] < 1.0:
        e.add_field(name="Role change", value=f"📉 {rc.get('basis')}", inline=False)
    e.set_footer(text=f"{p.get('games')} games · {p.get('window')}")
    return e


def build_form_embed(f: dict):
    """/nflform — the answer to tennis's /form."""
    import discord
    if not f:
        return None
    if f.get("ambiguous"):
        return discord.Embed(
            title="Which player?", color=COLOR_SHADOW,
            description="That name matches several:\n" +
                        "\n".join(f"• {n}" for n in f["ambiguous"]))
    e = discord.Embed(title=f"{f.get('player')} · recent form", color=COLOR)
    lines = []
    for g in f.get("games", []):
        wk = g.get("week")
        opp = g.get("opponent_team") or "?"
        bits = []
        if g.get("targets") is not None:
            bits.append(f"{int(g.get('receptions') or 0)}/{int(g['targets'])} "
                        f"for {int(g.get('receiving_yards') or 0)}")
        if g.get("carries"):
            bits.append(f"{int(g['carries'])} car {int(g.get('rushing_yards') or 0)}")
        if g.get("attempts"):
            bits.append(f"{int(g.get('completions') or 0)}/{int(g['attempts'])} "
                        f"for {int(g.get('passing_yards') or 0)}")
        lines.append(f"`wk{wk:>2}` vs {opp:<4} " + (" · ".join(bits) or "—"))
    e.description = "\n".join(lines) or "No games in the window."
    e.set_footer(text=f"{f.get('season')} season")
    return e


def build_history_embed(h: dict):
    """/nflhistory — the answer to tennis's /history."""
    import discord
    if not h:
        return None
    if h.get("ambiguous"):
        return discord.Embed(
            title="Which player?", color=COLOR_SHADOW,
            description="That name matches several:\n" +
                        "\n".join(f"• {n}" for n in h["ambiguous"]))
    a, l5 = h.get("all") or {}, h.get("last5") or {}
    e = discord.Embed(
        title=f"{h.get('player')} · {PROP_LABEL.get(h.get('prop'), h.get('prop'))} "
              f"{_fmt(h.get('line'))}",
        color=COLOR)
    if a.get("n"):
        e.add_field(name=f"Season ({a['n']})",
                    value=f"**{a['over']}-{a['under']}**"
                          + (f"-{a['push']}" if a.get("push") else "")
                          + f"  ·  {a['over'] / a['n'] * 100:.0f}% over",
                    inline=True)
    if l5.get("n"):
        e.add_field(name=f"Last {l5['n']}",
                    value=f"**{l5['over']}-{l5['under']}**"
                          + (f"-{l5['push']}" if l5.get("push") else ""),
                    inline=True)
    e.add_field(name="Average", value=_fmt(h.get("mean")), inline=True)
    vals = h.get("values") or []
    if vals:
        # The games themselves, not just the ratio — a hit rate with no games
        # behind it is the easiest number here to over-read.
        strip = " ".join(("**" + f"{v:g}" + "**") if v > h["line"] else f"{v:g}"
                         for v in vals[-12:])
        e.add_field(name="Game by game (bold = over)", value=strip, inline=False)
    e.set_footer(text=f"{h.get('season')} season")
    return e


def build_game_embed(g: dict):
    """/nflgame — the answer to tennis's /match."""
    import discord
    if not g:
        return None
    e = discord.Embed(title=g.get("matchup") or f"{g.get('team')} vs {g.get('opponent')}",
                      color=COLOR)
    if g.get("win_prob") is not None:
        e.add_field(name=f"{g.get('team')} win",
                    value=f"**{g['win_prob'] * 100:.0f}%**", inline=True)
        e.add_field(name=f"{g.get('opponent')} win",
                    value=f"{(1 - g['win_prob']) * 100:.0f}%", inline=True)
    if g.get("proj_for") is not None:
        e.add_field(name="Projected score",
                    value=f"**{g['proj_for']:.1f} – {g['proj_against']:.1f}**",
                    inline=True)
    e.add_field(name="Market",
                value=f"spread {_fmt(g.get('spread'))} · total {_fmt(g.get('total'))}",
                inline=False)
    # Said plainly rather than implied: this is the market's own number turned
    # into a probability, not a disagreement with it.
    e.set_footer(text="From the posted spread and total — a reading aid, not an "
                      "edge. The edge is in the player props.")
    return e


def build_spread_embed(s: dict):
    """/nflspread — the answer to tennis's /spread."""
    import discord
    if not s:
        return None
    hc = s.get("handicap")
    e = discord.Embed(
        title=f"{s.get('team')} {hc:+.1f} · cover chance",
        color=COLOR,
        description=s.get("matchup"))
    e.add_field(name="Covers", value=f"**{s['cover_prob'] * 100:.0f}%**", inline=True)
    e.add_field(name="Expected margin",
                value=f"{s.get('expected_margin'):+.1f}", inline=True)
    e.add_field(name="Market spread", value=_fmt(s.get("spread")), inline=True)
    e.set_footer(text=f"Normal around the market's margin, sd {s.get('margin_sd')} "
                      f"(measured on 5,431 games). At the posted number this is "
                      f"~50% by construction.")
    return e


def build_h2h_embed(h: dict):
    """/nflh2h — the answer to tennis's /h2h."""
    import discord
    if not h:
        return None
    a, b = h.get("team_a"), h.get("team_b")
    e = discord.Embed(title=f"{a} vs {b} · head to head", color=COLOR)
    if not h.get("meetings"):
        e.description = "No meetings on record."
        return e
    e.add_field(name="Record",
                value=f"**{a} {h.get('a_wins')} — {h.get('b_wins')} {b}**"
                      + (f" ({h['ties']} tie)" if h.get("ties") else ""),
                inline=False)
    rows = []
    for m in h["meetings"]:
        mark = "" if not m.get("winner") else ("**" + m["winner"] + "**")
        rows.append(f"`{m['season']} wk{m['week']:>2}`  {m['away']} {m['away_score']} "
                    f"@ {m['home']} {m['home_score']}  → {mark or 'tie'}")
    e.add_field(name=f"Last {len(rows)} of {h.get('total')}",
                value="\n".join(rows), inline=False)
    return e
