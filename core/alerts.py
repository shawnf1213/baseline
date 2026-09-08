"""
Shared line-movement alert layout — one definition, every sport.

WHY THIS IS IN core/ AND NOT IN A SPORT MODULE
-----------------------------------------------
Tennis and NFL post the same kind of message: a line moved, here is where it
went, here is whether our lean survives it. The user asked for the NFL card's
layout on the tennis alerts too, and the obvious way to do that — copy the
builder across — is how two surfaces start agreeing today and drift by March.
So the LAYOUT lives here as plain data, and each sport supplies its own content.

Deliberately returns a dict of parts, NOT a discord.Embed. core/ must not import
discord: it is shared by the backend and the bot, and pulling a Discord
dependency into the sport-neutral layer to render one card would be the wrong
trade. Each caller builds the embed from these parts.

The sport-specific extras stay possible on purpose. Tennis tracks the EDGE
across the move and can call a coin flip; NFL does not have that yet. `extra`
carries those as ordinary fields rather than forcing every sport to show the
same three numbers.
"""

COLOR_DEFAULT = 0x2B6CB0     # informational
COLOR_FLIPPED = 0xE03C31     # the move crossed our projection


def _fmt(v, nd=1):
    """One decimal, always.

    Betting lines read as 29.5 -> 31.0, not 29.5 -> 31: dropping the trailing
    zero makes a whole number look like a different KIND of number from the half
    it moved off, when it is the same line one tick along.
    """
    if not isinstance(v, (int, float)):
        return "—"
    return f"{v:.{nd}f}"


def line_alert(player, prop_label, old_line, new_line, projection=None,
               old_lean=None, new_lean=None, book=None, sport=None,
               extra=None, flipped=None):
    """Parts for one line-movement card.

    Returns {title, description, color, fields: [(name, value, inline)], footer}.
    `extra` is an optional list of (name, value) appended after the standard
    three, for whatever a sport measures that the others do not.
    """
    if flipped is None:
        flipped = bool(old_lean and new_lean
                       and str(old_lean).upper() != str(new_lean).upper())
    moved = None
    if isinstance(old_line, (int, float)) and isinstance(new_line, (int, float)):
        moved = float(new_line) - float(old_line)

    desc = f"**{prop_label}**  {_fmt(old_line)} → **{_fmt(new_line)}**"
    if moved is not None:
        desc += f"  ({moved:+.1f})"

    fields = [("Projection", _fmt(projection), True),
              ("Lean was", str(old_lean or "—"), True),
              ("Lean now", str(new_lean or "—"), True)]
    for name, value in (extra or []):
        fields.append((name, str(value), True))
    if flipped:
        fields.append(("⚠️", "The move crossed our projection — the lean has "
                             "flipped against the new line.", False))

    foot = " · ".join(p for p in (book, sport) if p)
    return {"title": f"Line moved · {player}",
            "description": desc,
            "color": COLOR_FLIPPED if flipped else COLOR_DEFAULT,
            "fields": fields,
            "footer": foot or None,
            "flipped": flipped}


def to_embed(parts, discord_module):
    """Build a discord.Embed from line_alert() parts.

    The discord module is PASSED IN rather than imported, so this file stays
    importable anywhere core/ is — see the module docstring.
    """
    e = discord_module.Embed(title=parts["title"],
                             description=parts["description"],
                             color=parts["color"])
    for name, value, inline in parts["fields"]:
        e.add_field(name=name, value=value, inline=inline)
    if parts.get("footer"):
        e.set_footer(text=parts["footer"])
    return e
