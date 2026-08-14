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


def current_season(today: _dt.date = None) -> int:
    """The NFL season a date belongs to.

    A season is named for the year it STARTS, and it runs into February. So
    January and February belong to the previous year's season — getting this
    wrong in February would silently read a season that has not been played.
    """
    today = today or _dt.date.today()
    return today.year - 1 if today.month <= 2 else today.year


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
            log.warning("nfl load: %s %s unavailable", dataset, season)
            return pd.DataFrame()
    try:
        df = (pd.read_parquet(path) if ext == "parquet"
              else pd.read_csv(path, low_memory=False))
        _mem[key] = df
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
                "home_abbr": (home.get("team") or {}).get("abbreviation"),
                "away_abbr": (away.get("team") or {}).get("abbreviation"),
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
    return [g for g in games if g.get("state") == "pre"]
