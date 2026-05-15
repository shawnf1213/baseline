"""
Sofascore tennis data client.

Core logic is identical to the pre-flag stable version (sofascore.py).
All requests route through Decodo rotating residential proxies via curl_cffi.
No Playwright. No file caching — uses st.session_state exactly as the original.

Public interface:
  init_session, run_connection_test, load_player_lists,
  search_players, get_player_stats_by_surface,
  get_h2h_summary, get_h2h_stat_avg,
  get_tournament_record_modifier,
  ts_to_date_str, format_h2h_table.
"""

import os
import re
import random
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import streamlit as st
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
BASE_URL = "https://api.sofascore.com/api/v1"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Cache-Control": "no-cache",
}

# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------
_PROXY_HOST  = os.getenv("PROXY_HOST",     "gate.decodo.com")
_PROXY_USER  = os.getenv("PROXY_USERNAME", "")
_PROXY_PASS  = os.getenv("PROXY_PASSWORD", "")
_PROXY_PORTS = [
    int(p.strip())
    for p in os.getenv("PROXY_PORT_LIST", "").split(",")
    if p.strip().isdigit()
]

# One port + one Session for the lifetime of a player search session.
# Only rotated when: new search starts, 407 received, or 403 persists.
current_proxy_port: Optional[int] = None
_used_ports: list = []
_bad_ports:  dict = {}          # port -> timestamp marked bad
_proxy_session   = None         # curl_cffi Session — reused across all requests


def _proxy_ok() -> bool:
    return bool(_PROXY_PORTS and _PROXY_USER and _PROXY_HOST)


def _proxy_url(port: int) -> str:
    return f"http://{_PROXY_USER}:{_PROXY_PASS}@{_PROXY_HOST}:{port}"


def _choose_port() -> Optional[int]:
    """Pick a port that is not recently used and not currently bad."""
    if not _proxy_ok():
        return None
    now = time.time()
    for p in [k for k, t in list(_bad_ports.items()) if now - t > 600]:
        del _bad_ports[p]
    avoid      = set(_used_ports[-2:]) | set(_bad_ports)
    candidates = ([p for p in _PROXY_PORTS if p not in avoid] or
                  [p for p in _PROXY_PORTS if p not in _bad_ports] or
                  list(_PROXY_PORTS))
    return random.choice(candidates)


def _new_session(force_port: bool = True) -> None:
    """
    Create a new curl_cffi Session bound to a proxy port.
    Called once per player search, and on 407 / persistent 403.
    """
    global current_proxy_port, _used_ports, _proxy_session
    if force_port or current_proxy_port is None:
        port = _choose_port()
        current_proxy_port = port
        if port is not None:
            _used_ports = (_used_ports + [port])[-4:]
            logger.info("Proxy port -> %d", port)
    from curl_cffi import requests as cf
    s = cf.Session(impersonate="chrome120")
    s.headers.update(HEADERS)
    if current_proxy_port and _proxy_ok():
        pu = _proxy_url(current_proxy_port)
        s.proxies = {"http": pu, "https": pu}
    _proxy_session = s


def _mark_bad(port: int) -> None:
    """Mark a port bad for 10 min, then immediately rotate to a new session."""
    logger.warning("Port %d marked bad — rotating", port)
    _bad_ports[port] = time.time()
    _new_session(force_port=True)


# ---------------------------------------------------------------------------
# Core HTTP fetch
# ---------------------------------------------------------------------------
def _get(url: str, params: dict = None) -> dict:
    global _proxy_session
    if _proxy_session is None:
        _new_session(force_port=True)

    for attempt in range(3):
        try:
            r = _proxy_session.get(url, params=params, timeout=15)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 407:
                _mark_bad(current_proxy_port)    # rotates session internally
                continue
            if r.status_code == 403:
                logger.warning("403 %s attempt=%d", url, attempt + 1)
                if attempt < 2:
                    time.sleep(30 * (attempt + 1))
                    continue
                # 403 persists — rotate port and give up on this URL
                _new_session(force_port=True)
                return {}
            logger.debug("HTTP %d %s", r.status_code, url)
            return {}
        except Exception as e:
            msg = str(e).lower()
            if any(x in msg for x in ("proxy", "tunnel", "connect", "407")):
                _mark_bad(current_proxy_port)
            logger.debug("Error %s: %s", url, e)
            if attempt == 2:
                return {}
    return {}


# ---------------------------------------------------------------------------
# Session init / connection test
# ---------------------------------------------------------------------------
def init_session() -> None:
    """Pick initial proxy port, create the shared Session, verify connectivity."""
    _new_session(force_port=True)
    if not _proxy_ok():
        logger.warning("No proxy configured — using direct connection")
        logger.info("Sofascore client ready")
        return
    try:
        r = _proxy_session.get("https://ip.decodo.com/json", timeout=15)
        if r.status_code == 407:
            logger.error("Proxy 407 — check credentials in .env")
        elif r.status_code == 200:
            info    = r.json()
            ext_ip  = (info.get("proxy") or {}).get("ip") or info.get("ip") or "?"
            country = (info.get("country") or {}).get("name") or ""
            logger.info("Proxy OK -> %s (%s) port=%d", ext_ip, country, current_proxy_port)
        else:
            logger.warning("Proxy health check HTTP %d", r.status_code)
    except Exception as e:
        logger.warning("Proxy health check failed: %s", e)
    logger.info("Sofascore client ready (port=%s)", current_proxy_port)


def run_connection_test() -> None:
    logger.info("=== connection test ===")
    resp = _get(f"{BASE_URL}/search/all", {"q": "Sinner"})
    results = resp.get("results", [])
    tennis  = [r for r in results
               if r.get("entity", {}).get("sport", {}).get("name", "").lower() == "tennis"]
    logger.info("tennis players found: %d", len(tennis))
    for t in tennis[:2]:
        e = t.get("entity", {})
        logger.info("  id=%-8s  %s", e.get("id"), e.get("name"))


def load_player_lists() -> None:
    logger.info("Sofascore: no startup player list needed")


# ---------------------------------------------------------------------------
# Surface inference (keyword-based — stable, works from tournament name)
# ---------------------------------------------------------------------------
HARD_KEYWORDS = [
    "us open", "australian open", "indian wells", "miami", "cincinnati",
    "canada", "montreal", "toronto", "vienna", "basel", "rotterdam",
    "doha", "dubai", "atp finals", "nitto", "paris masters",
    "washington", "beijing", "shanghai", "astana", "metz", "antwerp",
    "stockholm", "moscow", "sofia", "memphis", "delray", "hard", "indoor",
]
CLAY_KEYWORDS = [
    "roland garros", "french open", "monte carlo", "barcelona", "rome",
    "hamburg", "geneva", "lyon", "budapest", "estoril", "munich",
    "madrid", "rio", "buenos aires", "sao paulo", "marrakech",
    "bastad", "gstaad", "umag", "kitzbuhel", "bucharest",
    "clay", "terre battue",
]
GRASS_KEYWORDS = [
    "wimbledon", "queen", "queens", "halle", "stuttgart",
    "eastbourne", "nottingham", "newport", "'s-hertogenbosch", "rosmalen",
    "grass",
]


def _infer_surface(tournament_name: str) -> str:
    name = tournament_name.lower()
    for kw in GRASS_KEYWORDS:
        if kw in name:
            return "Grass"
    for kw in CLAY_KEYWORDS:
        if kw in name:
            return "Clay"
    for kw in HARD_KEYWORDS:
        if kw in name:
            return "Hard"
    return "Hard"


# ---------------------------------------------------------------------------
# Stat parsing helpers
# ---------------------------------------------------------------------------
def _parse_fraction_pct(value_str: str) -> Optional[float]:
    """Parse percentage from '56/94 (60%)' or '75%' or plain number."""
    if value_str is None:
        return None
    s = str(value_str).strip()
    m = re.search(r'\((\d+(?:\.\d+)?)%\)', s)
    if m:
        return float(m.group(1))
    m = re.search(r'^(\d+(?:\.\d+)?)%$', s)
    if m:
        return float(m.group(1))
    m = re.match(r'^(\d+)/(\d+)$', s)
    if m:
        n, d = float(m.group(1)), float(m.group(2))
        return (n / d * 100) if d > 0 else None
    try:
        return float(s)
    except ValueError:
        return None


# Sofascore stat label -> (internal_key, is_fraction_pct)
STAT_MAP = {
    "aces":                       ("aces",                        False),
    "double faults":               ("double_faults",               False),
    "first serve":                 ("first_serve_pct",             True),
    "first serve points":          ("first_serve_pts_won",         True),
    "second serve points":         ("second_serve_pts_won",        True),
    "first serve return points":   ("return_first_serve_pts_won",  True),
    "second serve return points":  ("return_second_serve_pts_won", True),
    "break points converted":      ("bp_converted_count",          False),
    "break points saved":          ("bp_saved",                    True),
    "service games played":        ("service_games",               False),
    "return games played":         ("return_games",                False),
    "service points won":          ("service_pts_won",             False),
    "receiver points won":         ("return_pts_won_count",        False),
    "total won":                   ("total_games_won",             False),
    "tiebreaks":                   ("tiebreaks",                   False),
}

THREE_YEARS_SECS = 3 * 365 * 24 * 3600


def _build_score_str(event: dict) -> str:
    home_sc = event.get("homeScore", {})
    away_sc = event.get("awayScore", {})
    sets = []
    for key in ["period1", "period2", "period3", "period4", "period5"]:
        h = home_sc.get(key)
        a = away_sc.get(key)
        if h is not None and a is not None:
            sets.append(f"{h}-{a}")
        else:
            break
    return " ".join(sets)


def _calc_total_match_games(event: dict) -> Optional[int]:
    """
    Sum games played by both players across all sets.
    e.g. 6-3 6-4 → (6+3) + (6+4) = 19
         7-6 6-4 → (7+6) + (6+4) = 23   (tiebreak = 1 game, so 7+6=13 ✓)
    Returns None if no period scores are present.
    """
    home_sc = event.get("homeScore", {})
    away_sc = event.get("awayScore", {})
    total = 0
    found_any = False
    for key in ["period1", "period2", "period3", "period4", "period5"]:
        h = home_sc.get(key)
        a = away_sc.get(key)
        if h is not None and a is not None:
            total += int(h) + int(a)
            found_any = True
        else:
            break
    return total if found_any else None


def _parse_match_stats(stats_data: dict, event: dict, player_id: int) -> Optional[dict]:
    statistics = stats_data.get("statistics", [])
    if not statistics:
        return None

    all_period = next((p for p in statistics if p.get("period") == "ALL"), None)
    if not all_period:
        all_period = statistics[0]

    home_id = event.get("homeTeam", {}).get("id")
    side     = "home" if home_id == player_id else "away"
    opp_side = "away" if side == "home" else "home"

    home_score = event.get("homeScore", {}).get("current", 0) or 0
    away_score = event.get("awayScore", {}).get("current", 0) or 0
    won = (home_score > away_score) if side == "home" else (away_score > home_score)

    opp_team = event.get("awayTeam", {}) if side == "home" else event.get("homeTeam", {})

    result = {
        "won":           won,
        "surface":       _infer_surface(event.get("tournament", {}).get("name", "")),
        "tournament":    event.get("tournament", {}).get("name", "Unknown"),
        "timestamp":     event.get("startTimestamp", 0),
        "event_id":      event.get("id"),
        "opponent_name": opp_team.get("name", "Unknown"),
    }

    opp_bp_faced = None

    for group in all_period.get("groups", []):
        for item in group.get("statisticsItems", []):
            name_lower = item.get("name", "").lower().strip()
            if name_lower not in STAT_MAP:
                continue
            internal_key, is_pct = STAT_MAP[name_lower]

            if is_pct:
                raw_str = item.get(side, "")
                val = _parse_fraction_pct(raw_str)
                if internal_key == "bp_saved":
                    # Also store how many BPs the player faced on their serve (denominator)
                    frac_m = re.match(r"(\d+)/(\d+)", str(raw_str))
                    if frac_m:
                        result["bp_faced_count"] = float(frac_m.group(2))
            elif internal_key == "bp_converted_count":
                # Sofascore returns percentage in {side}Value; extract count from fraction string
                # e.g. "3/5 (60%)" → 3
                raw_str = item.get(side, "")
                frac_m = re.match(r"(\d+)/(\d+)", str(raw_str))
                if frac_m:
                    val = float(frac_m.group(1))
                else:
                    # plain number fallback
                    try:
                        val = float(str(raw_str).strip()) if raw_str else None
                    except (ValueError, TypeError):
                        val = None
            else:
                val = item.get(f"{side}Value")
                if val is not None:
                    try:
                        val = float(val)
                    except (ValueError, TypeError):
                        val = None

            if val is not None:
                result[internal_key] = val

            if name_lower == "break points saved":
                opp_raw = item.get(opp_side, "")
                m = re.match(r"(\d+)/(\d+)", str(opp_raw))
                if m:
                    opp_bp_faced = float(m.group(2))

    bp_conv_count = result.get("bp_converted_count")
    if bp_conv_count is not None and opp_bp_faced and opp_bp_faced > 0:
        result["bp_converted"] = bp_conv_count / opp_bp_faced * 100
    elif bp_conv_count is not None and result.get("return_games") and result["return_games"] > 0:
        result["bp_converted"] = bp_conv_count / result["return_games"] * 100

    return result


# ---------------------------------------------------------------------------
# Event fetching
# ---------------------------------------------------------------------------
def _get_player_recent_events(player_id: int, num_pages: int = 5) -> list:
    cache_key = f"ss_events_{player_id}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    all_events = []
    for page in range(num_pages):
        data   = _get(f"{BASE_URL}/team/{player_id}/events/last/{page}")
        events = data.get("events", [])
        if not events:
            break
        all_events.extend(events)

    st.session_state[cache_key] = all_events
    return all_events


def _get_event_statistics(event_id: int) -> dict:
    cache_key = f"ss_stats_{event_id}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]
    data = _get(f"{BASE_URL}/event/{event_id}/statistics")
    st.session_state[cache_key] = data
    return data


def _fetch_stats_parallel(event_ids: list) -> dict:
    """
    Fetch event statistics for multiple events concurrently (up to 10 at once).
    Returns {event_id: stats_dict}. Checks st.session_state cache before any request.
    Each thread uses its own curl_cffi Session sharing the current sticky proxy port.
    Results are collected in the main thread before updating session state.
    """
    results: dict = {}
    uncached: list = []

    # Fix 4 — cache check before any network activity
    for eid in event_ids:
        cached = st.session_state.get(f"ss_stats_{eid}")
        if cached is not None:
            results[eid] = cached
        else:
            uncached.append(eid)

    if not uncached:
        return results

    port = current_proxy_port  # read once — all threads share same sticky port

    def _fetch_one(event_id: int):
        from curl_cffi import requests as cf
        s = cf.Session(impersonate="chrome120")
        s.headers.update(HEADERS)
        if port and _proxy_ok():
            pu = _proxy_url(port)
            s.proxies = {"http": pu, "https": pu}
        try:
            r = s.get(f"{BASE_URL}/event/{event_id}/statistics", timeout=15)
            if r.status_code == 200:
                return event_id, r.json()
        except Exception:
            pass
        return event_id, {}

    with ThreadPoolExecutor(max_workers=10) as ex:
        for eid, data in ex.map(_fetch_one, uncached):
            results[eid] = data
            # Update session state from main thread (map blocks until all done)
            st.session_state[f"ss_stats_{eid}"] = data

    return results


# ---------------------------------------------------------------------------
# Aggregation helper
# ---------------------------------------------------------------------------
def _agg(matches: list) -> dict:
    if not matches:
        return {"matches_played": 0}
    numeric_keys = [
        "aces", "double_faults", "first_serve_pct",
        "first_serve_pts_won", "second_serve_pts_won",
        "return_first_serve_pts_won", "return_second_serve_pts_won",
        "bp_converted", "bp_saved", "total_games_won", "total_match_games",
        "service_pts_won", "return_pts_won_count",
        "bp_converted_count", "bp_faced_count",
    ]
    wins = sum(1 for m in matches if m.get("won", False))
    agg = {
        "matches_played": len(matches),
        "wins":           wins,
        "win_rate":       wins / len(matches) * 100 if matches else 0,
    }
    for key in numeric_keys:
        vals = [m[key] for m in matches if key in m and m[key] is not None]
        agg[key] = sum(vals) / len(vals) if vals else None
    return agg


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def search_players(query: str, tour: str = "ATP") -> list:
    if len(query) < 3:
        return []
    # New search = new sticky proxy session for all subsequent requests
    _new_session(force_port=True)
    data = _get(f"{BASE_URL}/search/all", {"q": query})
    entities = []
    for item in data.get("results", []):
        entity = item.get("entity", {})
        if entity.get("type") != 1:
            continue
        sport = entity.get("sport", {})
        if sport.get("name", "").lower() != "tennis":
            continue
        entities.append(entity)

    gender_pref = "F" if tour.upper() == "WTA" else "M"
    # Hard-filter: only include players whose gender matches the tour (skip unknowns)
    entities = [e for e in entities if e.get("gender") == gender_pref]
    entities.sort(key=lambda x: x.get("ranking") or x.get("teamRank") or 9999)

    out = []
    for e in entities[:5]:
        c = e.get("country") or {}
        country = (c.get("alpha3") or c.get("name") or "") if isinstance(c, dict) else ""
        out.append({
            "id":          e.get("id"),
            "name":        e.get("name") or e.get("shortName") or "",
            "currentRank": e.get("ranking") or e.get("teamRank"),
            "countryAcr":  country,
            "gender":      e.get("gender") or "",
        })
    return out


def get_player_stats_by_surface(player_id, tour: str = "ATP") -> dict:
    pid = int(player_id)
    # v2: includes total_match_games — busts any pre-existing stale cache
    cache_key = f"ss_surface_v2_{pid}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    now = time.time()
    events = _get_player_recent_events(pid, num_pages=3)

    # Filter to finished singles events upfront
    valid: list = []
    for event in events:
        ts = event.get("startTimestamp", 0) or 0
        if ts and ts > now:
            continue
        if event.get("status", {}).get("type", "") not in ("finished", "ended"):
            continue
        ht = event.get("homeTeam", {}).get("name", "")
        at = event.get("awayTeam", {}).get("name", "")
        if "/" in ht or "/" in at:
            continue
        valid.append(event)

    # Fetch stats for the most recent 40 matches only to keep response time reasonable
    event_ids = [e.get("id", 0) for e in valid[:40]]
    stats_map = _fetch_stats_parallel(event_ids)

    # Merge base row data with fetched stats
    all_match_stats: list = []
    for event in valid:
        ts      = event.get("startTimestamp", 0) or 0
        home_id = event.get("homeTeam", {}).get("id")
        side    = "home" if home_id == pid else "away"
        opp     = event.get("awayTeam" if side == "home" else "homeTeam", {})
        hs      = event.get("homeScore", {}).get("current", 0) or 0
        aws     = event.get("awayScore", {}).get("current", 0) or 0
        won     = (hs > aws) if side == "home" else (aws > hs)

        score_str = _build_score_str(event)
        tmg = _calc_total_match_games(event)
        base = {
            "won":               won,
            "surface":           _infer_surface(event.get("tournament", {}).get("name", "")),
            "tournament":        event.get("tournament", {}).get("name", "Unknown"),
            "timestamp":         ts,
            "date":              datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if ts else "",
            "event_id":          event.get("id"),
            "opponent_name":     opp.get("name", "Unknown"),
            "score":             score_str,
            "total_match_games": tmg,
        }

        stats_data = stats_map.get(event.get("id", 0)) or {}
        if stats_data:
            parsed = _parse_match_stats(stats_data, event, pid)
            if parsed:
                base.update(parsed)

        all_match_stats.append(base)

    # Fix 6 — parallel surface aggregation
    def _agg_surface(surf):
        subset = [m for m in all_match_stats if m.get("surface") == surf] if surf else all_match_stats
        return surf or "All", _agg(subset)

    surfaces: dict = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        for label, result in ex.map(_agg_surface, [None, "Hard", "Clay", "Grass"]):
            surfaces[label] = result

    sorted_m = sorted(all_match_stats, key=lambda x: x.get("timestamp", 0), reverse=True)

    # Diagnostic: log period scores for first 3 matches to verify total_match_games extraction
    for i, m in enumerate(sorted_m[:3]):
        logger.info(
            "[TOTAL_GAMES] match %d: score=%r  total_match_games=%s  tournament=%r",
            i + 1, m.get("score"), m.get("total_match_games"), m.get("tournament")
        )

    surfaces["form"] = [
        {
            "won":        m.get("won", False),
            "tournament": m.get("tournament", ""),
            "surface":    m.get("surface", ""),
            "opponent":   m.get("opponent_name", ""),
        }
        for m in sorted_m[:10]
    ]
    surfaces["all_matches"] = sorted_m
    for surf in ("Hard", "Clay", "Grass"):
        surfaces[f"{surf}_matches"] = [m for m in sorted_m if m.get("surface") == surf]

    st.session_state[cache_key] = surfaces  # keyed as ss_surface_v2_{pid}
    return surfaces


def _get_player_events_paged(player_id: int, max_pages: int = 10) -> list:
    """
    Fetch up to max_pages pages of recent events for a player.
    Stops early if a page returns fewer than 10 events or total > 200.
    Uses a separate cache key from the stats cache to allow deeper pagination.
    """
    cache_key = f"ss_events_h2h_{player_id}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    all_events = []
    for page in range(max_pages):
        data   = _get(f"{BASE_URL}/team/{player_id}/events/last/{page}")
        events = data.get("events", [])
        if not events:
            break
        all_events.extend(events)
        if len(events) < 10 or len(all_events) > 200:
            break

    st.session_state[cache_key] = all_events
    return all_events


def get_h2h_summary(tour: str, p1: str, p2: str,
                    surface: Optional[str] = None) -> dict:
    empty = {
        "total": 0, "p1_wins": 0, "p2_wins": 0,
        "surface_matches": 0, "surface_p1_wins": 0, "surface_p2_wins": 0,
        "h2h_rate": 0.5,
        "matches": pd.DataFrame(), "surface_matches_df": pd.DataFrame(),
        "date_range": None, "surface_breakdown": {},
    }
    p1_id = int(p1)
    p2_id = int(p2)

    # Fetch both players' event histories and cross-reference for matches
    p1_events = _get_player_events_paged(p1_id, max_pages=10)
    p2_events = _get_player_events_paged(p2_id, max_pages=10)

    # Build a set of event IDs from p2's history for fast lookup
    p2_event_ids = {e.get("id") for e in p2_events if e.get("id")}

    # Find events appearing in BOTH players' lists where they faced each other
    h2h = []
    seen_ids: set = set()
    for e in p1_events:
        eid = e.get("id")
        if not eid or eid in seen_ids:
            continue
        home_id = e.get("homeTeam", {}).get("id")
        away_id = e.get("awayTeam", {}).get("id")
        if {home_id, away_id} != {p1_id, p2_id}:
            continue
        status = e.get("status", {}).get("type", "")
        if status not in ("finished", "ended"):
            continue
        ht = e.get("homeTeam", {}).get("name", "")
        at = e.get("awayTeam", {}).get("name", "")
        if "/" in ht or "/" in at:
            continue
        # Prefer events confirmed in p2's history too, but don't discard if missing
        seen_ids.add(eid)
        h2h.append(e)

    # Also check p2's events for any matches not yet found via p1
    for e in p2_events:
        eid = e.get("id")
        if not eid or eid in seen_ids:
            continue
        home_id = e.get("homeTeam", {}).get("id")
        away_id = e.get("awayTeam", {}).get("id")
        if {home_id, away_id} != {p1_id, p2_id}:
            continue
        status = e.get("status", {}).get("type", "")
        if status not in ("finished", "ended"):
            continue
        ht = e.get("homeTeam", {}).get("name", "")
        at = e.get("awayTeam", {}).get("name", "")
        if "/" in ht or "/" in at:
            continue
        seen_ids.add(eid)
        h2h.append(e)

    if not h2h:
        return empty

    rows = []
    timestamps = []
    for e in h2h:
        is_p1home = e.get("homeTeam", {}).get("id") == p1_id
        hs  = e.get("homeScore", {}).get("current", 0) or 0
        aws = e.get("awayScore", {}).get("current", 0) or 0
        p1w = (is_p1home and hs > aws) or (not is_p1home and aws > hs)
        ts  = e.get("startTimestamp", 0)
        dt  = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""
        if ts:
            timestamps.append(ts)
        opp = e.get("awayTeam", {}) if is_p1home else e.get("homeTeam", {})
        rows.append({
            "Match Date":  dt,
            "Tournament":  e.get("tournament", {}).get("name", ""),
            "Surface":     _infer_surface(e.get("tournament", {}).get("name", "")),
            "Result":      "W" if p1w else "L",
            "Opponent":    opp.get("name", "Unknown"),
            "Score":       _build_score_str(e),
        })

    total   = len(rows)
    p1_wins = sum(1 for r in rows if r["Result"] == "W")
    surf_rows  = [r for r in rows if r["Surface"] == surface] if surface else []
    surf_total = len(surf_rows)
    surf_p1w   = sum(1 for r in surf_rows if r["Result"] == "W")
    h2h_rate   = (surf_p1w / surf_total if surf_total
                  else p1_wins / total if total else 0.5)

    # Build date_range from earliest to latest year
    date_range = None
    if timestamps:
        years = [datetime.utcfromtimestamp(ts).year for ts in timestamps]
        if min(years) == max(years):
            date_range = str(min(years))
        else:
            date_range = f"{min(years)}–{max(years)}"

    # Build surface breakdown dict: {surface: count}
    surface_breakdown: dict = {}
    for r in rows:
        surf = r["Surface"]
        surface_breakdown[surf] = surface_breakdown.get(surf, 0) + 1

    return {
        "total":               total,
        "p1_wins":             p1_wins,
        "p2_wins":             total - p1_wins,
        "surface_matches":     surf_total,
        "surface_p1_wins":     surf_p1w,
        "surface_p2_wins":     surf_total - surf_p1w,
        "h2h_rate":            h2h_rate,
        "matches":             pd.DataFrame(rows),
        "surface_matches_df":  pd.DataFrame(surf_rows) if surf_rows else pd.DataFrame(),
        "date_range":          date_range,
        "surface_breakdown":   surface_breakdown,
    }


def get_h2h_stat_avg(tour: str, p1: str, p2: str,
                     surface: Optional[str] = None) -> dict:
    empty = {"ace": None, "df": None, "games_avg": None}
    p1_id = int(p1)
    p2_id = int(p2)

    events = _get_player_recent_events(p1_id)
    h2h = [
        e for e in events
        if {e.get("homeTeam", {}).get("id"), e.get("awayTeam", {}).get("id")} == {p1_id, p2_id}
        and e.get("status", {}).get("type", "") in ("finished", "ended")
        and (not surface or _infer_surface(e.get("tournament", {}).get("name", "")) == surface)
    ]

    if not h2h:
        return empty

    ace_sum = df_sum = bp_sum = n = 0
    games_sum = games_n = 0
    for e in h2h:
        # Total match games from period scores — no stats request needed
        tmg = _calc_total_match_games(e)
        if tmg is not None and tmg > 0:
            games_sum += tmg
            games_n   += 1

        stats  = _get_event_statistics(e.get("id", 0))
        parsed = _parse_match_stats(stats, e, p1_id) if stats else None
        if parsed:
            if parsed.get("aces") is not None:
                ace_sum += parsed["aces"]
            if parsed.get("double_faults") is not None:
                df_sum += parsed["double_faults"]
            if parsed.get("bp_converted_count") is not None:
                bp_sum += parsed["bp_converted_count"]
            n += 1

    return {
        "ace":       round(ace_sum  / n,       2) if n       else None,
        "df":        round(df_sum   / n,       2) if n       else None,
        "bp":        round(bp_sum   / n,       2) if n       else None,
        "games_avg": round(games_sum / games_n, 1) if games_n else None,
    }


def get_tournament_record_modifier(player_id: str, tournament_id: str,
                                   tour: str = "ATP") -> float:
    return 0.0


# ---------------------------------------------------------------------------
# Compatibility helpers
# ---------------------------------------------------------------------------
def ts_to_date_str(ts) -> str:
    if not ts:
        return "-"
    try:
        if isinstance(ts, str) and len(ts) >= 10 and ts[4] == "-":
            return ts[:10]
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%b %d %Y")
    except Exception:
        return str(ts)[:10]


def format_h2h_table(df: pd.DataFrame, p1_name: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    expected = {"Match Date", "Tournament", "Surface", "Result", "Opponent", "Score"}
    if expected.issubset(set(df.columns)):
        return df[list(expected)].copy()
    return df.copy()
