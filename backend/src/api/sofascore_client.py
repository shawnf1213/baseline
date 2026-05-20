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
    # Challenger & ITF clay venues
    "mexico city", "bogota", "lima", "santiago", "cordoba", "cherbourg",
    "oeiras", "prostejov", "poznan", "braunschweig", "salzburg",
    "tampere", "istanbul", "casablanca", "tunis", "cairo",
    "perugia", "parma", "banja luka", "santa fe", "morelos",
    # Additional Challenger clay venues
    "valencia", "biella", "maia", "braga", "bagnoles", "andrezieux",
    "leon", "guadalajara", "concepcion", "ortisei", "bergamo",
    "lugano", "olbia", "savona", "porto", "lagos", "tlemcen",
    "hammamet", "sfax", "monastir", "rabat", "fes", "agadir",
    "marrakech", "algier", "cairo", "sharm", "luxor",
    "szczecin", "bydgoszcz", "wroclaw", "krakow", "lodz",
    "norrkoping", "bastad", "manerbio", "como", "piacenza",
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


# Sofascore groundType numeric codes → surface name
_GROUND_TYPE_MAP: dict = {
    1: "Hard",   # outdoor hard
    2: "Clay",
    3: "Grass",
    4: "Hard",   # carpet (treat as hard)
    5: "Hard",   # indoor hard
    "hard": "Hard",
    "clay": "Clay",
    "grass": "Grass",
    "carpet": "Hard",
    "indoor": "Hard",
}


def _infer_surface_from_event(event: dict, log_missing: bool = False) -> str:
    """
    Try Sofascore native groundType fields first (numeric or string),
    then fall back to keyword matching on the tournament name.

    Checks multiple field paths including Challenger-specific locations:
      event.groundType
      event.tournament.groundType
      event.tournament.uniqueTournament.groundType
      event.uniqueTournament.groundType           ← top-level (Challenger events)
      event.tournament.category.groundType        ← category level
      event.uniqueTournament.groundTypeEnum       ← enum string (some Challenger events)
      event.tournament.uniqueTournament.groundTypeEnum
    """
    tournament = event.get("tournament") or {}
    unique_t   = tournament.get("uniqueTournament") or {}
    top_unique = event.get("uniqueTournament") or {}
    category   = tournament.get("category") or {}

    candidates = (
        event.get("groundType"),
        tournament.get("groundType"),
        unique_t.get("groundType"),
        top_unique.get("groundType"),
        category.get("groundType"),
        # groundTypeEnum — additional field path used in some Challenger events
        unique_t.get("groundTypeEnum"),
        top_unique.get("groundTypeEnum"),
    )

    for gt_raw in candidates:
        if gt_raw is None:
            continue
        if isinstance(gt_raw, int):
            mapped = _GROUND_TYPE_MAP.get(gt_raw)
            if mapped:
                return mapped
        elif isinstance(gt_raw, str):
            # Try integer parse first — Sofascore sometimes sends "1"/"2"/"3" as strings
            try:
                mapped = _GROUND_TYPE_MAP.get(int(gt_raw))
                if mapped:
                    return mapped
            except (ValueError, TypeError):
                pass
            mapped = _GROUND_TYPE_MAP.get(gt_raw.lower())
            if mapped:
                return mapped
        elif isinstance(gt_raw, dict):
            # Some API versions nest as {"name": "clay"}
            name_val = (gt_raw.get("name") or "").lower()
            mapped = _GROUND_TYPE_MAP.get(name_val)
            if mapped:
                return mapped

    # Fell through — keyword matching on all available name fields
    tourn_name = " ".join(filter(None, [
        tournament.get("name", ""),
        unique_t.get("name", ""),
        top_unique.get("name", ""),
    ]))
    surface = _infer_surface(tourn_name)
    if log_missing:
        logger.debug(
            "SURFACE_FALLBACK | event_id=%s | tourn=%r | "
            "gt_candidates=%s | inferred=%s",
            event.get("id"), tourn_name,
            [c for c in candidates if c is not None],
            surface,
        )
    return surface


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

# Years considered "recent 3-year window" — updated each calendar year
_RECENT_YEARS = {2023, 2024, 2025, 2026}

def _year_from_ts(ts: int) -> int:
    """Extract 4-digit year from Unix timestamp, or 0 if unknown."""
    if not ts:
        return 0
    try:
        return datetime.utcfromtimestamp(ts).year
    except Exception:
        return 0


# Stat keys used for per-match averages in _agg_split
_SPLIT_NUMERIC_KEYS = [
    "aces", "double_faults", "first_serve_pct",
    "first_serve_pts_won", "second_serve_pts_won",
    "return_first_serve_pts_won", "return_second_serve_pts_won",
    "bp_converted", "bp_saved", "total_match_games",
    "bp_converted_count", "bp_faced_count",
]


def _agg_split(all_m: list, stat_m: list) -> dict:
    """
    Split aggregation: win_rate uses all_m (all finished matches including
    stat-poor ones), stat averages use stat_m (only stat-rich matches).

    This ensures win rate reflects real match outcomes even for Challenger
    events where Sofascore's stats API returns empty data.
    """
    if not all_m and not stat_m:
        return {"matches_played": 0, "stat_matches": 0}
    wins = sum(1 for m in all_m if m.get("won", False))
    result: dict = {
        "matches_played": len(all_m),
        "stat_matches":   len(stat_m),
        "wins":           wins,
        "win_rate":       round(wins / len(all_m) * 100, 2) if all_m else 0,
    }
    for key in _SPLIT_NUMERIC_KEYS:
        vals = [m[key] for m in stat_m if key in m and m[key] is not None]
        result[key] = round(sum(vals) / len(vals), 4) if vals else None
    return result


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

    # NOTE: surface intentionally omitted — caller (get_player_stats_by_surface)
    # sets surface via _infer_surface_from_event (groundType-aware) and we must
    # not overwrite it here with the weaker keyword-only inference.
    result = {
        "won":           won,
        "tournament":    event.get("tournament", {}).get("name", "Unknown"),
        "timestamp":     event.get("startTimestamp", 0),
        "event_id":      event.get("id"),
        "opponent_name": opp_team.get("name", "Unknown"),
    }

    opp_bp_faced = None

    # ── Collect stat items — handle both nested (groups) and flat structures ──
    # ATP/WTA: statistics[0].groups[n].statisticsItems
    # Some Challenger events: statistics[0].statisticsItems  (no groups level)
    all_items: list = []
    for group in all_period.get("groups", []):
        all_items.extend(group.get("statisticsItems", []))
    if not all_items:
        # Flat structure fallback (Challenger / some tournament levels)
        all_items = all_period.get("statisticsItems", [])
        if all_items:
            logger.debug(
                "FLAT_STATS | event_id=%s | tourn=%r | items=%d",
                event.get("id"),
                event.get("tournament", {}).get("name", ""),
                len(all_items),
            )

    for item in all_items:
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
            # Sofascore returns "3/5 (60%)" — extract numerator count
            raw_str = item.get(side, "")
            frac_m = re.match(r"(\d+)/(\d+)", str(raw_str))
            if frac_m:
                val = float(frac_m.group(1))
            else:
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

        # Capture opponent aces so we can compute ace-against-per-match from SS data
        if name_lower == "aces":
            opp_ace_val = item.get(f"{opp_side}Value")
            if opp_ace_val is not None:
                try:
                    result["opp_aces"] = float(opp_ace_val)
                except (ValueError, TypeError):
                    pass

        if name_lower == "break points saved":
            opp_raw = item.get(opp_side, "")
            m = re.match(r"(\d+)/(\d+)", str(opp_raw))
            if m:
                opp_bp_faced = float(m.group(2))

    # If no stat items were parsed at all, return None — caller will skip this match
    stat_keys = {"aces", "double_faults", "bp_converted_count", "first_serve_pts_won"}
    if not any(k in result for k in stat_keys):
        logger.warning(
            "STATS_NO_KEYS | event_id=%s | tourn=%r | all_items=%d | "
            "returning None (match excluded from surface log)",
            event.get("id"),
            event.get("tournament", {}).get("name", ""),
            len(all_items),
        )
        return None

    bp_conv_count = result.get("bp_converted_count")
    if bp_conv_count is not None and opp_bp_faced and opp_bp_faced > 0:
        result["bp_converted"] = bp_conv_count / opp_bp_faced * 100
    elif bp_conv_count is not None and result.get("return_games") and result["return_games"] > 0:
        result["bp_converted"] = bp_conv_count / result["return_games"] * 100

    return result


# ---------------------------------------------------------------------------
# Event fetching
# ---------------------------------------------------------------------------
def _fetch_event_page(player_id: int, page: int) -> list:
    """Fetch one page of a player's event history. Returns list (may be empty)."""
    data = _get(f"{BASE_URL}/team/{player_id}/events/last/{page}")
    return data.get("events", [])


MAX_PAGES_DEFAULT = 50    # fetch up to 50 pages (~500 events) — covers full career history


def _get_player_recent_events(player_id: int, max_pages: int = MAX_PAGES_DEFAULT) -> list:
    """
    Fetch ALL available pages of a player's event history in parallel batches of 5.

    Strategy:
    - Fetch pages in batches of 5 concurrently.
    - Stop only when a batch returns no events (end of history) or max_pages reached.
    - No early-stop on surface match count — we want the full career history so
      all-time surface aggregations are accurate.
    - PAGE_SCAN lines logged per batch so Railway shows pagination progress.
    """
    cache_key = f"ss_events_v2_{player_id}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    all_events: list = []
    now = time.time()
    page = 0
    batch_num = 0

    while page < max_pages:
        batch = list(range(page, min(page + 5, max_pages)))
        if not batch:
            break

        # Fetch batch in parallel
        page_results: dict = {}
        with ThreadPoolExecutor(max_workers=len(batch)) as ex:
            fut_map = {ex.submit(_fetch_event_page, player_id, p): p for p in batch}
            for fut in as_completed(fut_map):
                page_results[fut_map[fut]] = fut.result()

        got_any = False
        for p in sorted(batch):
            evts = page_results.get(p, [])
            surface_this_page: dict = {}
            for e in evts:
                ts = e.get("startTimestamp", 0) or 0
                if ts and ts > now:
                    continue
                if e.get("status", {}).get("type", "") not in ("finished", "ended"):
                    continue
                ht = e.get("homeTeam", {}).get("name", "")
                at = e.get("awayTeam", {}).get("name", "")
                if "/" in ht or "/" in at:
                    continue
                surf = _infer_surface_from_event(e)
                surface_this_page[surf] = surface_this_page.get(surf, 0) + 1

            logger.info(
                "PAGE_SCAN | player_id=%s | page=%d | total_events=%d | surface_counts=%s",
                player_id, p, len(evts), surface_this_page,
            )
            all_events.extend(evts)
            if evts:
                got_any = True

        page += len(batch)
        batch_num += 1

        if not got_any:
            logger.info(
                "PAGE_SCAN | player_id=%s | empty batch at batch=%d page=%d — end of history",
                player_id, batch_num, page,
            )
            break   # end of history

        logger.info(
            "PAGE_SCAN | player_id=%s | cumulative_events=%d | batches_fetched=%d",
            player_id, len(all_events), batch_num,
        )

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
        url = f"{BASE_URL}/event/{event_id}/statistics"
        for attempt in range(2):
            try:
                r = s.get(url, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    # Challenger events sometimes return empty statistics on first hit —
                    # retry once after 500 ms before giving up.
                    if data.get("statistics"):
                        return event_id, data
            except Exception:
                pass
            if attempt == 0:
                time.sleep(0.5)
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
    # v6: full-history pagination, SS aggregation tiers, ace-against extraction
    cache_key = f"ss_surface_v6_{pid}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    now = time.time()
    events = _get_player_recent_events(pid, max_pages=MAX_PAGES_DEFAULT)

    # Filter to finished singles events; log surface detection for debugging
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

    # EVENT_DEBUG: log tournament → surface mapping for first 20 events so we
    # can confirm Challenger events are detected with the right surface.
    for event in valid[:20]:
        tournament  = event.get("tournament") or {}
        unique_t    = tournament.get("uniqueTournament") or {}
        top_unique  = event.get("uniqueTournament") or {}
        category    = tournament.get("category") or {}
        surf_detected = _infer_surface_from_event(event, log_missing=False)
        logger.info(
            "EVENT_DEBUG | id=%s | name=%r | "
            "gt_event=%s | gt_tourn=%s | gt_uniq=%s | gt_top_uniq=%s | gt_cat=%s | "
            "category_id=%s | surface=%s",
            event.get("id"),
            tournament.get("name", ""),
            event.get("groundType"),
            tournament.get("groundType"),
            unique_t.get("groundType"),
            top_unique.get("groundType"),
            category.get("groundType"),
            category.get("id"),
            surf_detected,
        )

    # Fetch stats for the most recent 50 matches in parallel
    event_ids = [e.get("id", 0) for e in valid[:50]]
    stats_map = _fetch_stats_parallel(event_ids)

    # Build per-match records.
    # all_match_stats  — every finished single (for form/display/win-rate).
    # stat_matches     — only matches where statistics parsed successfully;
    #                    these drive the stat averages and "Matches" count shown in cards.
    all_match_stats: list = []
    stat_matches:    list = []
    _logged_first = False

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
        # Use native groundType field first; fall back to keyword matching
        surface_val = _infer_surface_from_event(event)

        base = {
            "won":               won,
            "surface":           surface_val,
            "tournament":        event.get("tournament", {}).get("name", "Unknown"),
            "timestamp":         ts,
            "date":              datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if ts else "",
            "event_id":          event.get("id"),
            "opponent_name":     opp.get("name", "Unknown"),
            "score":             score_str,
            "total_match_games": tmg,
        }

        # Diagnostic: log first match's raw score/side fields to confirm win detection
        if not _logged_first:
            logger.info(
                "[WIN_DETECT] first event: id=%s side=%s homeScore=%s awayScore=%s won=%s surface=%s tourn=%r",
                event.get("id"), side, hs, aws, won, surface_val,
                event.get("tournament", {}).get("name", ""),
            )
            _logged_first = True

        has_stats = False
        stats_data = stats_map.get(event.get("id", 0)) or {}
        if stats_data:
            parsed = _parse_match_stats(stats_data, event, pid)
            if parsed:
                # _parse_match_stats intentionally excludes "surface" from its
                # return dict to avoid overwriting the groundType-aware detection
                # already stored in base["surface"] via _infer_surface_from_event.
                # Any remaining overlap (won, tournament, etc.) is idempotent.
                base.update(parsed)
                has_stats = True

        all_match_stats.append(base)
        if has_stats:
            # Only stat-parsed matches contribute to the aggregated averages
            stat_matches.append(base)

    # Aggregate stats using ONLY stat-parsed matches so the "Matches" count
    # reflects real data rows, not matches that returned empty statistics.
    def _agg_surface(surf):
        subset = (
            [m for m in stat_matches if m.get("surface") == surf]
            if surf else stat_matches
        )
        return surf or "All", _agg(subset)

    surfaces: dict = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        for label, result in ex.map(_agg_surface, [None, "Hard", "Clay", "Grass"]):
            surfaces[label] = result

    sorted_m = sorted(all_match_stats, key=lambda x: x.get("timestamp", 0), reverse=True)

    # Diagnostic: log first 3 matches for total_match_games verification
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

    # Build recent-results strings per surface for AI scouting report context.
    # Format: "W 6-3 6-4 vs Napolitano (Clay, Apr 13)"
    def _recent_result_str(m: dict) -> str:
        result_ch = "W" if m.get("won") else "L"
        score     = m.get("score", "")
        opp       = m.get("opponent_name", "Unknown")
        date_str  = ""
        ts_val    = m.get("timestamp", 0) or 0
        if ts_val:
            try:
                dt = datetime.utcfromtimestamp(ts_val)
                date_str = dt.strftime("%b %-d")   # "Apr 13"
            except Exception:
                try:
                    date_str = dt.strftime("%b %d").lstrip("0")
                except Exception:
                    date_str = m.get("date", "")
        tourn = m.get("tournament", "")
        return f"{result_ch} {score} vs {opp} ({tourn}, {date_str})" if date_str else f"{result_ch} {score} vs {opp} ({tourn})"

    for surf in ("Hard", "Clay", "Grass"):
        surf_matches = surfaces[f"{surf}_matches"]
        surfaces[f"{surf}_recent_results"] = [
            _recent_result_str(m) for m in surf_matches[:5]
        ]

    # Build sofascore_surface_log: last 10 stat-rich matches per surface.
    # These are the matches with full parsed statistics (aces, DFs, BP, total_games).
    # Used as the "recent form" layer in blended_stats.
    def _ss_log_entry(m: dict) -> dict:
        ts_val = m.get("timestamp", 0) or 0
        try:
            date_str = datetime.utcfromtimestamp(ts_val).strftime("%b %-d")
        except Exception:
            try:
                date_str = datetime.utcfromtimestamp(ts_val).strftime("%b %d").lstrip("0")
            except Exception:
                date_str = m.get("date", "")
        opp_parts = (m.get("opponent_name") or "Unknown").split()
        opp_abbr = opp_parts[-1] if opp_parts else "Unknown"
        return {
            "date":            date_str,
            "date_ts":         ts_val,
            "tournament":      m.get("tournament", ""),
            "surface":         m.get("surface", ""),
            "opponent":        m.get("opponent_name", "Unknown"),
            "opponent_abbr":   opp_abbr,
            "won":             m.get("won", False),
            "score":           m.get("score", ""),
            "total_match_games": m.get("total_match_games"),
            "aces":            m.get("aces"),
            "double_faults":   m.get("double_faults"),
            "bp_converted_count": m.get("bp_converted_count"),
            "bp_converted":    m.get("bp_converted"),
            "bp_faced_count":  m.get("bp_faced_count"),
            "first_serve_pts_won": m.get("first_serve_pts_won"),
            "second_serve_pts_won": m.get("second_serve_pts_won"),
        }

    for surf in ("Hard", "Clay", "Grass"):
        # Strict inclusion: only add matches where the stats API returned at least
        # aces AND bp_converted_count.  This excludes matches where the stats fetch
        # failed (aces would be None) and matches with only partial data.
        # A genuine 0 value (player hit 0 aces / won 0 BPs) is still included.
        surf_stat_matches = [
            m for m in sorted_m
            if m.get("surface") == surf
            and m.get("aces") is not None
            and m.get("bp_converted_count") is not None
        ]
        skipped = sum(
            1 for m in sorted_m
            if m.get("surface") == surf
            and not (m.get("aces") is not None and m.get("bp_converted_count") is not None)
        )
        if skipped:
            logger.info(
                "SURFACE_LOG | surface=%s | included=%d | skipped_no_stats=%d",
                surf, len(surf_stat_matches), skipped,
            )
        surfaces[f"{surf}_surface_log"] = [_ss_log_entry(m) for m in surf_stat_matches[:10]]

    # Per-surface chart log: ALL matches (stat-rich AND stat-poor) for bar chart.
    # Distinct from {surf}_surface_log which only contains stat-rich matches used
    # for blended stats.  For challenger players (Sofascore stats API fails),
    # this ensures the bar chart can still display match results (won/score/date)
    # even when individual stat values are None.
    for surf in ("Hard", "Clay", "Grass"):
        surf_all = [m for m in sorted_m if m.get("surface") == surf]
        surfaces[f"{surf}_chart_log"] = [_ss_log_entry(m) for m in surf_all[:10]]

    # All-surface chart log: most recent 10 matches across all surfaces combined.
    # Used as final Sofascore fallback when surface-specific chart log is also empty.
    surfaces["all_surface_chart_log"] = [_ss_log_entry(m) for m in sorted_m[:10]]

    # ── New SS aggregation tiers (for blended_stats) ─────────────────────────
    # Build all_time, recent_3yr, and last_20 tiers for each surface (and All).
    # Uses sorted_m (newest-first) so that stat_m[:20] captures the most recent
    # 20 stat-rich matches rather than the oldest 20 from the raw iteration order.
    _stat_ids = {id(m) for m in stat_matches}   # O(1) lookup — same objects in sorted_m

    for surf_label in (None, "Hard", "Clay", "Grass"):
        label = surf_label or "All"
        all_m  = [m for m in sorted_m
                  if surf_label is None or m.get("surface") == surf_label]
        stat_m = [m for m in sorted_m
                  if (surf_label is None or m.get("surface") == surf_label)
                  and id(m) in _stat_ids]

        # 3-year window: 2023-present
        all_m_3yr  = [m for m in all_m  if _year_from_ts(m.get("timestamp", 0)) in _RECENT_YEARS]
        stat_m_3yr = [m for m in stat_m if _year_from_ts(m.get("timestamp", 0)) in _RECENT_YEARS]

        # Last 20 stat-rich matches on this surface (newest-first = most recent 20)
        stat_m_20 = stat_m[:20]

        surfaces[f"{label}_all_time_stats"]  = _agg_split(all_m, stat_m)
        surfaces[f"{label}_recent_3yr_stats"] = _agg_split(all_m_3yr, stat_m_3yr)
        surfaces[f"{label}_last_20"]          = _agg_split(stat_m_20, stat_m_20)

        logger.info(
            "SS_TIERS | surface=%s | all_time=%d/%d | 3yr=%d/%d | last20=%d",
            label,
            len(all_m), len(stat_m),
            len(all_m_3yr), len(stat_m_3yr),
            len(stat_m_20),
        )

        # Ace-against: average of opponent aces per match (from opp_aces field)
        ace_ag_vals = [m["opp_aces"] for m in stat_m if m.get("opp_aces") is not None]
        surfaces[f"{label}_ace_against_per_match"] = (
            round(sum(ace_ag_vals) / len(ace_ag_vals), 2) if ace_ag_vals else None
        )

    # Summary log: confirms tier keys were built and shows match counts per surface.
    # If Railway shows all zeros here, the surface detection or event fetch failed.
    tier_summary = {
        lbl: surfaces.get(f"{lbl}_all_time_stats", {}).get("matches_played", 0)
        for lbl in ("All", "Hard", "Clay", "Grass")
    }
    logger.info("SS_TIERS_BUILT | pid=%d | all_time_matches=%s", pid, tier_summary)

    st.session_state[cache_key] = surfaces
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
