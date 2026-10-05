"""
NBA grading and recap — resolve posted picks against final box scores.

Mirrors nfl/recap.py: same grade() semantics, same "a day posts only once every
pick on it is settled" rule, same separate record. PUSH handling is identical
across all four sports — an exact-equal result pushed, never rounded into a win.

WHAT "PLAYED" MEANS HERE. stats.nba.com emits no row at all for a player who did
not dress, and a row with zero minutes for one who dressed and never left the
bench. Either way the pick is not gradeable as a zero: a DNP is a VOID, not a
loss. nfl/recap.py learned this with its INVOLVEMENT check, and the tennis
resolver produced four misgrades in a week by treating absent data as a real
result. Minutes are the test here because every NBA stat is downstream of them.

COMBOS GRADE FROM THE SAME COMPONENTS THEY WERE PROJECTED FROM, summed off the
box score — never from a separate "PRA" column, because there isn't one and
inventing one is how a graded result stops matching the projection's definition.
"""

import datetime as _dt
import logging

log = logging.getLogger("baseline.nba.recap")

# Which box-score column settles each prop. Combos list several and are summed.
RESULT_COL = {
    "pts": ("pts",),
    "reb": ("reb",),
    "ast": ("ast",),
    "fg3m": ("fg3m",),
    "nba_fantasy_pts": ("nba_fantasy_pts",),
    "pra": ("pts", "reb", "ast"),
    "pr": ("pts", "reb"),
    "pa": ("pts", "ast"),
    "ra": ("reb", "ast"),
}

_MARK = {"W": "✅", "L": "❌", "PUSH": "⚪", "VOID": "🚫", "PENDING": "⏳"}


def et_today() -> str:
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/New_York")
    except Exception:  # noqa: BLE001
        tz = _dt.timezone(_dt.timedelta(hours=-5))
    return _dt.datetime.now(tz).strftime("%Y-%m-%d")


def _played(row) -> bool:
    """Did this player actually take the floor?

    Minutes are the test. A zero-minute row is a player who dressed and never
    played, and nothing downstream of minutes can be graded from it.
    """
    try:
        import pandas as pd
        m = row.get("min")
        if m is None or (isinstance(m, float) and pd.isna(m)):
            return False
        return float(m) > 0
    except Exception:  # noqa: BLE001
        return False


def grade(lean: str, line, actual) -> str:
    """W / L / PUSH for a settled play."""
    if actual is None or line is None or not lean:
        return "PENDING"
    if float(actual) == float(line):
        return "PUSH"
    over = float(actual) > float(line)
    return "W" if over == (str(lean).upper() == "OVER") else "L"


def resolve(slate_date: str = None, book: str = None, season: int = None,
            commit: bool = True) -> dict:
    """Grade every PENDING pick whose game has finished.

    Returns a summary. Never raises. With no store configured it reports that
    plainly rather than pretending there was nothing to grade.
    """
    from . import store as _store, client as _c
    out = {"graded": 0, "pending": 0, "void": 0, "checked": 0,
           "store": _store.available()}
    if not out["store"]:
        out["error"] = ("the backend results API is unreachable, so nothing "
                        "is being persisted and there is nothing to grade")
        return out
    try:
        rows = _store.pending(book=book, slate_date=slate_date)
        out["checked"] = len(rows)
        if not rows:
            return out
        season = season or _c.current_season()
        df = _c.load("player_game_logs", season)
        if not len(df):
            out["error"] = (f"no {season} game logs published yet — picks "
                            f"stay PENDING")
            out["pending"] = len(rows)
            return out
        import pandas as pd
        df = df.copy()
        df["_key"] = df["player_name"].astype(str).map(_c._norm_name)
        for p in rows:
            cols = RESULT_COL.get(p.get("prop_type"))
            if not cols or any(c not in df.columns for c in cols):
                out["pending"] += 1
                continue
            sd = p.get("slate_date")
            m = df[(df["_key"] == _c._norm_name(p.get("player") or ""))]
            if sd:
                try:
                    m = m[m["game_date"].dt.strftime("%Y-%m-%d") == str(sd)]
                except Exception:  # noqa: BLE001
                    pass
            if not len(m):
                # No box score for that player on that date. Either the game has
                # not been published yet or he did not dress; both stay PENDING
                # until the giveup window upstream decides otherwise. Grading an
                # absent row as a zero is the misgrade this guards against.
                out["pending"] += 1
                continue
            row = m.iloc[0].to_dict()
            if not _played(row):
                if commit:
                    _store.update_result(p.get("id"), "VOID", None)
                out["void"] += 1
                continue
            try:
                actual = float(sum(float(row.get(c) or 0) for c in cols))
            except (TypeError, ValueError):
                out["pending"] += 1
                continue
            res = grade(p.get("lean"), p.get("line"), actual)
            if res == "PENDING":
                out["pending"] += 1
                continue
            if commit:
                _store.update_result(p.get("id"), res, actual)
            out["graded"] += 1
        log.info("nba recap resolve (%s %s): checked=%d graded=%d void=%d "
                 "pending=%d", book or "all", slate_date or "all",
                 out["checked"], out["graded"], out["void"], out["pending"])
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nba resolve failed: %s", exc)
        out["error"] = str(exc)[:200]
        return out


def record(slate_date: str = None, book: str = None,
           since_days: int = None) -> dict:
    """The graded record, optionally scoped. Never raises."""
    from . import store as _store
    try:
        picks = _store.picks_for(slate_date=slate_date, book=book,
                                 since_days=since_days)
        w = sum(1 for p in picks if p.get("result") == "W")
        l = sum(1 for p in picks if p.get("result") == "L")
        pu = sum(1 for p in picks if p.get("result") == "PUSH")
        v = sum(1 for p in picks if p.get("result") == "VOID")
        pend = sum(1 for p in picks
                   if (p.get("result") or "PENDING") == "PENDING")
        dec = w + l
        return {"picks": picks, "wins": w, "losses": l, "pushes": pu,
                "voids": v, "pending": pend,
                "win_rate": (w / dec * 100) if dec else None}
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nba record failed: %s", exc)
        return {"picks": [], "wins": 0, "losses": 0, "pushes": 0, "voids": 0,
                "pending": 0, "win_rate": None}


def build_recap_embed(book: str, slate_date: str, shadow: bool = True):
    """One book's recap for one slate. None when there is nothing to show."""
    import discord
    from .post import COLOR, COLOR_SHADOW, FOOTER_RECAP, _md
    from .props import PROP_LABEL
    rec = record(slate_date=slate_date, book=book)
    rows = rec.get("picks") or []
    if not rows:
        return None
    label = "PrizePicks" if book == "prizepicks" else "Underdog"
    md = _md(slate_date)

    # THE TENNIS RECAP'S SHAPE, field for field: a titled "Today's Picks" list
    # in PLAYER · PROP · LINE · LEAN -> VALUE order, then a "📋 Record" block
    # reading "X/Y cashed". The first cut put the lean before the prop and the
    # record loose in the description, which is the NFL shape this was changed
    # away from on the same day.
    lines = []
    for r in rows:
        res = (r.get("result") or "PENDING").upper()
        av = r.get("result_value")
        prop = PROP_LABEL.get(r.get("prop_type"), r.get("prop_type") or "")
        row = (f"{_MARK.get(res, '⏳')} **{r.get('player')}** {prop} "
               f"{r.get('line')} {(r.get('lean') or '').upper()}")
        if res == "VOID":
            row += " — **DNP**"
        elif isinstance(av, (int, float)):
            row += f" → **{av:g}**"
        lines.append(row)

    # CASHED = W + PUSH over everything that played — the convention tennis,
    # MLB and now NFL all use. VOID never played and is out of both sides.
    w, l, pu = rec["wins"], rec["losses"], rec["pushes"]
    cashed, played = w + pu, w + l + pu
    day = (f"**Today:** {cashed}/{played} cashed "
           f"({cashed / played * 100:.0f}%)"
           + (f"  ·  incl. {pu} push" if pu else "")) if played else \
          "**Today:** nothing settled yet"
    if rec["voids"]:
        day += f"\n_{rec['voids']} void_"

    e = discord.Embed(title=f"📊 {md} NBA {label} Recap",
                      colour=COLOR_SHADOW if shadow else COLOR)
    if shadow:
        e.description = ("⚠️ **SHADOW** — NBA is in testing. This record is "
                         "separate and is not part of the public track record.")
    e.add_field(name="Today's Picks", value="\n".join(lines)[:1024],
                inline=False)
    e.add_field(name="📋 Record", value=day, inline=False)
    e.set_footer(text=f"{FOOTER_RECAP} • {md}" + (" · shadow" if shadow else ""))
    return e
