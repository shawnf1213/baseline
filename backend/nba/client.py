"""
NBA data ingest — stats.nba.com for history, ESPN for schedule and market.

TWO SOURCES, EACH FOR WHAT IT IS BEST AT — the same split nfl/client.py uses.

stats.nba.com — the league's own stats endpoint. One unauthenticated call to
`playergamelogs` returns EVERY player's EVERY game for a season: measured
2026-10-02, the 2025-26 regular season came back as 26,651 rows across 582
players in 1.0s. That is the whole history this module needs, in one request,
with minutes on every row.

ESPN's public scoreboard — the upcoming schedule AND the spread/total. The
market line is not decoration: pace and game script scale counting stats, and a
blowout empties the fourth quarter. Without a spread there is no script, only a
mean.

WHY NOT hoopR / sportsdataverse (checked 2026-10-02, before any of this was
written). sportsdataverse/hoopR-data is ARCHIVED — last push 2023-04-05, newest
NBA file player_box_2023.parquet. Its live successor sportsdataverse-data does
publish NBA releases, but the ones carrying PER-GAME player box scores
(nba_stats_player_boxscores, espn_nba_player_boxscores) are frozen at March 2023
while the live ones are season aggregates. `nba_stats_player_game_logs` sounds
right and is not: downloaded, it is 2,630 rows with NO player column — team
logs, 30 x 82 plus playoffs. Recency weighting, the 20-game sigma the EVR grade
needs, and "cleared the line in his last 20" all require per-game rows, so the
archive cannot serve this module. Those releases are scraped FROM stats.nba.com
anyway (the tags are literally nba_stats_*), so this goes to the source.

CACHED ON DISK, DELIBERATELY — same reasoning as nfl/client.py. A finished
season never changes; the current one gets a short TTL. Re-fetching a 26k-row
season per projection would make a board scan take minutes and hammer an
endpoint that rate-limits hard.

EGRESS GOES THROUGH core.proxy. stats.nba.com refuses datacenter ranges the way
ESPN does, so a direct call works from a laptop and is dead on Railway — the
exact shape of bug that made /nflgame look fine in every local test. Routed the
same way so one switch covers every NBA egress.

Every function returns an empty result on failure and never raises (Rule 2).
"""

import datetime as _dt
import logging
import os
import time

log = logging.getLogger("baseline.nba.client")

STATS = "https://stats.nba.com/stats"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
TIMEOUT = 90

# Cache under the repo, not /tmp: Railway containers keep the filesystem for the
# life of a deploy, so one fetch serves every scan until the next deploy.
CACHE_DIR = os.getenv("NBA_CACHE_DIR",
                      os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "_cache"))
# A completed season never changes; the current one gets new games nightly.
TTL_CURRENT = int(os.getenv("NBA_CACHE_TTL", str(3 * 3600)) or 3 * 3600)
TTL_FINISHED = 90 * 24 * 3600

# stats.nba.com REQUIRES the x-nba-stats-* pair and a nba.com Referer/Origin.
# Without them it does not 403 — it hangs until it times out, which reads as an
# outage rather than a rejection. Verified working 2026-10-02.
_STATS_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Connection": "keep-alive",
}

# ESPN NEEDS A BARE USER-AGENT, and this is not a typo — see the same note in
# nfl/client.py. A complete browser UA on an API endpoint returns 403; plain
# "Mozilla/5.0" returns 200. The two header sets stay separate rather than
# shared, because the failure is total and looks exactly like an outage.
_ESPN_HEADERS = {"User-Agent": "Mozilla/5.0"}

_mem = {}          # in-process frame cache, keyed by (dataset, season)
_mem_at = {}       # when each _mem entry was stored — see the TTL note in load()
# NEGATIVE cache — seasons genuinely absent upstream, keyed the same way. The
# current season has NO rows until opening night, and a board scan calls load()
# per line, so without this every scan in October fires hundreds of doomed
# requests at an endpoint that rate-limits. Short and not infinite: the season
# starts mid-process-lifetime and must be picked up without a restart.
_mem_neg = {}
NEG_TTL = int(os.getenv("NBA_MISSING_TTL", "900") or 900)   # 15 minutes

# ── TEAM ABBREVIATIONS DIFFER BETWEEN THE TWO SOURCES, AND SILENTLY ──────────
# Same trap nfl/client.py documents. ESPN says GS/SA/NY/NO/UTAH/PHX; stats.nba.com
# says GSW/SAS/NYK/NOP/UTA/PHX. Nothing errors when they disagree — the ratings
# lookup simply misses and the opponent adjustment defaults to 1.000, so a
# projection against the Warriors quietly loses its defensive adjustment while
# looking completely normal.
#
# Everything is normalised to the STATS.NBA.COM convention at the ingest
# boundary, because that is the side the statistics live on.
TEAM_ALIASES = {
    "GS": "GSW", "SA": "SAS", "NY": "NYK", "NO": "NOP",
    "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX", "BKN": "BKN", "BRK": "BKN",
    "CHO": "CHA", "NOH": "NOP", "NJN": "BKN", "SEA": "OKC",
}


def normalize_team(abbr: str) -> str:
    """Team abbreviation in stats.nba.com form. Passes unknown codes through."""
    if not abbr:
        return abbr
    a = str(abbr).strip().upper()
    return TEAM_ALIASES.get(a, a)


def current_season(today: _dt.date = None) -> int:
    """The NBA season a date belongs to, named by the year it ENDS.

    stats.nba.com spells a season "2025-26" and sportsdataverse files it as
    2026, so the ending year is the canonical id here. A season tips off in
    October and runs to June, so October-December belong to the NEXT year's
    season — getting this backwards in November would silently read a season
    that has not been played.
    """
    today = today or _dt.date.today()
    return today.year + 1 if today.month >= 9 else today.year


def season_str(season: int = None) -> str:
    """stats.nba.com's season format: 2026 -> "2025-26"."""
    s = int(season or current_season())
    return f"{s - 1}-{str(s)[-2:]}"


def _cache_path(dataset: str, season: int) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{dataset}_{season}.parquet")


def _stats_get(endpoint: str, params: dict):
    """One stats.nba.com call -> the first resultSet as (headers, rows).

    (None, None) on any failure. Never raises.
    """
    try:
        from core import proxy as _px
        r = _px.get(f"{STATS}/{endpoint}", "nba", params=params,
                    headers=_STATS_HEADERS, timeout=TIMEOUT)
        if r is None:
            log.warning("nba stats: %s request failed (proxy and direct)",
                        endpoint)
            return None, None
        r.raise_for_status()
        d = r.json() or {}
        rs = (d.get("resultSets") or d.get("resultSet") or [{}])
        if isinstance(rs, dict):
            rs = [rs]
        if not rs:
            return None, None
        return rs[0].get("headers") or [], rs[0].get("rowSet") or []
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nba stats %s failed: %s", endpoint, str(exc)[:160])
        return None, None


def load(dataset: str = "player_game_logs", season: int = None):
    """Load one NBA dataset as a DataFrame. Empty frame on any failure.

    dataset: "player_game_logs"  every player's every game, one row each
             "team_game_logs"    every team's every game (pace / opponent work)

    Column names are LOWERCASED at the boundary. stats.nba.com returns SHOUTING
    headers (PTS, FG3M, NBA_FANTASY_PTS) and every consumer in this module reads
    lower case, so the translation happens once here rather than at each call
    site where one missed spelling is a silent KeyError-shaped hole.
    """
    import pandas as pd
    season = season or current_season()
    key = (dataset, season)

    # ── THE IN-PROCESS MEMO EXPIRES FOR THE CURRENT SEASON ───────────────────
    # nfl/client.py has the full account of why this matters: an un-evicted memo
    # pinned whatever the process read FIRST, so a bot running for weeks served
    # a week-1 frame all season and disagreed with a freshly restarted backend
    # about every player. A FINISHED season never changes and stays memoised
    # forever; the current one gets the same TTL the disk cache uses.
    cached = _mem.get(key)
    if cached is not None:
        if season < current_season():
            return cached
        if (time.time() - _mem_at.get(key, 0.0)) < TTL_CURRENT:
            return cached
    _missed = _mem_neg.get(key)
    if _missed is not None and (time.time() - _missed) < NEG_TTL:
        return pd.DataFrame()

    path = _cache_path(dataset, season)
    ttl = TTL_CURRENT if season >= current_season() else TTL_FINISHED
    fresh = (os.path.exists(path)
             and (time.time() - os.path.getmtime(path)) <= ttl)
    if fresh:
        try:
            df = pd.read_parquet(path)
            _mem[key], _mem_at[key] = df, time.time()
            return df
        except Exception as exc:  # noqa: BLE001
            log.warning("nba load: could not read %s: %s", path, str(exc)[:160])

    endpoint = ("playergamelogs" if dataset == "player_game_logs"
                else "teamgamelogs")
    hdr, rows = _stats_get(endpoint, {
        "LeagueID": "00", "Season": season_str(season),
        "SeasonType": "Regular Season",
    })
    if not rows:
        # Log once per retry window, not once per caller — before opening night
        # this fires for every line on the board otherwise.
        if _mem_neg.get(key) is None:
            log.warning("nba load: %s %s returned no rows — suppressing retries "
                        "for %ds (the season may not have tipped off yet)",
                        dataset, season, NEG_TTL)
        _mem_neg[key] = time.time()
        # A stale cache beats nothing: if a previous fetch succeeded, serve it
        # rather than returning empty and making every projection refuse.
        if os.path.exists(path):
            try:
                df = pd.read_parquet(path)
                log.warning("nba load: serving STALE %s %s (%d rows) — the live "
                            "fetch came back empty", dataset, season, len(df))
                return df
            except Exception:  # noqa: BLE001
                pass
        return pd.DataFrame()
    try:
        df = pd.DataFrame(rows, columns=[str(h).lower() for h in hdr])
        if "game_date" in df.columns:
            df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
        try:
            df.to_parquet(path, index=False)
        except Exception as exc:  # noqa: BLE001 — a cache miss is not an error
            log.warning("nba load: could not cache %s: %s", path, str(exc)[:120])
        _mem[key], _mem_at[key] = df, time.time()
        _mem_neg.pop(key, None)     # it exists after all
        log.info("nba load: %s %s -> %d rows, %d player(s)", dataset, season,
                 len(df), df["player_id"].nunique() if "player_id" in df else 0)
        return df
    except Exception as exc:  # noqa: BLE001
        log.warning("nba load: could not build frame for %s %s: %s",
                    dataset, season, str(exc)[:160])
        return pd.DataFrame()


def injuries() -> dict:
    """{normalised player name: status} from ESPN's injury report.

    Feeds the availability gate and the usage-vacuum term — a high-usage
    teammate being OUT is the single largest swing in an NBA prop, and it is
    knowable before tip-off.

    {} on failure, which the caller must treat as "no injury information",
    never as "everyone is healthy".
    """
    try:
        from core import proxy as _px
        r = _px.get(f"{ESPN}/injuries", "nba", headers=_ESPN_HEADERS,
                    timeout=TIMEOUT)
        if r is None:
            log.warning("nba injuries: request failed (proxy and direct)")
            return {}
        r.raise_for_status()
        out = {}
        for team in (r.json() or {}).get("injuries") or []:
            for it in (team.get("injuries") or []):
                ath = (it.get("athlete") or {})
                nm = ath.get("displayName") or ""
                if not nm:
                    continue
                out[_norm_name(nm)] = {
                    "status": (it.get("status") or "").strip(),
                    "team": normalize_team((team.get("abbreviation") or "")),
                    "detail": ((it.get("details") or {}).get("type") or ""),
                }
        log.info("nba injuries: %d player(s) listed", len(out))
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nba injuries failed: %s", str(exc)[:160])
        return {}


def _norm_name(s: str) -> str:
    """Shared name key. Kept here as well as in lines.py because the injury
    report and the book board are matched against each other and a second
    spelling of "normalise" is how half a board silently stops matching."""
    import re
    import unicodedata
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "")
                if not unicodedata.combining(c))
    s = re.sub(r"[^a-z ]", " ", s.lower())
    toks = [t for t in s.split() if t not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(toks).strip()


# ── Schedule and market ──────────────────────────────────────────────────────
def get_schedule(date: str = None) -> list:
    """NBA games on ONE date (YYYYMMDD), WITH the spread and total.

    [{game_id, name, tipoff, state, home, away, home_abbr, away_abbr,
      spread_home, total}]

    ONE DATE, NOT A RANGE, AND THIS IS THE NBA DIFFERENCE FROM NFL. ESPN's
    football scoreboard accepts `dates=YYYYMMDD-YYYYMMDD`; the basketball one
    answers 400 Bad Request to the identical shape. Verified back to back on
    2026-10-02: `dates=20261003` returned the Heat at Raptors, while
    `dates=20261003-20261006` and `dates=20261021-20261023` both 400'd.
    Copying the NFL signature here would have left every scan with an empty
    schedule, every row off-slate, and a board that posts nothing while looking
    like the season simply has not started. See upcoming(), which walks days.

    The spread and total are what the pace/script term reads, so a game without
    odds is returned with them as None and the caller falls back rather than
    inventing a line.
    """
    try:
        params = {"dates": date} if date else {}
        from core import proxy as _px
        r = _px.get(f"{ESPN}/scoreboard", "nba", params=params or None,
                    headers=_ESPN_HEADERS, timeout=TIMEOUT)
        if r is None:
            raise RuntimeError("scoreboard request failed (proxy and direct)")
        r.raise_for_status()
        out = []
        for e in (r.json() or {}).get("events") or []:
            comp = (e.get("competitions") or [{}])[0]
            teams = {c.get("homeAway"): c for c in (comp.get("competitors") or [])}
            home, away = teams.get("home") or {}, teams.get("away") or {}
            odds = (comp.get("odds") or [{}])[0]
            spread_home = odds.get("spread")
            if spread_home is None:
                # ESPN sometimes gives only "BOS -6.5"; parse the favourite out.
                det = (odds.get("details") or "").strip()
                ab = ((home.get("team") or {}).get("abbreviation") or "")
                try:
                    tok, num = det.split()
                    spread_home = float(num) if tok == ab else -float(num)
                except Exception:  # noqa: BLE001
                    spread_home = None
            out.append({
                "game_id": e.get("id"),
                "name": e.get("name"),
                "tipoff": e.get("date"),
                "state": ((comp.get("status") or {}).get("type") or {}).get("state"),
                "home": (home.get("team") or {}).get("displayName"),
                "away": (away.get("team") or {}).get("displayName"),
                "home_abbr": normalize_team(
                    (home.get("team") or {}).get("abbreviation")),
                "away_abbr": normalize_team(
                    (away.get("team") or {}).get("abbreviation")),
                "spread_home": (float(spread_home)
                                if isinstance(spread_home, (int, float, str))
                                and str(spread_home).strip() not in ("", "None")
                                else None),
                "total": (float(odds["overUnder"])
                          if odds.get("overUnder") is not None else None),
            })
        log.info("nba schedule: %d game(s)%s", len(out),
                 f" for {date}" if date else "")
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("nba schedule failed%s: %s",
                    f" for {date}" if date else "", str(exc)[:160])
        return []


def upcoming(days: int = 2) -> list:
    """Games tipping off in the next `days`, pre-game only.

    Pre-game only for the same reason MLB gates on abstract_state and NFL on
    "pre": a projection built from full-game history against a game already in
    the third quarter is not a prediction.

    WALKS ONE DAY AT A TIME because the basketball scoreboard refuses ranges —
    see get_schedule. The default window is DAYS, NOT WEEKS, unlike NFL: the NBA
    plays most nights, so a week-wide window would mix tonight's slate with five
    others and a board titled for today would carry games three days out. Each
    day is a separate request, which is why the window stays small.
    """
    out, seen = [], set()
    today = _dt.date.today()
    for i in range(max(1, int(days) + 1)):
        d = today + _dt.timedelta(days=i)
        for g in get_schedule(d.strftime("%Y%m%d")):
            gid = g.get("game_id")
            if g.get("state") != "pre" or gid in seen:
                continue
            seen.add(gid)
            out.append(g)
    return out


def last_game_dates(season: int = None) -> dict:
    """{team abbr: [game dates, newest first]} for the season.

    This is what back-to-back detection reads. Derived from the TEAM log rather
    than the player log so a player who sat still has his team's schedule — a
    rested star coming back on the second night of a back-to-back is exactly the
    case that matters, and his own log would not show the first night at all.
    """
    import pandas as pd
    try:
        df = load("team_game_logs", season)
        if not len(df) or "team_abbreviation" not in df.columns:
            return {}
        d = df[["team_abbreviation", "game_date"]].dropna()
        d = d.sort_values("game_date", ascending=False)
        out = {}
        for abbr, grp in d.groupby("team_abbreviation"):
            out[normalize_team(str(abbr))] = [
                x.date() if hasattr(x, "date") else x
                for x in grp["game_date"].tolist()]
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nba last_game_dates failed: %s", str(exc)[:160])
        return {}
