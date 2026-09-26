"""Title, description and tags for the daily Short.

ONE TEMPLATE, EVERY DAY. The wording never changes — only the play does. That is
deliberate beyond consistency: a channel whose every upload is visibly the same
format, with the numbers swapped, is what YouTube's inauthentic-content policy
looks for. What keeps this on the right side of it is that the SUBSTANCE is
different daily (a different match, a real projection, its own reasoning), so the
copy stays a stable frame around genuinely new content rather than the content
itself.

Nothing here claims a record or a hit rate. The disclaimer is in every
description because the content is betting-adjacent and the site carries it too.
"""

import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import json


def _fmt(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f == int(f) else str(round(f, 2))


def _clean_tags(raw) -> list:
    """Tags YouTube will actually store as separate tags.

    Commas are the separator, so any tag containing one is split — a tournament
    like "Guadalajara, Mexico" therefore has to become "Guadalajara". Also dedupes
    case-insensitively and respects the 500-character total budget, since YouTube
    silently drops the overflow rather than telling you.
    """
    out, seen, budget = [], set(), 500
    for t in raw:
        for part in str(t or "").split(","):
            s = " ".join(part.split()).strip()
            if not s or s.lower() in seen:
                continue
            if len(s) + 1 > budget:
                break
            seen.add(s.lower())
            out.append(s)
            budget -= len(s) + 1
    return out


def _why(p: dict) -> str:
    """The one-line reason, from what was stored with the pick."""
    mi = p.get("inputs") or {}
    bits = []
    if isinstance(mi.get("opp_hold"), (int, float)):
        bits.append(f"{p.get('opponent')} holds serve "
                    f"{round(mi['opp_hold'])}% of the time")
    if isinstance(mi.get("expected_sets"), (int, float)):
        fmt = mi.get("match_format") or ""
        bits.append(f"{mi['expected_sets']} expected sets"
                    + (f" ({fmt})" if fmt else ""))
    return " · ".join(bits)


def build(plan: dict) -> dict:
    p = plan["pick"]
    lean = str(p.get("lean") or "").upper()
    line = _fmt(p.get("line"))
    where = p.get("tournament") or ""
    potd = plan.get("kind") == "potd"

    # ── THE OPERATOR'S FORMAT ────────────────────────────────────────────────
    # Both of these were set by hand on the first public upload and are matched
    # here exactly, so every post from here on is identical in shape.
    #
    # The title is FIXED — no player, no line. It is a channel-level label, and
    # a viewer scrolling Shorts sees the same name every day rather than a
    # different string of numbers; the play itself is in the video and in the
    # first line of the description.
    kind = plan.get("kind")
    if potd:
        title, head = "Baseline Pick of the Day", "BASELINE POTD"
    elif kind == "top":
        title, head = "Baseline #1 Play", "BASELINE #1 PLAY"
    else:
        # operator-supplied label, e.g. "#2 PLAY" when the POTD line was pulled
        lbl = str(kind).strip().upper()
        title, head = f"Baseline {lbl.title()}", f"BASELINE {lbl}"

    # Short on purpose: the headline names the whole play, the two links sit
    # above the fold, and nothing competes with them. The book line, projection,
    # probability and reasoning all live in the video — repeating them here only
    # pushed the links out of view. The lean is abbreviated the way it would be
    # written on a slip.
    short_lean = ("U" if lean.startswith("U")
                  else "O" if lean.startswith("O") else lean)
    lines = [
        f"{head} : {p.get('player')} {short_lean} {line} {p.get('prop')}",
        "",
        "Full board:   https://baselineev.com",
        "X / Twitter:  https://x.com/BaselineEV",
        "Free Discord: https://discord.gg/tjud82cmZh",
        "—",
        "Model projections, not betting advice. 21+. Please gamble responsibly.",
        "",
        # YouTube surfaces the FIRST THREE above the title, so the books lead.
        # #shorts is last because it is functional rather than descriptive.
        "#prizepicks #sportsbetting #underdog #draftkings #fanduel #shorts",
    ]

    return {
        "title": title,
        "description": "\n".join(lines),
        # A COMMA INSIDE A TAG DESTROYS THE WHOLE LIST. YouTube serialises tags
        # comma-separated, so "Guadalajara, Mexico" did not become one tag with a
        # comma in it — it split, and the rest of the list collapsed into a single
        # run-on tag ("tennis tennis betting player props sports betting..."), which
        # is worth nothing for discovery. Observed on the first live upload.
        "tags": _clean_tags([
            "prizepicks", "sports betting", "underdog", "draftkings", "fanduel",
            "tennis", "tennis betting", "player props",
            p.get("player"), where]),
        # 17 = Sports. Made-for-kids must be false; this is 19+ content.
        "categoryId": "17",
        "selfDeclaredMadeForKids": False,
    }


if __name__ == "__main__":
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    plan = json.load(open(os.path.join(here, "out", "plan.json"), encoding="utf-8"))
    m = build(plan)
    print("TITLE  (%d chars)" % len(m["title"]))
    print(m["title"])
    print("\nDESCRIPTION")
    print(m["description"])
    print("\nTAGS:", ", ".join(t for t in m["tags"] if t))
