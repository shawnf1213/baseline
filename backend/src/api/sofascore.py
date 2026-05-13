import time
import re
import streamlit as st
from typing import Optional

try:
    from curl_cffi import requests
    IMPERSONATE = "chrome110"
except ImportError:
    import requests
    IMPERSONATE = None

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Cache-Control": "no-cache",
}

BASE_URL = "https://api.sofascore.com/api/v1"


def _get(url: str, params: dict = None) -> dict:
    try:
        kwargs = dict(headers=HEADERS, params=params, timeout=12)
        if IMPERSONATE:
            kwargs["impersonate"] = IMPERSONATE
        r = requests.get(url, **kwargs)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


# ── Tournament name → surface inference ──────────────────────────────────────
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
    # Check groundType in slug
    return "Hard"  # default to hard (most common)


def _parse_fraction_pct(value_str: str) -> Optional[float]:
    """Parse percentage from '56/94 (60%)' or '75%' or plain number."""
    if value_str is None:
        return None
    s = str(value_str).strip()
    # Try "N/M (P%)" format
    m = re.search(r'\((\d+(?:\.\d+)?)%\)', s)
    if m:
        return float(m.group(1))
    # Try plain "N%" format
    m = re.search(r'^(\d+(?:\.\d+)?)%$', s)
    if m:
        return float(m.group(1))
    # Try fraction "N/M"
    m = re.match(r'^(\d+)/(\d+)$', s)
    if m:
        n, d = float(m.group(1)), float(m.group(2))
        return (n / d * 100) if d > 0 else None
    # Plain number
    try:
        return float(s)
    except ValueError:
        return None


# Exact Sofascore stat names → our internal keys + whether to parse as %
# Format: name_lower → (internal_key, is_fraction_pct)
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


def search_players(query: str, gender_pref: str = None) -> list:
    if len(query) < 3:
        return []
    data = _get(f"{BASE_URL}/search/all", {"q": query})
    results = []
    for item in data.get("results", []):
        entity = item.get("entity", {})
        if entity.get("type") != 1:
            continue
        sport = entity.get("sport", {})
        if sport.get("name", "").lower() != "tennis":
            continue
        results.append(entity)
    if gender_pref == "F":
        results.sort(key=lambda x: 0 if x.get("gender") == "F" else 1)
    return results[:5]


THREE_YEARS_SECS = 3 * 365 * 24 * 3600


def get_player_recent_events(player_id: int, num_pages: int = 15) -> list:
    cache_key = f"ss_events_{player_id}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    import time as _time
    cutoff = _time.time() - THREE_YEARS_SECS

    all_events = []
    for page in range(num_pages):
        data = _get(f"{BASE_URL}/team/{player_id}/events/last/{page}")
        events = data.get("events", [])
        if not events:
            break
        all_events.extend(events)
        # Stop fetching if oldest event on this page is beyond 3-year cutoff
        oldest_ts = min(e.get("startTimestamp", 0) or 0 for e in events)
        if oldest_ts and oldest_ts < cutoff:
            break
        time.sleep(0.2)

    st.session_state[cache_key] = all_events
    return all_events


def get_event_statistics(event_id: int) -> dict:
    cache_key = f"ss_stats_{event_id}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]
    data = _get(f"{BASE_URL}/event/{event_id}/statistics")
    st.session_state[cache_key] = data
    return data


def _parse_match_stats(stats_data: dict, event: dict, player_id: int) -> Optional[dict]:
    statistics = stats_data.get("statistics", [])
    if not statistics:
        return None

    all_period = next((p for p in statistics if p.get("period") == "ALL"), None)
    if not all_period:
        all_period = statistics[0]

    home_id = event.get("homeTeam", {}).get("id")
    away_id = event.get("awayTeam", {}).get("id")

    side = "home" if home_id == player_id else "away"
    opp_side = "away" if side == "home" else "home"

    home_score = event.get("homeScore", {}).get("current", 0) or 0
    away_score = event.get("awayScore", {}).get("current", 0) or 0
    won = (home_score > away_score) if side == "home" else (away_score > home_score)

    opp_team = event.get("awayTeam", {}) if side == "home" else event.get("homeTeam", {})

    result = {
        "won": won,
        "surface": _infer_surface(event.get("tournament", {}).get("name", "")),
        "tournament": event.get("tournament", {}).get("name", "Unknown"),
        "timestamp": event.get("startTimestamp", 0),
        "event_id": event.get("id"),
        "opponent_name": opp_team.get("name", "Unknown"),
    }

    # Also grab opponent bp_saved to compute player's bp conversion rate
    opp_bp_faced = None

    for group in all_period.get("groups", []):
        for item in group.get("statisticsItems", []):
            name_lower = item.get("name", "").lower().strip()
            if name_lower not in STAT_MAP:
                continue
            internal_key, is_pct = STAT_MAP[name_lower]

            # Player side value
            if is_pct:
                raw_str = item.get(side, "")
                val = _parse_fraction_pct(raw_str)
            else:
                val = item.get(f"{side}Value")
                if val is not None:
                    try:
                        val = float(val)
                    except (ValueError, TypeError):
                        val = None

            if val is not None:
                result[internal_key] = val

            # Capture opponent's bp_saved denominator for bp conversion %
            if name_lower == "break points saved":
                opp_raw = item.get(opp_side, "")
                m = re.match(r"(\d+)/(\d+)", str(opp_raw))
                if m:
                    opp_bp_faced = float(m.group(2))

    # Compute bp_converted_pct from count + opponent bp faced
    bp_conv_count = result.get("bp_converted_count")
    if bp_conv_count is not None and opp_bp_faced and opp_bp_faced > 0:
        result["bp_converted"] = bp_conv_count / opp_bp_faced * 100
    elif bp_conv_count is not None and result.get("return_games") and result["return_games"] > 0:
        result["bp_converted"] = bp_conv_count / result["return_games"] * 100

    return result


def _build_score_str(event: dict) -> str:
    """Build set score string like '6-3 7-5' from event homeScore/awayScore periods."""
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


def get_player_stats_by_surface(player_id: int) -> dict:
    cache_key = f"ss_surface_{player_id}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    import time as _time
    cutoff = _time.time() - THREE_YEARS_SECS

    events = get_player_recent_events(player_id)
    all_match_stats = []

    for event in events:
        ts = event.get("startTimestamp", 0) or 0
        if ts and ts < cutoff:
            continue  # skip events older than 3 years

        status_type = event.get("status", {}).get("type", "")
        if status_type not in ("finished", "ended"):
            continue

        # Skip doubles (names contain "/")
        ht_name = event.get("homeTeam", {}).get("name", "")
        at_name = event.get("awayTeam", {}).get("name", "")
        if "/" in ht_name or "/" in at_name:
            continue

        home_id = event.get("homeTeam", {}).get("id")
        side = "home" if home_id == player_id else "away"
        opp_team = event.get("awayTeam", {}) if side == "home" else event.get("homeTeam", {})

        home_score = event.get("homeScore", {}).get("current", 0) or 0
        away_score = event.get("awayScore", {}).get("current", 0) or 0
        won = (home_score > away_score) if side == "home" else (away_score > home_score)

        base = {
            "won": won,
            "surface": _infer_surface(event.get("tournament", {}).get("name", "")),
            "tournament": event.get("tournament", {}).get("name", "Unknown"),
            "timestamp": ts,
            "event_id": event.get("id"),
            "opponent_name": opp_team.get("name", "Unknown"),
            "score": _build_score_str(event),
        }

        stats_data = get_event_statistics(event.get("id", 0))
        if stats_data:
            parsed = _parse_match_stats(stats_data, event, player_id)
            if parsed:
                base.update(parsed)

        all_match_stats.append(base)
        time.sleep(0.1)

    def _agg(matches: list) -> dict:
        if not matches:
            return {"matches_played": 0}
        numeric_keys = [
            "aces", "double_faults", "first_serve_pct",
            "first_serve_pts_won", "second_serve_pts_won",
            "return_first_serve_pts_won", "return_second_serve_pts_won",
            "bp_converted", "bp_saved", "total_games_won",
            "service_pts_won", "return_pts_won_count",
            "bp_converted_count",
        ]
        wins = sum(1 for m in matches if m.get("won", False))
        agg = {
            "matches_played": len(matches),
            "wins": wins,
            "win_rate": wins / len(matches) * 100 if matches else 0,
        }
        for key in numeric_keys:
            vals = [m[key] for m in matches if key in m and m[key] is not None]
            agg[key] = sum(vals) / len(vals) if vals else None
        return agg

    surfaces = {
        "All":   _agg(all_match_stats),
        "Hard":  _agg([m for m in all_match_stats if m.get("surface") == "Hard"]),
        "Clay":  _agg([m for m in all_match_stats if m.get("surface") == "Clay"]),
        "Grass": _agg([m for m in all_match_stats if m.get("surface") == "Grass"]),
    }

    sorted_m = sorted(all_match_stats, key=lambda x: x.get("timestamp", 0), reverse=True)
    surfaces["form"] = [
        {
            "won": m.get("won", False),
            "tournament": m.get("tournament", ""),
            "surface": m.get("surface", ""),
            "opponent": m.get("opponent_name", ""),
        }
        for m in sorted_m[:10]
    ]
    surfaces["all_matches"] = sorted_m  # sorted newest first

    # Per-surface sorted match lists (for schedule tables)
    for surf in ("Hard", "Clay", "Grass"):
        surfaces[f"{surf}_matches"] = [m for m in sorted_m if m.get("surface") == surf]

    st.session_state[cache_key] = surfaces
    return surfaces


def ts_to_date_str(ts: int) -> str:
    """Convert Unix timestamp to 'Mon DD YYYY' string."""
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %d %Y")
    except Exception:
        return "—"
