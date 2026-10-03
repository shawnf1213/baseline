"""
Read-only lookups over the cached NBA game logs — autocomplete, form, history.

ENTIRELY LOCAL. Every function here reads the frame client.load() already holds
in memory, so an autocomplete answers in well under Discord's ~3s deadline and a
form request costs no network at all. Nothing here fetches.

Never raises (Rule 2): an autocomplete that throws shows the user "Loading
options failed", which is worse than an empty list.
"""

import logging

log = logging.getLogger("baseline.nba.queries")


def _frame(season: int = None):
    """The newest game-log frame with rows in it, or None."""
    from . import client as _c
    s = season or _c.current_season()
    df = _c.load("player_game_logs", s)
    if not len(df):
        df = _c.load("player_game_logs", s - 1)
    return df if len(df) else None


def search_players(query: str, limit: int = 25, season: int = None) -> list:
    """[{name, team, games}] matching `query`, most-played first.

    Matches on the NORMALISED name, so "jokic" finds "Nikola Jokic" and
    "doncic" finds "Luka Doncic" without the user reproducing the accents the
    books themselves disagree about.
    """
    from . import client as _c
    try:
        q = _c._norm_name(query or "")
        if len(q) < 2:
            return []
        df = _frame(season)
        if df is None:
            return []
        d = df[["player_name", "team_abbreviation"]].copy()
        d["_k"] = d["player_name"].astype(str).map(_c._norm_name)
        d = d[d["_k"].str.contains(q, regex=False, na=False)]
        if not len(d):
            return []
        g = d.groupby(["player_name", "team_abbreviation"]).size().reset_index(
            name="games")
        g = g.sort_values("games", ascending=False)
        out, seen = [], set()
        for _, r in g.iterrows():
            nm = str(r["player_name"])
            if nm in seen:
                continue
            seen.add(nm)
            out.append({"name": nm,
                        "team": _c.normalize_team(str(r["team_abbreviation"])),
                        "games": int(r["games"])})
            if len(out) >= limit:
                break
        return out
    except Exception:  # noqa: BLE001 — Rule 2
        log.exception("nba search_players failed")
        return []


def recent_games(player: str, n: int = 20, season: int = None) -> list:
    """The player's last `n` games, newest first, as plain dicts."""
    from . import client as _c
    try:
        df = _frame(season)
        if df is None:
            return []
        key = _c._norm_name(player or "")
        d = df[df["player_name"].astype(str).map(_c._norm_name) == key]
        if not len(d):
            return []
        d = d.sort_values("game_date", ascending=False).head(max(1, int(n)))
        cols = [c for c in ("game_date", "matchup", "wl", "min", "pts", "reb",
                            "ast", "fg3m", "nba_fantasy_pts")
                if c in d.columns]
        return d[cols].to_dict("records")
    except Exception:  # noqa: BLE001 — Rule 2
        log.exception("nba recent_games failed")
        return []


def line_history(player: str, stat: str, line: float, n: int = 20,
                 season: int = None) -> dict:
    """How often the player cleared `line` in his last `n`.

    Combos are summed from their components, exactly as recap.RESULT_COL does,
    so "cleared" here means the same thing a graded result means. A separate
    definition is how a history screen ends up disagreeing with the record.
    """
    from .recap import RESULT_COL
    try:
        rows = recent_games(player, n=n, season=season)
        cols = RESULT_COL.get(stat, (stat,))
        vals = []
        for r in rows:
            try:
                vals.append(float(sum(float(r.get(c) or 0) for c in cols)))
            except (TypeError, ValueError):
                continue
        if not vals:
            return {"player": player, "stat": stat, "line": line, "n": 0,
                    "over": 0, "push": 0, "values": []}
        over = sum(1 for v in vals if v > line)
        push = sum(1 for v in vals if v == line)
        return {"player": player, "stat": stat, "line": line, "n": len(vals),
                "over": over, "push": push, "under": len(vals) - over - push,
                "rate": over / len(vals), "values": vals}
    except Exception:  # noqa: BLE001 — Rule 2
        log.exception("nba line_history failed")
        return {"player": player, "stat": stat, "line": line, "n": 0,
                "over": 0, "push": 0, "values": []}
