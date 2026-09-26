"""
NFL resolve and recap — grade what was posted, and summarise it.

DOES NOT POST ANYWHERE. This module produces text and dicts; nothing here knows
about Discord, and nothing calls it on a schedule. The operator asked for the
recap built but explicitly NOT wired to the track-record channel yet (2026-09-10),
so wiring it is a later, separate decision rather than something to be undone.
scripts/nfl_recap.py prints it; that is the only consumer.

A DNP IS A VOID, NOT AN UNDER
-----------------------------
The single most important rule here, and the one the tennis resolver still gets
wrong (see the reliability backlog). A player who did not play posts zero yards,
and zero is under every line — so a naive grader records a win for every UNDER
on an inactive player, and the record silently inflates.

Books void those. So do we. The test is PARTICIPATION, not the stat: a receiver
with three targets and no catches genuinely produced zero and that UNDER is a
real win (Romeo Doubs did exactly that in week 1). A receiver with no row in the
box score at all did not play, and the pick is VOID. The distinction is
targets/carries/attempts — involvement — never the yardage itself.

A pick whose game has not finished stays PENDING. Grading a live game as a loss
because the yards are not there yet would be worse than not grading at all.
"""

import logging

log = logging.getLogger("baseline.nfl.recap")

# canonical prop -> the weekly-log column that settles it
RESULT_COL = {
    "pass_yards": "passing_yards",
    "rush_yards": "rushing_yards",
    "receiving_yards": "receiving_yards",
    "receptions": "receptions",
}

# Columns that prove a player was ON THE FIELD, whatever the stat line says.
INVOLVEMENT = ("targets", "carries", "attempts", "receptions", "passing_yards",
               "rushing_yards", "receiving_yards")


def et_today() -> str:
    import datetime
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/New_York")
    except Exception:  # noqa: BLE001
        tz = datetime.timezone(datetime.timedelta(hours=-4))
    return datetime.datetime.now(tz).strftime("%Y-%m-%d")


def _played(row) -> bool:
    """Did this player actually take the field?

    A box-score row with zero involvement everywhere is a player who dressed but
    never touched the ball; nflverse does not emit a row at all for an inactive.
    Either way the pick is not gradeable.
    """
    try:
        import pandas as pd
        for c in INVOLVEMENT:
            if c in row and not pd.isna(row.get(c)) and float(row.get(c)) != 0:
                return True
        # Every involvement column zero or missing.
        return False
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
            week: int = None, commit: bool = True) -> dict:
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
        wk = week or _c.current_week()
        df = _c.load("stats_player_week", season)
        if not len(df):
            out["error"] = (f"no {season} weekly stats published yet — picks "
                            f"stay PENDING")
            out["pending"] = len(rows)
            return out
        col = ("player_display_name" if "player_display_name" in df.columns
               else "player_name")
        # Which column carries the team, for the inactive-vs-unpublished test
        # below. nflverse has used more than one name for it across seasons.
        _TEAM_COL = next((x for x in ("team", "recent_team", "team_abbr")
                          if x in df.columns), None)
        for p in rows:
            stat = RESULT_COL.get(p.get("prop_type"))
            if not stat or stat not in df.columns:
                out["pending"] += 1
                continue
            _wk = p.get("week") or wk
            m = df[(df[col].astype(str) == p.get("player"))
                   & (df["week"] == _wk)]
            if not len(m):
                # NO BOX-SCORE ROW MEANS ONE OF TWO THINGS, and they are not the
                # same: the player was inactive, or his game has not been
                # published yet. nflverse emits no row at all for an inactive,
                # so waiting for one is waiting for something that will never
                # arrive — and a single such pick holds the whole day's recap,
                # which only posts when nothing is pending. Cade Stover was
                # inactive in week 2 and by himself blocked the 2026-09-20
                # PrizePicks recap indefinitely.
                #
                # HIS TEAM IS THE TEST. If Houston has week-2 rows and Stover is
                # not among them, the game is published and he did not play —
                # that is a VOID, the same answer _played() gives for a row of
                # zeroes. If the team has no rows either, the game genuinely is
                # not in yet and PENDING is still right.
                # NORMALISE BOTH SIDES. The books and nflverse do not spell
                # teams the same way — our rows say LAR, WSH, JAC; nflverse says
                # LA, WAS, JAX. Comparing the raw strings made the test answer
                # "this team has not been published yet" for every Rams player,
                # so Puka Nacua (inactive, week 2) held the whole 2026-09-21
                # PrizePicks recap open instead of being voided. normalize_team
                # already knows every one of these aliases.
                from .client import normalize_team as _nt
                _team = _nt(str(p.get("team") or "").strip())
                if not _team and _TEAM_COL and _TEAM_COL in df.columns:
                    _prior = df[df[col].astype(str) == p.get("player")]
                    _team = _nt(str(_prior.iloc[-1][_TEAM_COL])) if len(_prior) else ""
                _published = False
                if _team and _TEAM_COL and _TEAM_COL in df.columns:
                    _wkrows = df[df["week"] == _wk]
                    _published = bool(len(
                        _wkrows[_wkrows[_TEAM_COL].astype(str).map(_nt) == _team]))
                if _published:
                    log.info("nfl recap: %s has no week-%s row but %s does — "
                             "inactive, voiding", p.get("player"), _wk, _team)
                    if commit:
                        _store.update_result(p["id"], "VOID", None)
                    out["void"] += 1
                else:
                    out["pending"] += 1
                continue
            r = m.iloc[0]
            if not _played(r):
                if commit:
                    _store.update_result(p["id"], "VOID", None)
                out["void"] += 1
                continue
            actual = float(r[stat]) if r[stat] == r[stat] else None
            res = grade(p.get("lean"), p.get("line"), actual)
            if res == "PENDING":
                out["pending"] += 1
                continue
            if commit:
                _store.update_result(p["id"], res, actual)
            out["graded"] += 1
        log.info("nfl recap: graded %d, void %d, still pending %d (of %d)",
                 out["graded"], out["void"], out["pending"], out["checked"])
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl resolve failed: %s", exc)
        out["error"] = str(exc)[:200]
        return out


def record(slate_date: str = None, book: str = None,
           since_days: int = None) -> dict:
    """The record itself: W-L by slate, by prop, and overall."""
    from . import store as _store
    if not _store.available():
        return {"available": False}
    picks = _store.picks_for(slate_date=slate_date, book=book,
                             since_days=since_days)
    out = {"available": True, "picks": picks, "n": len(picks),
           "W": 0, "L": 0, "PUSH": 0, "VOID": 0, "PENDING": 0,
           "by_prop": {}, "by_slate": {}, "potd": {"W": 0, "L": 0}}
    for p in picks:
        res = p.get("result") or "PENDING"
        if res in out:
            out[res] += 1
        bp = out["by_prop"].setdefault(p.get("prop_type"),
                                       {"W": 0, "L": 0, "PUSH": 0, "VOID": 0,
                                        "PENDING": 0})
        if res in bp:
            bp[res] += 1
        bs = out["by_slate"].setdefault(p.get("slate_date"),
                                        {"W": 0, "L": 0, "PUSH": 0, "VOID": 0,
                                         "PENDING": 0})
        if res in bs:
            bs[res] += 1
        if p.get("is_potd") and res in ("W", "L"):
            out["potd"][res] += 1
    decided = out["W"] + out["L"]
    out["win_pct"] = round(out["W"] / decided * 100, 1) if decided else None
    return out


def format_recap(rec: dict, title: str = "NFL Recap") -> str:
    """Plain text — for a terminal or a chat reply, NOT a Discord embed.

    Deliberately text: this is not wired to a channel, and building an embed
    would imply it is one edit away from being posted.
    """
    if not rec.get("available"):
        return (f"{title}\nThe backend results API is unreachable, so no "
                f"picks can be read.")
    if not rec.get("n"):
        return f"{title}\nNo picks recorded for that range."
    lines = [title, "=" * len(title)]
    dec = rec["W"] + rec["L"]
    head = f"{rec['W']}-{rec['L']}"
    if rec["PUSH"]:
        head += f"-{rec['PUSH']} push"
    if rec.get("win_pct") is not None:
        head += f"   ({rec['win_pct']:.0f}%)"
    lines.append(f"Record: {head}   ·   {rec['n']} pick(s) logged")
    if rec["PENDING"] or rec["VOID"]:
        bits = []
        if rec["PENDING"]:
            bits.append(f"{rec['PENDING']} pending")
        if rec["VOID"]:
            bits.append(f"{rec['VOID']} void (did not play)")
        lines.append("           " + " · ".join(bits))
    if rec["potd"]["W"] or rec["potd"]["L"]:
        lines.append(f"Pick of the Day: {rec['potd']['W']}-{rec['potd']['L']}")

    if rec["by_slate"]:
        lines.append("")
        lines.append("By slate:")
        for d in sorted(rec["by_slate"]):
            s = rec["by_slate"][d]
            extra = f"  ({s['PENDING']} pending)" if s["PENDING"] else ""
            lines.append(f"   {d}   {s['W']}-{s['L']}{extra}")
    if rec["by_prop"]:
        lines.append("")
        lines.append("By prop:")
        for p in sorted(rec["by_prop"]):
            s = rec["by_prop"][p]
            lines.append(f"   {p:18s} {s['W']}-{s['L']}"
                         + (f"  ({s['PENDING']} pending)" if s["PENDING"] else ""))

    graded = [p for p in rec["picks"] if p.get("result") in ("W", "L", "PUSH")]
    if graded:
        lines.append("")
        lines.append(f"{'PLAYER':22s} {'PICK':>5} {'LINE':>6} {'PROJ':>6} "
                     f"{'ACTUAL':>7}  RESULT")
        for p in graded:
            star = "*" if p.get("is_potd") else " "
            lines.append(
                f"{star}{str(p.get('player'))[:21]:21s} "
                f"{str(p.get('lean') or ''):>5} "
                f"{(p.get('line') if p.get('line') is not None else 0):6.1f} "
                f"{(p.get('model_projection') or 0):6.1f} "
                f"{(p.get('result_value') if p.get('result_value') is not None else 0):7.1f}"
                f"  {p.get('result')}")
    # Anything priced on last season's usage is flagged, because a record that
    # pools those with in-season picks is measuring two different models.
    prior = sum(1 for p in rec["picks"] if p.get("prior_season_only"))
    if prior:
        lines.append("")
        lines.append(f"NOTE: {prior} of {rec['n']} were priced on prior-season "
                     f"usage only (no current-season games at post time).")
    return "\n".join(lines)
