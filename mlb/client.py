"""
MLB Stats API client — read-only, no credentials, isolated.

statsapi.mlb.com is MLB's own public endpoint: no key, no rate-limit headers, no
TLS fingerprinting (unlike the ATP leaderboard, which needs curl_cffi). Plain
requests works, verified 2026-08-07.

Every function is wrapped and returns an empty/typed result on failure. Per North
Star rule 2 an MLB data outage must never surface as an exception in a shared code
path — it degrades to "no MLB projections", never to a broken tennis board.
"""

import logging
import datetime as _dt

import requests

log = logging.getLogger("baseline.mlb.client")

BASE = "https://statsapi.mlb.com/api/v1"
TIMEOUT = 25
SPORT_ID = 1                      # 1 = MLB (2 = AAA, etc.)
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "application/json",
}


def _get(path: str, **params):
    """GET {BASE}{path} -> dict, or {} on any failure. Never raises."""
    try:
        r = requests.get(f"{BASE}{path}", params=params or None,
                         headers=_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json() or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("mlb api %s failed: %s", path, str(exc)[:160])
        return {}


# ── Schedule / probable pitchers ─────────────────────────────────────────────
def get_schedule(date: str = None) -> list:
    """Games for an ISO date (default today, US Eastern — MLB's own calendar day),
    each with both probable starters where announced.

    Returns [{game_pk, game_date, status, away:{...}, home:{...}}]; [] on failure.
    A probable pitcher of None means MLB has not announced one yet — that is a
    real state, not an error, and callers must skip rather than guess.
    """
    if not date:
        date = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=-4))).strftime("%Y-%m-%d")
    data = _get("/schedule", sportId=SPORT_ID, date=date,
                hydrate="probablePitcher,team,linescore")
    out = []
    for day in (data.get("dates") or []):
        for g in (day.get("games") or []):
            teams = g.get("teams") or {}

            def _side(key):
                s = teams.get(key) or {}
                t = s.get("team") or {}
                pp = s.get("probablePitcher") or {}
                return {
                    "team_id": t.get("id"),
                    "team": t.get("name"),
                    "pitcher_id": pp.get("id"),
                    "pitcher": pp.get("fullName"),
                }
            st = g.get("status") or {}
            out.append({
                "game_pk": g.get("gamePk"),
                "game_date": g.get("gameDate"),
                "status": st.get("detailedState"),
                # "Preview" | "Live" | "Final". Coarser than detailedState and
                # therefore the safe thing to gate on: detailedState has a long
                # tail ("Manager challenge", "Warmup", "Delayed Start: Rain")
                # that a status allow-list would keep getting wrong.
                "abstract_state": st.get("abstractGameState"),
                "away": _side("away"),
                "home": _side("home"),
            })
    log.info("mlb schedule %s: %d games", date, len(out))
    return out


def get_slate_lineups(date: str = None) -> dict:
    """Every posted lineup on a slate in ONE request:
    {game_pk: {"home": [{id, name}], "away": [...]}}.

    context.get_lineup() fetches one game-side at a time and looks up each
    player's handedness, which is right for a single matchup and far too slow for
    a whole board — thirty schedule calls plus a couple of hundred people calls.
    The schedule endpoint hydrates every lineup at once, so this is one call, and
    handedness is left to the caller to fetch only for batters it actually
    prices.

    {} when nothing is posted, which at a morning board time is the normal case.
    """
    if not date:
        date = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=-4))).strftime("%Y-%m-%d")
    data = _get("/schedule", sportId=SPORT_ID, date=date, hydrate="lineups")
    out = {}
    for day in (data.get("dates") or []):
        for g in (day.get("games") or []):
            lu = g.get("lineups") or {}
            sides = {}
            for side, key in (("home", "homePlayers"), ("away", "awayPlayers")):
                players = lu.get(key) or []
                if players:
                    sides[side] = [{"id": p.get("id"), "name": p.get("fullName")}
                                   for p in players if p.get("id")]
            if sides:
                out[g.get("gamePk")] = sides
    log.info("mlb lineups %s: %d game(s) with a posted lineup", date, len(out))
    return out


# ── Pitcher ──────────────────────────────────────────────────────────────────
def search_pitcher(name: str) -> dict:
    """Name -> {id, name}. {} when not found. Exact-ish: MLB's search is decent,
    but we return the first hit only and callers should prefer the schedule's
    pitcher_id whenever they have it."""
    if not name:
        return {}
    data = _get("/people/search", names=name)
    people = data.get("people") or []
    if not people:
        return {}
    return {"id": people[0].get("id"), "name": people[0].get("fullName")}


def get_pitcher_season(pitcher_id, season: int = None) -> dict:
    """Season pitching line for one starter.

    Returns the fields the strikeout model actually needs, already derived:
        k_rate      strikeouts / batters faced   (the rate that matters — K/9
                    conflates rate with how deep the start goes)
        bf_per_start batters faced per START (not per appearance)
        ip_per_start innings per start
    plus the raw counting stats. {} when unavailable.
    """
    if not pitcher_id:
        return {}
    if season is None:
        season = _dt.date.today().year
    data = _get(f"/people/{pitcher_id}/stats",
                stats="season", group="pitching", season=season)
    for block in (data.get("stats") or []):
        for split in (block.get("splits") or []):
            st = split.get("stat") or {}
            gs = st.get("gamesStarted") or 0
            bf = st.get("battersFaced") or 0
            so = st.get("strikeOuts") or 0
            try:
                ip = float(st.get("inningsPitched") or 0)
            except (TypeError, ValueError):
                ip = 0.0
            if not bf:
                continue
            return {
                "pitcher_id": pitcher_id,
                "season": season,
                "games_started": gs,
                "innings_pitched": ip,
                "strikeouts": so,
                "batters_faced": bf,
                "k_rate": so / bf,
                "bf_per_start": (bf / gs) if gs else None,
                "ip_per_start": (ip / gs) if gs else None,
                "k_per_9": st.get("strikeoutsPer9Inn"),
                "era": st.get("era"),
                "whip": st.get("whip"),
            }
    return {}


def get_pitcher_game_log(pitcher_id, season: int = None) -> list:
    """Per-start log: [{date, opponent_id, bf, ip, k, is_start}] newest-first.

    This is the sample the scenario fit is built from — how deep starts actually
    go — so it must be START-ONLY. A relief appearance has a completely different
    length distribution and would drag the fit down.
    """
    if not pitcher_id:
        return []
    if season is None:
        season = _dt.date.today().year
    data = _get(f"/people/{pitcher_id}/stats",
                stats="gameLog", group="pitching", season=season)
    out = []
    for block in (data.get("stats") or []):
        for split in (block.get("splits") or []):
            st = split.get("stat") or {}
            gs = st.get("gamesStarted") or 0
            try:
                ip = float(st.get("inningsPitched") or 0)
            except (TypeError, ValueError):
                ip = 0.0
            out.append({
                "date": split.get("date"),
                "opponent_id": ((split.get("opponent") or {}).get("id")),
                "opponent": ((split.get("opponent") or {}).get("name")),
                "bf": st.get("battersFaced"),
                "ip": ip,
                "k": st.get("strikeOuts"),
                "is_start": bool(gs),
            })
    out.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
    return out


# ── Team batting (the opponent side of a strikeout matchup) ──────────────────
def get_team_batting(season: int = None) -> dict:
    """{team_id: {team, pa, k, k_rate}} for all 30 clubs. {} on failure.

    k_rate is strikeouts / plate appearances — the opponent-quality term. League
    mean is carried alongside so callers can express a matchup as a RATIO to
    league rather than an absolute, which is what keeps the model stable when the
    run environment shifts year to year.
    """
    data = _get("/teams/stats", stats="season", group="hitting",
                season=season or _dt.date.today().year, sportId=SPORT_ID)
    out = {}
    for block in (data.get("stats") or []):
        for split in (block.get("splits") or []):
            st = split.get("stat") or {}
            t = split.get("team") or {}
            pa = st.get("plateAppearances")
            so = st.get("strikeOuts")
            if not pa or so is None:
                continue
            out[t.get("id")] = {
                "team_id": t.get("id"),
                "team": t.get("name"),
                "pa": pa,
                "k": so,
                "k_rate": so / pa,
            }
    return out


def league_k_rate(team_batting: dict = None, season: int = None) -> float:
    """League-average K per plate appearance. Falls back to 0.22, roughly the
    modern MLB norm, so a data outage degrades to a sane constant rather than a
    divide-by-zero."""
    tb = team_batting if team_batting is not None else get_team_batting(season)
    if not tb:
        return 0.22
    tot_k = sum(v["k"] for v in tb.values())
    tot_pa = sum(v["pa"] for v in tb.values())
    return (tot_k / tot_pa) if tot_pa else 0.22
