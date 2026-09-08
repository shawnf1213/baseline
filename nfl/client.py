"""
NFL data ingest — nflverse for history, ESPN for schedule and market.

TWO SOURCES, EACH FOR WHAT IT IS BEST AT
----------------------------------------
nflverse (github.com/nflverse/nflverse-data) — every play since 1999, weekly
player stats, snap counts, depth charts and injury reports. Free, no key,
actively maintained, and released as parquet. It is the reason this module can
compute usage shares at all.

ESPN's public scoreboard — the upcoming schedule AND the spread/total. The market
line is not decoration here: the game-script mixture weights its scenarios by win
probability, so without a spread there is no mixture, only a mean.

CACHED ON DISK, DELIBERATELY. Play-by-play is 20 MB a season and the season is
over — it does not change. Re-downloading it per projection would make a board
scan take minutes and hammer someone else's free hosting. Weekly files refresh
on a short TTL; finished seasons effectively never.

Every function returns an empty result on failure and never raises (Rule 2).
"""

import datetime as _dt
import logging
import os
import time

log = logging.getLogger("baseline.nfl.client")

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
TIMEOUT = 90

# Cache under the repo, not /tmp: Railway containers keep the filesystem for the
# life of a deploy, so one download serves every scan until the next deploy.
CACHE_DIR = os.getenv("NFL_CACHE_DIR",
                      os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "_cache"))
# A completed season never changes; the current one gets new games weekly.
TTL_CURRENT = int(os.getenv("NFL_CACHE_TTL", str(6 * 3600)) or 6 * 3600)
TTL_FINISHED = 90 * 24 * 3600

_HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/125.0.0.0 Safari/537.36")}

# ESPN NEEDS A BARE USER-AGENT, and this is not a typo. The full Chrome string
# above returns 403 Forbidden on every scoreboard call while a plain
# "Mozilla/5.0" returns 200 — verified back to back on the same URL, same
# second. ESPN appears to treat a complete browser UA on an API endpoint as
# scraping. The failure is total and looks exactly like an outage, so the two
# header sets are kept separate rather than shared.
_ESPN_HEADERS = {"User-Agent": "Mozilla/5.0"}

_mem = {}          # in-process frame cache, keyed by (dataset, season)
# NEGATIVE cache — datasets that are genuinely absent upstream, keyed the same
# way, valued with the time we last tried.
#
# WHY THIS EXISTS: `load()` memoised only SUCCESS, so a dataset that 404s was
# re-downloaded on every single call. That is the normal state of affairs in
# week 1 of a new season — nflverse has published no 2026 file yet — and a
# 374-line board scan calls load() several times per line, so the scan fired
# well over a thousand doomed HTTP requests at GitHub before producing a row.
#
# The retry window is SHORT and deliberately not infinite: the current season's
# file appears mid-season, and a bot process that runs for weeks must pick it up
# without a restart. So an absence is trusted for NEG_TTL and then re-checked.
_mem_neg = {}
NEG_TTL = int(os.getenv("NFL_MISSING_TTL", "900") or 900)   # 15 minutes

# ── TEAM ABBREVIATIONS DIFFER BETWEEN THE TWO SOURCES, AND SILENTLY ──────────
# ESPN says LAR and WSH; nflverse says LA and WAS. Nothing errors when they
# disagree — the ratings lookup simply misses and the opponent adjustment
# defaults to 1.000, so a projection against the Rams or the Commanders quietly
# loses its defensive adjustment while looking completely normal.
#
# Everything is normalised to the NFLVERSE convention at the ingest boundary,
# because that is the side the statistics live on. The extra historical codes
# cost nothing and cover relocations that still appear in older data.
TEAM_ALIASES = {
    "LAR": "LA", "WSH": "WAS", "JAC": "JAX",
    "OAK": "LV", "SD": "LAC", "STL": "LA", "ARZ": "ARI",
    "BLT": "BAL", "CLV": "CLE", "HST": "HOU",
}


def normalize_team(abbr: str) -> str:
    """Team abbreviation in nflverse form. Passes unknown codes through."""
    if not abbr:
        return abbr
    a = str(abbr).strip().upper()
    return TEAM_ALIASES.get(a, a)


def current_season(today: _dt.date = None) -> int:
    """The NFL season a date belongs to.

    A season is named for the year it STARTS, and it runs into February. So
    January and February belong to the previous year's season — getting this
    wrong in February would silently read a season that has not been played.
    """
    today = today or _dt.date.today()
    return today.year - 1 if today.month <= 2 else today.year


def _week_start(season: int, week: int) -> _dt.date:
    """Kickoff date of a given NFL week — the Thursday after Labor Day, +7/week."""
    sep1 = _dt.date(season, 9, 1)
    labor_day = sep1 + _dt.timedelta(days=(7 - sep1.weekday()) % 7)
    return labor_day + _dt.timedelta(days=3 + 7 * (max(1, int(week)) - 1))


def current_week(today: _dt.date = None) -> int:
    """Which NFL week a date falls in, 1-18.

    Week 1 opens on the Thursday after Labor Day (the first Monday in
    September), so the season start is derived rather than hardcoded to a date
    that would silently drift a year later. Clamped to [1, 18]: the postseason
    is not a week this module prices, and a preseason date reads as week 1.
    """
    today = today or _dt.date.today()
    year = current_season(today)
    sep1 = _dt.date(year, 9, 1)
    labor_day = sep1 + _dt.timedelta(days=(7 - sep1.weekday()) % 7)  # first Monday
    kickoff = labor_day + _dt.timedelta(days=3)                      # Thursday
    if today < kickoff:
        return 1
    return max(1, min(18, ((today - kickoff).days // 7) + 1))


def _cache_path(dataset: str, season: int, ext: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{dataset}_{season}.{ext}")


def _download(url: str, path: str) -> bool:
    """Stream a release asset to disk. False on failure; never raises."""
    import requests
    try:
        tmp = path + ".part"
        with requests.get(url, headers=_HEADERS, timeout=TIMEOUT,
                          stream=True) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
        os.replace(tmp, path)          # atomic: a killed download never
        return True                    # leaves a truncated file behind
    except Exception as exc:  # noqa: BLE001
        log.warning("nfl download failed (%s): %s", url, str(exc)[:160])
        try:
            if os.path.exists(path + ".part"):
                os.remove(path + ".part")
        except OSError:
            pass
        return False


def load(dataset: str, season: int = None, ext: str = "parquet"):
    """Load one nflverse dataset as a DataFrame. Empty frame on any failure.

    dataset: the release tag's file stem, e.g. "play_by_play", "stats_player_week",
             "snap_counts", "depth_charts", "injuries", "roster_weekly".
    """
    import pandas as pd
    season = season or current_season()
    key = (dataset, season, ext)
    if key in _mem:
        return _mem[key]
    # Known-absent and still inside the retry window — return empty without
    # touching the network. See _mem_neg.
    _missed = _mem_neg.get(key)
    if _missed is not None and (time.time() - _missed) < NEG_TTL:
        return pd.DataFrame()
    tag = {
        "play_by_play": "pbp",
        "stats_player_week": "stats_player",
        "stats_player_reg": "stats_player",
        "stats_team_week": "stats_team",
        "snap_counts": "snap_counts",
        "depth_charts": "depth_charts",
        "injuries": "injuries",
        "roster_weekly": "weekly_rosters",
        "roster": "rosters",
    }.get(dataset, dataset)
    path = _cache_path(dataset, season, ext)
    ttl = TTL_CURRENT if season >= current_season() else TTL_FINISHED
    stale = (not os.path.exists(path)
             or (time.time() - os.path.getmtime(path)) > ttl)
    if stale:
        url = f"{NFLVERSE}/{tag}/{dataset}_{season}.{ext}"
        if not _download(url, path) and not os.path.exists(path):
            # Log once per retry window, not once per caller — at week 1 this
            # fires for every player on the board otherwise.
            if _mem_neg.get(key) is None:
                log.warning("nfl load: %s %s unavailable — suppressing retries "
                            "for %ds", dataset, season, NEG_TTL)
            _mem_neg[key] = time.time()
            return pd.DataFrame()
    try:
        df = (pd.read_parquet(path) if ext == "parquet"
              else pd.read_csv(path, low_memory=False))
        _mem[key] = df
        _mem_neg.pop(key, None)     # it exists after all
        log.info("nfl load: %s %s -> %d rows", dataset, season, len(df))
        return df
    except Exception as exc:  # noqa: BLE001
        log.warning("nfl load: could not read %s: %s", path, str(exc)[:160])
        return pd.DataFrame()


# ── Schedule and market ──────────────────────────────────────────────────────
def get_schedule(start: str = None, end: str = None) -> list:
    """Upcoming NFL games WITH the spread and total.

    [{game_id, name, kickoff, state, home, away, home_abbr, away_abbr,
      spread_home, total, favorite}]

    The spread and total are what the game-script mixture weights its scenarios
    by, so a game without odds is returned with them as None and the caller must
    fall back to an unweighted projection rather than inventing a line.
    """
    import requests
    try:
        params = {}
        if start:
            params["dates"] = f"{start}-{end}" if end else start
        r = requests.get(f"{ESPN}/scoreboard", params=params or None,
                         headers=_ESPN_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        out = []
        for e in (r.json() or {}).get("events") or []:
            comp = (e.get("competitions") or [{}])[0]
            teams = {c.get("homeAway"): c for c in (comp.get("competitors") or [])}
            home, away = teams.get("home") or {}, teams.get("away") or {}
            odds = (comp.get("odds") or [{}])[0]
            spread_home = odds.get("spread")
            if spread_home is None:
                # ESPN sometimes gives only "SEA -3.5"; parse the favourite out.
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
                "kickoff": e.get("date"),
                "state": ((comp.get("status") or {}).get("type") or {}).get("state"),
                "home": (home.get("team") or {}).get("displayName"),
                "away": (away.get("team") or {}).get("displayName"),
                "home_abbr": normalize_team(
                    (home.get("team") or {}).get("abbreviation")),
                "away_abbr": normalize_team(
                    (away.get("team") or {}).get("abbreviation")),
                "spread_home": spread_home,
                "total": odds.get("overUnder"),
            })
        log.info("nfl schedule: %d game(s)%s", len(out),
                 f" for {params.get('dates')}" if params else "")
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("nfl schedule failed: %s", str(exc)[:160])
        return []


def upcoming_week(days: int = 8) -> list:
    """Games kicking off in the next `days`, pre-game only.

    Pre-game only for the same reason MLB gates on abstract_state: a projection
    built from full-game history against a game already in the third quarter is
    not a prediction.
    """
    today = _dt.date.today()
    end = today + _dt.timedelta(days=days)
    games = get_schedule(today.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    out = [g for g in games if g.get("state") == "pre"]
    if out:
        return out
    # ESPN refuses datacenter IPs. It answers fine from a laptop and returns
    # 403 Forbidden from the Railway container, so /nflgame, /nflspread and the
    # board's whole game-script term worked in every local test and were dead in
    # production — the same shape of bug as the missing parquet engine.
    #
    # nflverse's games.csv carries the identical fixtures WITH the spread and
    # total (272 unplayed 2026 games, 112 already priced), is a plain CSV over
    # GitHub, and is already downloaded for /nflh2h. So it is a real second
    # source rather than a degraded one.
    return schedule_from_games_csv(today, end)


GAMES_CSV = ("https://github.com/nflverse/nflverse-data/releases/download/"
             "schedules/games.csv")
_games_csv_cache = {}


def schedule_from_games_csv(start: _dt.date, end: _dt.date) -> list:
    """Upcoming fixtures from nflverse, shaped exactly like get_schedule()."""
    import pandas as pd
    try:
        if "df" not in _games_csv_cache:
            _games_csv_cache["df"] = pd.read_csv(GAMES_CSV, low_memory=False)
        df = _games_csv_cache["df"]
        d = df[df["home_score"].isna()].copy()
        d["_day"] = pd.to_datetime(d["gameday"], errors="coerce").dt.date
        d = d[(d["_day"] >= start) & (d["_day"] <= end)]
        out = []
        for _, r in d.iterrows():
            sl = r.get("spread_line")
            # SIGN FLIP. games.csv states the spread POSITIVE-means-home-favoured;
            # get_schedule (ESPN) and every consumer of this function state it
            # NEGATIVE-means-favoured. Returning the raw number here would invert
            # every game script on the board — favourites priced as underdogs.
            spread_home = (-float(sl) if isinstance(sl, (int, float))
                           and not pd.isna(sl) else None)
            tot = r.get("total_line")
            tot = float(tot) if isinstance(tot, (int, float)) and not pd.isna(tot) else None
            ha, aa = str(r.get("home_team") or ""), str(r.get("away_team") or "")
            out.append({
                "game_id": str(r.get("game_id") or ""),
                "name": f"{aa} at {ha}",
                "kickoff": f"{r.get('gameday')}T{r.get('gametime') or '00:00'}",
                "state": "pre",
                "home": ha, "away": aa,
                "home_abbr": normalize_team(ha), "away_abbr": normalize_team(aa),
                "spread_home": spread_home, "total": tot,
                "favorite": (ha if (spread_home or 0) < 0 else aa
                             if spread_home is not None else None),
                "source": "nflverse",
            })
        log.warning("nfl schedule: ESPN unavailable — using nflverse games.csv "
                    "(%d fixture(s))", len(out))
        return out
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl schedule fallback failed: %s", exc)
        return []
