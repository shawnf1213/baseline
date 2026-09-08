"""
NFL Discord presentation — embeds and channel routing.

Mirrors mlb/post.py. Building the embed here rather than in bot.py keeps the
Discord layer thin and means the NFL board can be rendered and eyeballed in a
plain Python shell, without a bot token.

CHANNELS (given by the user 2026-09-08):

    prizepicks board   1535163281768185926
    underdog board     1546942176259346482
    projections        1546942501812834414   /nflprop lives here
    line changes       1546943210574708848

Each is an env override so a channel move is a Railway variable, not a deploy.

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

CHANNELS = {
    ("board", "prizepicks"): int(
        os.getenv("NFL_PP_CHANNEL_ID", "1535163281768185926") or 0),
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


def format_play(row: dict, rank: int = None) -> str:
    """One board line. Two rows on a phone, which is where these are read."""
    lean = _side(row)
    arrow = "🔼" if lean == "OVER" else "🔽"
    head = f"**{row.get('player')}** · {PROP_LABEL.get(row.get('prop'), row.get('prop'))}"
    if rank:
        head = f"`{rank:>2}` {head}"
    prob = _prob(row)
    body = (f"{arrow} **{lean} {_fmt(row.get('line'))}**  ·  "
            f"proj {_fmt(row.get('projection'))}")
    if prob:
        body += f"  ·  {prob * 100:.0f}%"
    if row.get("matchup"):
        body += f"\n{row['matchup']}"
    for c in _caveats(row):
        body += f"\n_{c}_"
    return f"{head}\n{body}"


def build_board_embed(rows: list, book: str, shadow: bool = True,
                      date_label: str = None, max_plays: int = 8):
    """The board embed. Returns None when there is nothing to post.

    An EMPTY board is not an error and is not posted — see nfl.board's
    data-sufficiency gate. In week 1 that is the expected state.
    """
    import discord
    if not rows:
        return None
    shown = rows[:max_plays]
    title = f"NFL Board · {book.title()}"
    if date_label:
        title += f" · {date_label}"
    e = discord.Embed(
        title=("🕶️ SHADOW — " if shadow else "") + title,
        color=COLOR_SHADOW if shadow else COLOR,
        description=("_Shadow mode: these are not posted plays. The model is "
                     "being watched against real results before it counts._"
                     if shadow else None))
    for i, r in enumerate(shown, 1):
        e.add_field(name="​", value=format_play(r, i), inline=False)
    held = len(rows) - len(shown)
    foot = f"{len(rows)} play(s) cleared the filters"
    if held:
        foot += f" · showing top {len(shown)}"
    e.set_footer(text=foot)
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
    """A line-movement alert. `alert` comes from nfl.line_monitor."""
    import discord
    moved = alert.get("new_line", 0) - alert.get("old_line", 0)
    flipped = alert.get("flipped")
    e = discord.Embed(
        title=f"Line moved · {alert.get('player')}",
        color=0xE03C31 if flipped else COLOR,
        description=(f"**{PROP_LABEL.get(alert.get('prop'), alert.get('prop'))}**  "
                     f"{_fmt(alert.get('old_line'))} → **{_fmt(alert.get('new_line'))}** "
                     f"({moved:+.1f})"))
    e.add_field(name="Projection", value=_fmt(alert.get("projection")), inline=True)
    e.add_field(name="Lean was", value=str(alert.get("old_lean")), inline=True)
    e.add_field(name="Lean now", value=str(alert.get("new_lean")), inline=True)
    if flipped:
        e.add_field(name="⚠️", value="The move crossed our projection — the lean "
                                     "has flipped against the new line.",
                    inline=False)
    e.set_footer(text=f"{alert.get('book','')} · NFL")
    return e


def build_intro_embed():
    """The pinned 'how this works' post for the projections channel."""
    import discord
    e = discord.Embed(
        title="NFL Projections — how this works",
        color=COLOR,
        description=(
            "Same engine as the tennis and MLB tools: a projection is built from "
            "**volume × rate**, then compared against the posted line. It is not "
            "a scrape of anyone's number."))
    e.add_field(
        name="Commands",
        value=("`/nflprop player prop [line]` — project one player prop\n"
               "`/nflboard [book]` — the current ranked board\n"
               "Props: Pass Yards, Rush Yards, Receiving Yards, Receptions"),
        inline=False)
    e.add_field(
        name="How a number is built",
        value=("**Volume** — the team's expected plays, split pass/run by the "
               "spread and total. A big favourite runs more; a trailing team "
               "throws.\n"
               "**Rate** — the player's own share and efficiency, shrunk toward "
               "his position's baseline so a two-game hot streak doesn't become "
               "a projection.\n"
               "**Opponent** — applied to the *rate*, never the volume. The "
               "spread already carries the opponent, and counting it twice is "
               "how models talk themselves into fake edges."),
        inline=False)
    e.add_field(
        name="What it will not do",
        value=("**Demons and goblins are never priced.** Those are different "
               "payout structures, not different opinions about a number.\n"
               "**No play without current-season data.** In week 1 the board is "
               "deliberately empty — projecting off last season measured +25% "
               "error against the market, concentrated on players whose role "
               "changed. It fills as real games are played."),
        inline=False)
    e.set_footer(text="Shadow mode until the model is validated against results.")
    return e
