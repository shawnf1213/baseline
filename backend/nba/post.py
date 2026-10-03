"""
NBA Discord output — embeds and channel routing.

CHANNELS (given by the operator 2026-10-02):

    prizepicks board   1555685687708225617
    underdog board     1555685733694705694
    projections        1555685789323497492   /nbaprop lives here
    line changes       1555685763918860298

Each is an env override so a channel move is a Railway variable, not a deploy.

MIND THE DUPLICATE NAMES. The BASELINE guild has several channels called
"#prizepicks" — the tennis POTD channel, an older one, and the NFL one. They are
indistinguishable by name in the client, and the first NFL test post went to the
wrong one and looked like a silent failure. The four ids above were created as
one batch (1555685...) and belong together; verify by id, never by name.

EVERY POST SAYS WHAT IT KNEW. A projection built on prior-season usage, one
whose minutes are volatile, one on the second night of a back-to-back, or one
lifted by a teammate being OUT — each says so on the row. That is not
decoration: NFL's week-1 board measured +25% relative error precisely on its
unlabelled rows, and a number that cannot explain itself is how that ends up
looking like edge.
"""

import logging
import os

log = logging.getLogger("baseline.nba.post")

COLOR = 0xC8102E          # NBA red — distinct from tennis, NFL navy and MLB blue
COLOR_SHADOW = 0x4F545C   # grey, so a shadow board is obvious at a glance

CHANNELS = {
    ("board", "prizepicks"): int(os.getenv("NBA_PP_BOARD_CHANNEL_ID",
                                           "1555685687708225617") or 0),
    ("board", "underdog"):   int(os.getenv("NBA_UD_BOARD_CHANNEL_ID",
                                           "1555685733694705694") or 0),
    ("projections", None):   int(os.getenv("NBA_PROJECTIONS_CHANNEL_ID",
                                           "1555685789323497492") or 0),
    ("lines", None):         int(os.getenv("NBA_LINE_CHANNEL_ID",
                                           "1555685763918860298") or 0),
}

FOOTER_GENERIC = "Baseline · Model projections, not betting advice"


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
    """Everything a reader needs in order to discount this number."""
    out = []
    if row.get("prior_season_only"):
        out.append("priced on last season only")
    if row.get("rotation") == "volatile":
        out.append(f"ROTATION RISK (minutes cv {row.get('minutes_cv')})")
    elif row.get("rotation") == "variable":
        out.append("minutes move week to week")
    if row.get("back_to_back"):
        out.append("2nd night of a back-to-back")
    if row.get("traded"):
        out.append("changed team this season")
    if row.get("stale") == "red":
        out.append(f"has not played in {row.get('days_since_last')} days")
    elif row.get("stale") == "amber":
        out.append(f"{row.get('days_since_last')}d since last game")
    if row.get("usage_out"):
        names = ", ".join(row["usage_out"][:3])
        out.append(f"usage up — OUT: {names}")
    if row.get("coin_flip"):
        out.append("projection sits within a coin flip of the line")
    if row.get("cap_reason"):
        out.append(row["cap_reason"])
    return out


def _ranked_line(row: dict, rank: int) -> str:
    """One play, two lines, matching the NFL board's shape."""
    side = _side(row)
    dot = "🟢" if side == "OVER" else "🔴"
    p = _prob(row)
    conf = row.get("confidence")
    head = f"**{rank}. {row.get('player')}**"
    body = (f"{dot} **{side} {_fmt(row.get('line'))} "
            f"{(row.get('label') or row.get('prop') or '').upper()}** · "
            f"Proj {_fmt(row.get('projection'))}")
    if isinstance(conf, (int, float)):
        body += f" · {conf:.0f}%"
    if isinstance(p, (int, float)):
        body += f" · p {p:.0%}"
    tail = ""
    cav = _caveats(row)
    if cav:
        tail = "\n_" + " · ".join(cav[:3]) + "_"
    mt = row.get("matchup")
    if mt:
        body += f" · {mt}"
    return f"{head}\n{body}{tail}"


def build_board_embed(rows: list, book: str, shadow: bool = True,
                      slate: str = None, star=None):
    """The ranked NBA board for one book."""
    import discord
    label = "PrizePicks" if book == "prizepicks" else "Underdog"
    title = f"🏀 {slate or ''} {label} NBA Board".strip()
    e = discord.Embed(title=title,
                      colour=COLOR_SHADOW if shadow else COLOR)
    if not rows:
        e.description = "No plays cleared the bar on this slate."
        e.set_footer(text=FOOTER_GENERIC + (" · shadow" if shadow else ""))
        return e
    blocks = []
    for i, r in enumerate(rows, 1):
        if star is not None and r is star:
            blocks.append("⭐ " + _ranked_line(r, i))
        else:
            blocks.append(_ranked_line(r, i))
    e.description = "\n\n".join(blocks)[:4000]
    if shadow:
        e.description = ("⚠️ **SHADOW** — NBA is in testing. This board is not "
                         "part of the public record.\n\n" + e.description)
    e.set_footer(text=FOOTER_GENERIC + (" · shadow" if shadow else ""))
    return e


def build_prop_embed(r: dict, line=None):
    """A single on-demand projection — what /nbaprop returns."""
    import discord
    if not r:
        return None
    side = _side(r)
    colour = COLOR if side == "OVER" else 0x8B0000 if side == "UNDER" else COLOR
    e = discord.Embed(
        title=f"🏀 {r.get('player')} — {r.get('label') or r.get('prop')}",
        colour=colour)
    body = [f"**Projection** {_fmt(r.get('projection'), 2)}"]
    if r.get("fair_line") is not None:
        body.append(f"**Fair line** {_fmt(r.get('fair_line'), 2)}  "
                    f"_(the 50/50 point — the projection is a mean, and this "
                    f"stat is right-skewed)_")
    if r.get("line") is not None:
        body.append(f"**Book line** {_fmt(r.get('line'))} · "
                    f"**{side or '—'}** · edge {_fmt(r.get('edge'), 2)} "
                    f"({_fmt(r.get('edge_sd'), 2)}σ)")
    if r.get("confidence") is not None:
        body.append(f"**Confidence** {r['confidence']}%"
                    + (f" _(capped: {r.get('cap_reason')})_"
                       if r.get("cap_reason") else ""))
    e.description = "\n".join(body)

    if r.get("components"):
        e.add_field(
            name="Components",
            value=" + ".join(f"{k} {v}" for k, v in r["components"].items())
                  + f" = {_fmt(r.get('projection'), 2)}",
            inline=False)

    # ── SUPPORTING STATS ─────────────────────────────────────────────────────
    # Matching the depth the tennis cards give surface stats: the numbers the
    # adjustments are MADE OF, not just the adjusted result. A reader who cannot
    # see "28th vs guards" and "24.1 home / 20.8 away" has to take the
    # projection on trust, which is the opposite of what this product sells.
    ctx = [f"**Minutes** {_fmt(r.get('minutes'))} ({r.get('rotation')})"
           f" · **Games** {r.get('games_in_window')}"]
    if r.get("def_basis"):
        tag = "vs position" if r.get("def_by_position") else "team-level"
        ctx.append(f"**Defense** {r['def_basis']} → ×{r.get('def_factor')} "
                   f"_({tag})_")
    sp = r.get("split") or {}
    if isinstance(sp.get("home"), (int, float)) or isinstance(sp.get("away"), (int, float)):
        where = ("HOME" if r.get("home") else "AWAY") if r.get("home") is not None else "—"
        ctx.append(f"**Home/Away** {_fmt(sp.get('home'))} home / "
                   f"{_fmt(sp.get('away'))} away "
                   f"({sp.get('home_games', 0)}/{sp.get('away_games', 0)} games)"
                   f" · playing **{where}** → ×{r.get('home_factor')}")
        if r.get("home_used"):
            ctx.append(f"_{r['home_used']}_")
    if r.get("pace_basis"):
        ctx.append(f"**Pace** {r['pace_basis']}")
    if r.get("usage_vacuum_basis"):
        ctx.append(f"**Usage** {r['usage_vacuum_basis']}")
    e.add_field(name="Supporting stats", value="\n".join(ctx)[:1024],
                inline=False)

    cav = _caveats(r)
    if cav:
        e.add_field(name="Read this with", value="\n".join(f"• {c}" for c in cav)[:1024],
                    inline=False)
    e.set_footer(text=FOOTER_GENERIC)
    return e


def build_form_embed(player: str, rows: list, stat: str = "pts"):
    """Recent game log with trend arrows — the NBA /nbaform."""
    import discord
    e = discord.Embed(title=f"🏀 {player} — recent form", colour=COLOR)
    if not rows:
        e.description = "No recent games."
        return e
    lines, prev = [], None
    for r in rows[:10]:
        v = r.get(stat)
        arrow = "" if prev is None else (" ▲" if v > prev else " ▼" if v < prev else " ▬")
        lines.append(f"`{str(r.get('game_date'))[:10]}` {r.get('matchup','')} · "
                     f"**{v}** {stat} · {r.get('min')} min{arrow}")
        prev = v
    e.description = "\n".join(lines)
    e.set_footer(text=FOOTER_GENERIC)
    return e


def build_history_embed(player: str, stat: str, line: float, rows: list):
    """How often the player cleared this line in his last N — /nbahistory."""
    import discord
    e = discord.Embed(
        title=f"🏀 {player} — {stat} vs {line:g}", colour=COLOR)
    if not rows:
        e.description = "No games to compare."
        return e
    vals = [r.get(stat) for r in rows if isinstance(r.get(stat), (int, float))]
    over = sum(1 for v in vals if v > line)
    push = sum(1 for v in vals if v == line)
    e.description = (f"**{over}/{len(vals)}** cleared {line:g} "
                     f"({over / len(vals):.0%})"
                     + (f" · {push} push" if push else ""))
    e.add_field(
        name=f"Last {len(vals)}",
        value=" ".join(("🟢" if v > line else "⚪" if v == line else "🔴")
                       for v in vals)[:1024],
        inline=False)
    e.set_footer(text=FOOTER_GENERIC)
    return e


def build_line_alert_embed(alert: dict):
    """A posted play's line has moved."""
    import discord
    e = discord.Embed(title="🏀 NBA line moved", colour=COLOR)
    e.description = (
        f"**{alert.get('player')}** {alert.get('prop')}\n"
        f"{_fmt(alert.get('old'))} → **{_fmt(alert.get('new'))}** "
        f"({alert.get('book')})")
    e.set_footer(text=FOOTER_GENERIC)
    return e
