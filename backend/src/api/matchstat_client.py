"""
Central HTTP client for the Matchstat Tennis API (tennis-api-atp-wta-itf.p.rapidapi.com).
This is the only file in the app that makes HTTP requests to Matchstat.
- 700 ms minimum between requests
- 6-hour disk cache keyed by endpoint + params hash
- Exponential backoff on 429: wait 5s → 10s → 30s
- All functions return parsed dict/list or None on failure
- Player search uses /tennis/v2/search + startup ranking cache for IDs
"""

import os
import json
import time
import hashlib
import logging
import pandas as pd
from pathlib import Path
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY = os.getenv("MATCHSTAT_API_KEY", "")
BASE_URL = "https://tennis-api-atp-wta-itf.p.rapidapi.com"
HEADERS = {
    "Content-Type": "application/json",
    "x-rapidapi-host": "tennis-api-atp-wta-itf.p.rapidapi.com",
    "x-rapidapi-key": API_KEY,
}

CACHE_DIR = Path(".cache/matchstat")
CACHE_TTL_HOURS = 6
MIN_REQUEST_INTERVAL = 0.7  # 700 ms

# Surface courtId → display name  (confirmed from live API: 5=Grass, 3=Indoor Hard)
SURFACE_MAP = {1: "Hard", 2: "Clay", 3: "Hard", 4: "Hard", 5: "Grass"}
# Surface → courtIds to include when filtering past-matches
SURFACE_ID_MAP = {"Hard": [1, 3, 4], "Clay": [2], "Grass": [5]}

# ── Internal state ────────────────────────────────────────────────────────────
_last_request_time: float = 0.0

# Ranking cache: name_lower → {id, rank, countryAcr, gender}
# Populated at startup by load_player_lists(); used to resolve IDs from search results.
_ranking_cache: dict[str, dict] = {}

# Secondary index: last_name_lower → list of {id, name_lower, rank, countryAcr, gender}
# Allows resolving "Comesana" → "Francisco Comesana" when full name isn't in cache.
_lastname_cache: dict[str, list] = {}

# ID → entry: lets us look up canonical name from a known ID.
_id_cache: dict[str, dict] = {}


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_key(path: str, params: dict) -> str:
    raw = f"{path}|{json.dumps(params, sort_keys=True)}"
    return hashlib.md5(raw.encode()).hexdigest()


def _load_cache(key: str):
    f = CACHE_DIR / f"{key}.json"
    if not f.exists():
        return None
    age_h = (time.time() - f.stat().st_mtime) / 3600
    if age_h > CACHE_TTL_HOURS:
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_cache(key: str, data) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{key}.json").write_text(json.dumps(data), encoding="utf-8")


# ── Core HTTP client ───────────────────────────────────────────────────────────

def _api_get(path: str, params: dict = None) -> dict | None:
    global _last_request_time
    if params is None:
        params = {}

    ck = _cache_key(path, params)
    cached = _load_cache(ck)
    if cached is not None:
        return cached

    elapsed = time.time() - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)

    url = f"{BASE_URL}{path}"
    backoff_seq = [0, 5, 10, 30]

    for i, wait in enumerate(backoff_seq):
        if wait > 0:
            logger.warning("Rate limited — waiting %ds before retry %d", wait, i)
            time.sleep(wait)
        try:
            _last_request_time = time.time()
            resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
            logger.debug("GET %s params=%s → %s", path, params, resp.status_code)

            if resp.status_code == 429:
                if i < len(backoff_seq) - 1:
                    continue
                logger.error("Rate limit persists after all retries for %s", path)
                return None

            if resp.status_code != 200:
                logger.error("API %d for %s: %s", resp.status_code, path, resp.text[:300])
                return None

            data = resp.json()
            _save_cache(ck, data)
            return data

        except Exception as exc:
            logger.error("Request error for %s: %s", path, exc)
            if i == len(backoff_seq) - 1:
                return None

    return None


# ── Confirmed endpoint wrappers ───────────────────────────────────────────────

# --- Search ---
def search_api(query: str):
    """GET /tennis/v2/search?search={query}"""
    return _api_get("/tennis/v2/search", {"search": query})


# --- Player endpoints ---
def get_players(tour: str = "atp", page_size: int = 500, page_no: int = 1):
    """GET /tennis/v2/{tour}/player?pageSize=&pageNo="""
    return _api_get(f"/tennis/v2/{tour.lower()}/player",
                    {"pageSize": page_size, "pageNo": page_no})


def get_player_info(player_id: str, tour: str = "atp"):
    """GET /tennis/v2/{tour}/player/profile/{player_id}?include=form,ranking,country"""
    return _api_get(f"/tennis/v2/{tour.lower()}/player/profile/{player_id}",
                    {"include": "form,ranking,country"})


def get_player_match_stats(player_id: str, tour: str = "atp"):
    """GET /tennis/v2/{tour}/player/match-stats/{player_id}"""
    return _api_get(f"/tennis/v2/{tour.lower()}/player/match-stats/{player_id}")


def get_player_past_matches(player_id: str, tour: str = "atp",
                            page_size: int = 100, page_no: int = 1,
                            filter_str: str = "", include: str = ""):
    """GET /tennis/v2/{tour}/player/past-matches/{player_id}?pageSize=&pageNo=&filter=&include="""
    params = {"pageSize": page_size, "pageNo": page_no}
    if filter_str:
        params["filter"] = filter_str
    if include:
        params["include"] = include
    return _api_get(f"/tennis/v2/{tour.lower()}/player/past-matches/{player_id}", params)


def get_player_surface_summary(player_id: str, tour: str = "atp"):
    """GET /tennis/v2/{tour}/player/surface-summary/{player_id}"""
    return _api_get(f"/tennis/v2/{tour.lower()}/player/surface-summary/{player_id}")


def get_player_tournament_record(player_id: str, tournament_id: str, tour: str = "atp"):
    """GET /tennis/v2/{tour}/player/tournament-record/{player_id}/{tournament_id}"""
    return _api_get(
        f"/tennis/v2/{tour.lower()}/player/tournament-record/{player_id}/{tournament_id}"
    )


def get_player_finals(player_id: str, tour: str = "atp"):
    return _api_get(f"/tennis/v2/{tour.lower()}/player/finals/{player_id}")


def get_player_titles(player_id: str, tour: str = "atp"):
    return _api_get(f"/tennis/v2/{tour.lower()}/player/titles/{player_id}")


def get_player_performance_breakdown(player_id: str, tour: str = "atp"):
    return _api_get(f"/tennis/v2/{tour.lower()}/player/perf-breakdown/{player_id}")


def get_player_match_filters(player_id: str, tour: str = "atp"):
    return _api_get(f"/tennis/v2/{tour.lower()}/player/match-filters/{player_id}")


# --- H2H endpoints ---
def get_h2h_info(player1_id: str, player2_id: str, tour: str = "atp"):
    """GET /tennis/v2/{tour}/h2h/info/{player1_id}/{player2_id}"""
    return _api_get(f"/tennis/v2/{tour.lower()}/h2h/info/{player1_id}/{player2_id}")


def get_h2h_matches(player1_id: str, player2_id: str, tour: str = "atp",
                    page_size: int = 50, page_no: int = 1, filter_str: str = ""):
    """GET /tennis/v2/{tour}/h2h/matches/{player1_id}/{player2_id}?pageSize=&pageNo=&filter="""
    params = {"pageSize": page_size, "pageNo": page_no}
    if filter_str:
        params["filter"] = filter_str
    return _api_get(f"/tennis/v2/{tour.lower()}/h2h/matches/{player1_id}/{player2_id}", params)


def get_h2h_stats(player1_id: str, player2_id: str, tour: str = "atp"):
    """GET /tennis/v2/{tour}/h2h/stats/{player1_id}/{player2_id}"""
    return _api_get(f"/tennis/v2/{tour.lower()}/h2h/stats/{player1_id}/{player2_id}")


def get_h2h_match_filters(player1_id: str, player2_id: str, tour: str = "atp"):
    return _api_get(f"/tennis/v2/{tour.lower()}/h2h/filter/{player1_id}/{player2_id}")


def get_match_stats(tournament_id: str, player1_id: str, player2_id: str, tour: str = "atp"):
    return _api_get(
        f"/tennis/v2/{tour.lower()}/h2h/match-stats/{tournament_id}/{player1_id}/{player2_id}"
    )


def get_h2h_vs_all_stats(player_id: str, tour: str = "atp"):
    return _api_get(f"/tennis/v2/{tour.lower()}/h2h/vs-all-stats/{player_id}")


# --- Ranking endpoints ---
def get_singles_ranking(tour: str = "atp"):
    """GET /tennis/v2/{tour}/ranking/singles"""
    return _api_get(f"/tennis/v2/{tour.lower()}/ranking/singles")


def get_doubles_ranking(tour: str = "atp"):
    return _api_get(f"/tennis/v2/{tour.lower()}/ranking/doubles")


# --- Fixture endpoints ---
def get_date_fixtures(date: str, tour: str = "atp"):
    """GET /tennis/v2/{tour}/fixtures/{date}  date: YYYY-MM-DD"""
    return _api_get(f"/tennis/v2/{tour.lower()}/fixtures/{date}")


def get_player_fixtures(player_id: str, tour: str = "atp"):
    """GET /tennis/v2/{tour}/fixtures/player/{player_id}"""
    return _api_get(f"/tennis/v2/{tour.lower()}/fixtures/player/{player_id}")


def get_h2h_fixtures(player1_id: str, player2_id: str, tour: str = "atp"):
    return _api_get(f"/tennis/v2/{tour.lower()}/fixtures/h2h/{player1_id}/{player2_id}")


def get_all_fixtures(tour: str = "atp"):
    return _api_get(f"/tennis/v2/{tour.lower()}/fixtures")


def get_date_range_fixtures(start_date: str, end_date: str, tour: str = "atp"):
    return _api_get(f"/tennis/v2/{tour.lower()}/fixtures/{start_date}/{end_date}")


def get_tournament_fixtures(tournament_id: str, tour: str = "atp",
                            page_size: int = 50, page_no: int = 1, filter_str: str = ""):
    params = {"pageSize": page_size, "pageNo": page_no}
    if filter_str:
        params["filter"] = filter_str
    return _api_get(f"/tennis/v2/{tour.lower()}/fixtures/tournament/{tournament_id}", params)


# --- Tournament endpoints ---
def get_tournament_seasons(tournament_id: str, tour: str = "atp"):
    return _api_get(f"/tennis/v2/{tour.lower()}/tournament/seasons/{tournament_id}")


def get_tournament_results(tournament_id: str, tour: str = "atp"):
    return _api_get(f"/tennis/v2/{tour.lower()}/tournament/results/{tournament_id}")


def get_tournament_calendar(tour: str = "atp"):
    return _api_get(f"/tennis/v2/{tour.lower()}/tournament/calendar")


def get_tour_info(tournament_id: str, tour: str = "atp"):
    return _api_get(f"/tennis/v2/{tour.lower()}/tournament/info/{tournament_id}")


def get_tournament_past_champions(tournament_id: str, tour: str = "atp"):
    return _api_get(f"/tennis/v2/{tour.lower()}/tournament/past-champions/{tournament_id}")


# --- Misc endpoints ---
def get_country_list():
    return _api_get("/tennis/v2/countryList")


def get_ranking_list():
    return _api_get("/tennis/v2/rankingList")


def get_round_list():
    return _api_get("/tennis/v2/roundList")


def get_court_list():
    return _api_get("/tennis/v2/courtList")


# ── Ranking cache loader (called at startup) ──────────────────────────────────

def _add_to_caches(pid, name: str, rank, ctr: str, gender: str) -> None:
    """Insert one player into all three caches."""
    key = name.lower()
    entry = {"id": pid, "rank": rank, "countryAcr": ctr, "gender": gender}
    _ranking_cache[key] = entry
    _id_cache[str(pid)] = {**entry, "name_lower": key}
    # Last-name index: last word of name (e.g. "comesana" from "francisco comesana")
    parts = key.split()
    if parts:
        ln = parts[-1]
        _lastname_cache.setdefault(ln, [])
        if not any(e["id"] == pid for e in _lastname_cache[ln]):
            _lastname_cache[ln].append({**entry, "name_lower": key})


def _ingest_ranking_entry(entry: dict, gender: str) -> None:
    """Parse one ranking entry and add to caches."""
    player = entry.get("player") or entry
    pid  = player.get("id") or entry.get("id")
    name = (player.get("name") or player.get("playerName") or "").strip()
    rank = entry.get("position") or entry.get("rank") or player.get("currentRank")
    ctr  = player.get("countryAcr") or player.get("country") or ""
    if isinstance(ctr, dict):
        ctr = ctr.get("acronym") or ""
    if name and pid:
        _add_to_caches(pid, name, rank, ctr, gender)


def _ingest_player_entry(entry: dict, gender: str) -> None:
    """Parse one player-list entry and add to caches if not already present."""
    pid  = entry.get("id") or entry.get("playerId")
    name = (entry.get("name") or entry.get("playerName") or "").strip()
    rank = entry.get("currentRank") or entry.get("ranking") or entry.get("rank")
    ctr  = entry.get("countryAcr") or entry.get("country") or ""
    if isinstance(ctr, dict):
        ctr = ctr.get("acronym") or ""
    key = name.lower()
    if name and pid and key not in _ranking_cache:
        _add_to_caches(pid, name, rank, ctr, gender)


def load_player_lists() -> None:
    """
    At startup:
    1. Fetch ATP + WTA singles rankings → exact rank numbers + player IDs.
    2. Paginate the /player endpoint to extend the cache for non-ranked players.
    Builds _ranking_cache: name_lower → {id, rank, countryAcr, gender}.
    """
    global _ranking_cache
    for tour_lc, gender in [("atp", "M"), ("wta", "F")]:
        # ── Step 1: rankings (authoritative rank numbers + IDs) ──────────────
        resp = get_singles_ranking(tour_lc)
        if resp:
            for entry in _extract_list(resp, ("data", "players", "results", "items")):
                _ingest_ranking_entry(entry, gender)

        rank_count = sum(1 for v in _ranking_cache.values() if v["gender"] == gender)
        logger.info("Rankings loaded: %d %s players", rank_count, tour_lc.upper())

        # ── Step 2: paginated player list (adds non-ranked players / IDs) ────
        for page in range(1, 26):          # up to 25 pages × 100 = 2500 players per tour
            presp = get_players(tour_lc, page_size=100, page_no=page)
            if not presp:
                break
            batch = _extract_list(presp, ("data", "players", "results", "items"))
            if not batch:
                break
            for entry in batch:
                _ingest_player_entry(entry, gender)
            if len(batch) < 100:
                break

        total = sum(1 for v in _ranking_cache.values() if v["gender"] == gender)
        logger.info("Total cache: %d %s players", total, tour_lc.upper())


# ── Player search (live /tennis/v2/search + ranking cache for IDs) ────────────

def _resolve_from_cache(name: str, gender_pref: str) -> dict:
    """
    Try to find a player entry in the ranking cache by name.
    Resolution order:
      1. Exact full-name match
      2. startswith / prefix match (handles "Carlos Alcaraz Gonzalez" → "carlos alcaraz")
      3. Last-name match via _lastname_cache
    Returns the cache entry dict or {}.
    """
    name_lc = name.lower()

    # 1. Exact
    if name_lc in _ranking_cache:
        e = _ranking_cache[name_lc]
        if e["gender"] == gender_pref:
            return e

    # 2. Prefix / startswith
    for cache_key, cache_val in _ranking_cache.items():
        if cache_val["gender"] != gender_pref:
            continue
        if name_lc.startswith(cache_key) or cache_key.startswith(name_lc):
            return cache_val

    # 3. Last-name index — search result last word vs cache last word
    parts = name_lc.split()
    if parts:
        ln = parts[-1]
        for entry in _lastname_cache.get(ln, []):
            if entry.get("gender") == gender_pref:
                return entry

    return {}


def _canonical_name(pid, fallback: str) -> str:
    """Return title-cased canonical name from id cache, or title-case the fallback."""
    entry = _id_cache.get(str(pid))
    if entry:
        return " ".join(w.capitalize() for w in entry["name_lower"].split())
    return " ".join(w.capitalize() for w in fallback.lower().split())


def _extract_search_players(resp, tour_lc: str) -> list[dict]:
    """
    Pull player dicts out of a search API response.
    The confirmed shape: {"data": [{"category": "player_atp", "result": [{name,...}]}]}
    Logs all categories seen so we can debug non-tennis results.
    """
    if not resp:
        return []

    categories = _extract_list(resp, ("data", "results", "items"))
    players: list[dict] = []

    if categories and isinstance(categories[0], dict) and "category" in categories[0]:
        for cat in categories:
            if not isinstance(cat, dict):
                continue
            category = (cat.get("category") or "").lower()
            logger.info("search category found: %r", category)
            # Relaxed filter: include anything with "player" (not sport-specific checks)
            if "player" not in category:
                continue
            # Skip wrong-tour categories when both are present
            if "player_atp" in category and tour_lc == "wta":
                continue
            if "player_wta" in category and tour_lc == "atp":
                continue
            result_list = cat.get("result") or cat.get("results") or []
            if isinstance(result_list, list):
                logger.info("  → %d player(s) in category %r", len(result_list), category)
                players.extend(result_list)
    else:
        # Flat list or different nesting
        players = _extract_list(resp, ("players", "results", "data", "items"))
        if not players and isinstance(resp, list):
            players = resp

    return players


def search_players(query: str, tour: str = "ATP") -> list[dict]:
    """
    Search using /tennis/v2/search?search={query}.
    Falls back to first-4-chars retry then cache-only substring search.
    Returns top 5 [{id, name, currentRank, countryAcr, gender}].
    """
    q = query.strip()
    if len(q) < 3:
        return []

    tour_lc = tour.lower()
    gender_pref = "F" if tour.upper() == "WTA" else "M"

    resp = search_api(q)
    logger.info("search_api(%r) → keys=%s preview=%.300s",
                q,
                list(resp.keys()) if isinstance(resp, dict) else type(resp).__name__,
                json.dumps(resp) if resp else "None")

    raw_players = _extract_search_players(resp, tour_lc) if resp else []

    # Fallback 1: retry with first 4 chars (helps accented names, short names)
    if not raw_players and len(q) > 4:
        short_q = q[:4]
        logger.info("No tennis players for %r — retrying with %r", q, short_q)
        resp2 = search_api(short_q)
        if resp2:
            raw_players = _extract_search_players(resp2, tour_lc)

    # Fallback 2: pure cache substring search
    if not raw_players:
        logger.info("Search API returned no players for %r — using cache only", q)
        return _cache_only_search(q, gender_pref)

    seen_ids: set = set()
    matches: list[dict] = []
    for p in raw_players:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or p.get("playerName") or p.get("fullName") or "").strip()
        if not name:
            continue

        pid = p.get("id") or p.get("playerId") or p.get("player_id")
        cached = _resolve_from_cache(name, gender_pref)
        if not pid:
            pid = cached.get("id")
        if not pid:
            logger.debug("No ID resolved for %r — skipping", name)
            continue

        pid_str = str(pid)
        if pid_str in seen_ids:
            continue
        seen_ids.add(pid_str)

        rank = (p.get("currentRank") or p.get("ranking") or p.get("rank")
                or cached.get("rank"))
        country = (p.get("countryAcr") or p.get("country") or cached.get("countryAcr") or "")
        if isinstance(country, dict):
            country = country.get("acronym") or ""

        matches.append({
            "id": pid,
            "name": _canonical_name(pid, name),
            "currentRank": rank,
            "countryAcr": country,
            "gender": gender_pref,
        })

    matches.sort(key=lambda x: x.get("currentRank") or 9999)

    # Supplement sparse API results with cache hits
    if len(matches) < 5:
        cache_hits = _cache_only_search(q, gender_pref, exclude_ids=seen_ids)
        matches.extend(cache_hits[: 5 - len(matches)])
        matches.sort(key=lambda x: x.get("currentRank") or 9999)

    return matches[:5]


def _cache_only_search(query: str, gender_pref: str,
                        exclude_ids: set | None = None) -> list[dict]:
    """
    Substring search directly against the ranking cache.
    Used as fallback when the API fails or returns sparse results.
    """
    q = query.lower().strip()
    exclude_ids = exclude_ids or set()
    results: list[dict] = []

    for name_key, entry in _ranking_cache.items():
        if entry.get("gender") != gender_pref:
            continue
        if str(entry.get("id", "")) in exclude_ids:
            continue
        if q in name_key:
            results.append({
                "id": entry["id"],
                "name": " ".join(w.capitalize() for w in name_key.split()),
                "currentRank": entry.get("rank"),
                "countryAcr": entry.get("countryAcr", ""),
                "gender": gender_pref,
            })

    results.sort(key=lambda x: x.get("currentRank") or 9999)
    return results[:5]


def _extract_list(resp, keys: tuple) -> list:
    """Walk a nested dict looking for the first key that holds a list."""
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for k in keys:
            v = resp.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                for k2 in keys:
                    v2 = v.get(k2)
                    if isinstance(v2, list):
                        return v2
    return []


# ── Career stats from get_player_match_stats response ────────────────────────

def _safe_pct(num, den) -> float | None:
    """Return num/den*100 rounded to 1dp, or None if den is 0/None."""
    try:
        num, den = float(num), float(den)
        if den > 0:
            return round(num / den * 100, 1)
    except (TypeError, ValueError):
        pass
    return None


def _compute_career_stats(player_id: str, tour: str, total_matches: int) -> dict:
    """
    Call get_player_match_stats and compute percentage stats from the
    confirmed field structure:
      data.serviceStats  → firstServeGm, firstServeOfGm, winningOnFirstServeGm, etc.
      data.rtnStats      → winningOnFirstServeGm / winningOnFirstServeOfGm, etc.
      data.breakPointsRtnStats  → breakPointWonGm / breakPointChanceGm
      data.breakPointsServeStats → breakPointSavedGm / breakPointFacedGm
    """
    resp = get_player_match_stats(player_id, tour)
    if not resp:
        logger.warning("get_player_match_stats returned None for player %s", player_id)
        return {}

    data = resp.get("data") or resp
    logger.info("get_player_match_stats raw for %s: %s", player_id, str(data)[:400])

    svc    = data.get("serviceStats") or {}
    rtn    = data.get("rtnStats") or {}
    bp_rtn = data.get("breakPointsRtnStats") or {}
    bp_svc = data.get("breakPointsServeStats") or {}

    aces_total = svc.get("acesGm") or 0
    dfs_total  = svc.get("doubleFaultsGm") or 0

    return {
        "aces":   round(aces_total / total_matches, 2) if total_matches else None,
        "double_faults": round(dfs_total / total_matches, 2) if total_matches else None,
        # Service percentages  (spec-confirmed field pairs)
        "first_serve_pct":      _safe_pct(svc.get("firstServeGm"),
                                          svc.get("firstServeOfGm")),
        "first_serve_pts_won":  _safe_pct(svc.get("winningOnFirstServeGm"),
                                          svc.get("winningOnFirstServeOfGm")),
        "second_serve_pts_won": _safe_pct(svc.get("winningOnSecondServeGm"),
                                          svc.get("winningOnSecondServeOfGm")),
        # Return percentages
        "return_first_serve_pts_won":  _safe_pct(rtn.get("winningOnFirstServeGm"),
                                                 rtn.get("winningOnFirstServeOfGm")),
        "return_second_serve_pts_won": _safe_pct(rtn.get("winningOnSecondServeGm"),
                                                 rtn.get("winningOnSecondServeOfGm")),
        # Break points
        "bp_converted": _safe_pct(bp_rtn.get("breakPointWonGm"),
                                  bp_rtn.get("breakPointChanceGm")),
        "bp_saved":     _safe_pct(bp_svc.get("breakPointSavedGm"),
                                  bp_svc.get("breakPointFacedGm")),
        "bp_converted_count": round(
            (bp_rtn.get("breakPointWonGm") or 0) / total_matches, 2
        ) if total_matches else None,
    }


def _compute_surface_stats_from_matches(matches: list, player_id: str) -> dict:
    """
    Aggregate per-match stat objects (fetched with include=stat) into surface averages.
    Accumulates raw counts across all matches then computes percentages — same field
    pairs as _compute_career_stats.
    Returns {} if fewer than 3 matches have usable stat data (UI will show N/A).
    """
    pid = str(player_id)
    totals: dict[str, float] = {k: 0.0 for k in [
        "aces", "dfs",
        "fs_gm", "fs_of",           # first serve in / attempted
        "w1s", "w1s_of",            # win on 1st serve
        "w2s", "w2s_of",            # win on 2nd serve
        "rtn_w1s", "rtn_w1s_of",   # return 1st serve
        "rtn_w2s", "rtn_w2s_of",   # return 2nd serve
        "bp_won", "bp_chances",    # break points converted
        "bp_saved", "bp_faced",    # break points saved
    ]}
    n = 0  # matches with usable stat data

    for m in matches:
        p1_id = str(m.get("player1Id") or "")
        is_p1 = (pid == p1_id)

        svc = rtn = bp_rtn = bp_svc = {}

        # Structure A: flat serviceStats/rtnStats directly on match object
        if m.get("serviceStats"):
            svc    = m.get("serviceStats") or {}
            rtn    = m.get("rtnStats") or {}
            bp_rtn = m.get("breakPointsRtnStats") or {}
            bp_svc = m.get("breakPointsServeStats") or {}

        # Structure B: player1Stats / player2Stats sub-objects
        elif m.get("player1Stats") or m.get("player2Stats"):
            pstats = m.get("player1Stats" if is_p1 else "player2Stats") or {}
            svc    = pstats.get("serviceStats") or {}
            rtn    = pstats.get("rtnStats") or {}
            bp_rtn = pstats.get("breakPointsRtnStats") or {}
            bp_svc = pstats.get("breakPointsServeStats") or {}

        # Structure C: stats list [{playerId, serviceStats, ...}]
        elif m.get("stats"):
            raw_s = m["stats"]
            if isinstance(raw_s, list):
                for s in raw_s:
                    if str(s.get("playerId") or s.get("player_id") or "") == pid:
                        svc    = s.get("serviceStats") or {}
                        rtn    = s.get("rtnStats") or {}
                        bp_rtn = s.get("breakPointsRtnStats") or {}
                        bp_svc = s.get("breakPointsServeStats") or {}
                        break
            elif isinstance(raw_s, dict):
                svc    = raw_s.get("serviceStats") or {}
                rtn    = raw_s.get("rtnStats") or {}
                bp_rtn = raw_s.get("breakPointsRtnStats") or {}
                bp_svc = raw_s.get("breakPointsServeStats") or {}

        if not any([svc, rtn, bp_rtn, bp_svc]):
            continue  # no stat data in this match

        totals["aces"]      += float(svc.get("acesGm") or 0)
        totals["dfs"]       += float(svc.get("doubleFaultsGm") or 0)
        totals["fs_gm"]     += float(svc.get("firstServeGm") or 0)
        totals["fs_of"]     += float(svc.get("firstServeOfGm") or 0)
        totals["w1s"]       += float(svc.get("winningOnFirstServeGm") or 0)
        totals["w1s_of"]    += float(svc.get("winningOnFirstServeOfGm") or 0)
        totals["w2s"]       += float(svc.get("winningOnSecondServeGm") or 0)
        totals["w2s_of"]    += float(svc.get("winningOnSecondServeOfGm") or 0)
        totals["rtn_w1s"]   += float(rtn.get("winningOnFirstServeGm") or 0)
        totals["rtn_w1s_of"] += float(rtn.get("winningOnFirstServeOfGm") or 0)
        totals["rtn_w2s"]   += float(rtn.get("winningOnSecondServeGm") or 0)
        totals["rtn_w2s_of"] += float(rtn.get("winningOnSecondServeOfGm") or 0)
        totals["bp_won"]    += float(bp_rtn.get("breakPointWonGm") or 0)
        totals["bp_chances"] += float(bp_rtn.get("breakPointChanceGm") or 0)
        totals["bp_saved"]  += float(bp_svc.get("breakPointSavedGm") or 0)
        totals["bp_faced"]  += float(bp_svc.get("breakPointFacedGm") or 0)
        n += 1

    if n < 3:
        logger.info("_compute_surface_stats: only %d stat-bearing matches — returning {}", n)
        return {}

    return {
        "aces":                        round(totals["aces"] / n, 2),
        "double_faults":               round(totals["dfs"] / n, 2),
        "first_serve_pct":             _safe_pct(totals["fs_gm"], totals["fs_of"]),
        "first_serve_pts_won":         _safe_pct(totals["w1s"], totals["w1s_of"]),
        "second_serve_pts_won":        _safe_pct(totals["w2s"], totals["w2s_of"]),
        "return_first_serve_pts_won":  _safe_pct(totals["rtn_w1s"], totals["rtn_w1s_of"]),
        "return_second_serve_pts_won": _safe_pct(totals["rtn_w2s"], totals["rtn_w2s_of"]),
        "bp_converted":                _safe_pct(totals["bp_won"], totals["bp_chances"]),
        "bp_saved":                    _safe_pct(totals["bp_saved"], totals["bp_faced"]),
        "bp_converted_count":          round(totals["bp_won"] / n, 2),
    }


def _parse_match_row(m: dict, player_id: str, surface_override: str | None = None) -> dict:
    """
    Flatten a raw past-match dict into a standard history row.
    Confirmed response shape:
      {id, date (ISO string), player1Id, player2Id, match_winner (player id),
       result (score string), player1: {id, name, countryAcr}, player2: {id, name, countryAcr}}
    match_winner is the authoritative win indicator — NOT player1 always being the winner.
    """
    pid = str(player_id)
    p1_id = str(m.get("player1Id") or "")
    p2_id = str(m.get("player2Id") or "")
    winner_id = str(m.get("match_winner") or "")

    is_our_p1 = (pid == p1_id)
    won = (pid == winner_id)

    # Opponent object may be a dict {id, name, countryAcr} or a string
    opp_raw = m.get("player2") if is_our_p1 else m.get("player1")
    if isinstance(opp_raw, dict):
        opp_name = opp_raw.get("name") or "Unknown"
    elif isinstance(opp_raw, str) and opp_raw:
        opp_name = opp_raw
    else:
        # Fallback: try plain name fields
        opp_name = (m.get("player2Name") if is_our_p1 else m.get("player1Name")) or "Unknown"

    # Date: "2026-05-03T18:00:00.000Z" → "2026-05-03"
    date_raw = m.get("date") or m.get("gameDate") or m.get("matchDate") or ""
    date_str = str(date_raw)[:10] if date_raw else ""

    # Surface comes from context (which surface-filtered call produced this match),
    # or courtId if present in the match object
    if surface_override:
        surface = surface_override
    else:
        court_id = m.get("gameCourt") or m.get("courtId") or 0
        try:
            court_id = int(court_id)
        except Exception:
            court_id = 0
        surface = SURFACE_MAP.get(court_id, "Hard")

    tournament = m.get("tournamentName") or m.get("tournament") or ""
    score = m.get("result") or m.get("score") or ""

    return {
        "won": won,
        "timestamp": 0,
        "date": date_str,
        "tournament": tournament,
        "surface": surface,
        "opponent_name": opp_name,
        "score": score,
    }


def _extract_matches(resp) -> list:
    return _extract_list(resp, ("matches", "data", "results", "items", "content"))


def _is_recent(m: dict, cutoff_year: int = 2022) -> bool:
    date = m.get("gameDate") or m.get("date") or m.get("matchDate") or ""
    if isinstance(date, (int, float)) and date > 1e8:
        try:
            return datetime.utcfromtimestamp(date).year >= cutoff_year
        except Exception:
            return True
    if isinstance(date, str) and len(date) >= 4:
        try:
            return int(date[:4]) >= cutoff_year
        except Exception:
            pass
    return True


# Surface filter strings for the past-matches ?filter= query param
# courtId 1=Hard outdoor, 2=Clay, 3=Indoor Hard (→Hard), 5=Grass
_SURFACE_FILTER = {
    "Hard":  "GameCourt:1",   # outdoor hard only (indoor hard fetched via GameCourt:3 and merged)
    "Clay":  "GameCourt:2",
    "Grass": "GameCourt:5",
}
_SURFACE_FILTER_INDOOR = "GameCourt:3"  # indoor hard — merged into Hard
_YEAR_FILTER = "GameYear:2022,2023,2024,2025,2026"


# ── High-level adapter: player surface stats ──────────────────────────────────

def get_player_stats_by_surface(player_id: str, tour: str = "ATP") -> dict:
    """
    Returns surface-keyed stats dict compatible with the existing UI:
      {All, Hard, Clay, Grass, form, all_matches, Hard_matches, Clay_matches, Grass_matches}

    Issue 1 fix: Hard/Clay/Grass stats are computed from surface-filtered past-matches
      fetched with include=stat, giving real per-surface averages.
      The All column uses career aggregates from get_player_match_stats.

    Issue 3 fix: all_matches = union of hard+clay+grass rows (each with correct surface
      label via surface_override), sorted by date desc, capped at 20.
      No GameCourt filter is applied when All is selected — the union covers all surfaces.
    """
    tour_lc = tour.lower()
    pid = str(player_id)

    # ── 1. All past matches (no surface filter) → total count for career stats ─
    all_raw: list = []
    for page in range(1, 16):
        resp = get_player_past_matches(
            pid, tour_lc, page_size=100, page_no=page, filter_str=_YEAR_FILTER,
        )
        batch = _extract_matches(resp)
        if not batch:
            break
        all_raw.extend(batch)
        if len(batch) < 100:
            break

    total_matches = len(all_raw)

    # ── 2. Career stats (All column) ─────────────────────────────────────────
    career_stats = _compute_career_stats(pid, tour_lc, total_matches) if total_matches else {}

    # ── 3. Surface win/loss from surface-summary endpoint ────────────────────
    surface_wins: dict[str, tuple[int, int]] = {
        "Hard": (0, 0), "Clay": (0, 0), "Grass": (0, 0),
    }
    summary_resp = get_player_surface_summary(pid, tour_lc)
    if summary_resp:
        raw_data = summary_resp.get("data") or summary_resp
        entries: list = []
        if isinstance(raw_data, list):
            entries = raw_data
        elif isinstance(raw_data, dict):
            entries = list(raw_data.values()) if raw_data else []
        for item in entries:
            if not isinstance(item, dict):
                continue
            surfaces_list = item.get("surfaces")
            if isinstance(surfaces_list, list):
                for s in surfaces_list:
                    _accumulate_surface(s, surface_wins)
            else:
                _accumulate_surface(item, surface_wins)

    # ── 4. Surface-specific matches WITH stats (Issue 1) ─────────────────────
    # User confirmed filter values: GameCourt:1=Hard, GameCourt:2=Clay, GameCourt:3=Grass
    def _fetch_surface_matches(court_filter: str, max_pages: int = 5) -> list:
        rows: list = []
        for page in range(1, max_pages + 1):
            r = get_player_past_matches(
                pid, tour_lc, page_size=100, page_no=page,
                filter_str=court_filter, include="stat",
            )
            batch = _extract_matches(r)
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < 100:
                break
        return rows

    hard_raw  = _fetch_surface_matches("GameCourt:1", max_pages=5)
    clay_raw  = _fetch_surface_matches("GameCourt:2", max_pages=5)
    grass_raw = _fetch_surface_matches("GameCourt:3", max_pages=3)

    logger.info("Surface match counts for player %s — Hard:%d Clay:%d Grass:%d",
                pid, len(hard_raw), len(clay_raw), len(grass_raw))

    # Per-surface serve/return stats (None values → UI shows N/A)
    hard_computed  = _compute_surface_stats_from_matches(hard_raw, pid)
    clay_computed  = _compute_surface_stats_from_matches(clay_raw, pid)
    grass_computed = _compute_surface_stats_from_matches(grass_raw, pid)

    # ── 5. Assemble surface stat dicts ────────────────────────────────────────
    def _surface_dict(computed: dict, surface_name: str) -> dict:
        wins, total = surface_wins[surface_name]
        # Start from surface-computed stats; fall back to career stats if insufficient data
        d = dict(computed) if computed else dict(career_stats)
        d["win_rate"] = round(wins / total * 100, 1) if total > 0 else None
        d["matches_played"] = total or len(
            hard_raw if surface_name == "Hard" else
            clay_raw if surface_name == "Clay" else grass_raw
        )
        return d

    all_w = sum(1 for m in all_raw if str(m.get("match_winner") or "") == pid)
    all_stats: dict = dict(career_stats)
    all_stats["win_rate"] = round(all_w / total_matches * 100, 1) if total_matches else None
    all_stats["matches_played"] = total_matches

    hard_stats  = _surface_dict(hard_computed,  "Hard")
    clay_stats  = _surface_dict(clay_computed,  "Clay")
    grass_stats = _surface_dict(grass_computed, "Grass")

    # ── 6. Match history rows (Issue 3) ──────────────────────────────────────
    # Each surface set already fetched above; apply surface_override for correct labels.
    # all_matches = union of all three → no GameCourt filter, sorted by date, top 20.
    hard_rows  = sorted([_parse_match_row(m, pid, "Hard")  for m in hard_raw],
                        key=lambda x: x.get("date", ""), reverse=True)
    clay_rows  = sorted([_parse_match_row(m, pid, "Clay")  for m in clay_raw],
                        key=lambda x: x.get("date", ""), reverse=True)
    grass_rows = sorted([_parse_match_row(m, pid, "Grass") for m in grass_raw],
                        key=lambda x: x.get("date", ""), reverse=True)

    all_rows = sorted(
        hard_rows + clay_rows + grass_rows,
        key=lambda x: x.get("date", ""), reverse=True,
    )[:20]
    form = all_rows[:10]

    # ── Sofascore fallback ────────────────────────────────────────────────────
    if not career_stats and not all_raw:
        try:
            from src.api.sofascore import get_player_stats_by_surface as ss
            return ss(player_id)
        except Exception:
            pass

    return {
        "All":   all_stats,
        "Hard":  hard_stats,
        "Clay":  clay_stats,
        "Grass": grass_stats,
        "form":  form,
        "all_matches":   all_rows,
        "Hard_matches":  hard_rows,
        "Clay_matches":  clay_rows,
        "Grass_matches": grass_rows,
    }


def _accumulate_surface(s: dict, surface_wins: dict) -> None:
    """Add one surface-row's wins/losses into the running totals dict."""
    try:
        court_id = int(s.get("courtId") or s.get("gameCourt") or 0)
    except Exception:
        return
    wins   = int(s.get("courtWins")   or s.get("wins")   or 0)
    losses = int(s.get("courtLosses") or s.get("losses") or 0)
    total  = wins + losses
    surf   = SURFACE_MAP.get(court_id)
    if surf and surf in surface_wins:
        old_w, old_t = surface_wins[surf]
        surface_wins[surf] = (old_w + wins, old_t + total)


def _parse_surface_summary(resp) -> dict:
    """
    Try to parse player-surface-summary into {all:{...}, Hard:{...}, Clay:{...}, Grass:{...}}.
    Handles multiple possible response shapes gracefully.
    """
    if not resp:
        return {}
    data = resp
    if isinstance(resp, dict):
        data = resp.get("data") or resp.get("results") or resp

    result = {"all": {}, "Hard": {}, "Clay": {}, "Grass": {}}

    stat_map = {
        "win_rate":                    ("winRate", "winPct", "win_rate"),
        "aces":                        ("aceGm", "aces", "aceAvg"),
        "double_faults":               ("dfGm", "dfs", "dfAvg"),
        "first_serve_pct":             ("firstServePct", "firstServePercent"),
        "first_serve_pts_won":         ("firstServeWonPct", "firstServePtsWon"),
        "second_serve_pts_won":        ("secondServeWonPct", "secondServePtsWon"),
        "return_first_serve_pts_won":  ("retFirstWonPct", "returnFirstServePtsWon"),
        "return_second_serve_pts_won": ("retSecondWonPct", "returnSecondServePtsWon"),
        "bp_converted":                ("bpConvertedPct", "bpConverted"),
        "bp_saved":                    ("bpSavedPct", "bpSaved"),
        "matches_played":              ("matches", "matchCount", "total"),
    }

    def _extract_stats(src: dict) -> dict:
        out = {}
        for our_key, api_keys in stat_map.items():
            for ak in api_keys:
                v = src.get(ak)
                if v is not None:
                    try:
                        out[our_key] = float(v)
                    except Exception:
                        pass
                    break
        return out

    if isinstance(data, dict):
        surf_keys = {
            "all":   ("all", "overall", "total"),
            "Hard":  ("hard", "Hard", "HARD"),
            "Clay":  ("clay", "Clay", "CLAY"),
            "Grass": ("grass", "Grass", "GRASS"),
        }
        for our_surf, candidates in surf_keys.items():
            for ck in candidates:
                v = data.get(ck)
                if isinstance(v, dict):
                    result[our_surf] = _extract_stats(v)
                    break
        # If the dict IS the stats directly (no surface nesting)
        if not any(result.values()):
            result["all"] = _extract_stats(data)

    elif isinstance(data, list):
        for item in data:
            court_id = item.get("gameCourt") or item.get("courtId") or item.get("surface") or 0
            try:
                court_id = int(court_id)
            except Exception:
                court_id = 0
            surf = SURFACE_MAP.get(court_id)
            if surf:
                result[surf] = _extract_stats(item)
            result["all"] = _extract_stats(item) if not result["all"] else result["all"]

    return result


def _fill_missing(stats: dict, src: dict) -> None:
    """Copy keys from src into stats only where stats has None / is missing."""
    for k, v in src.items():
        if v is not None and (stats.get(k) is None):
            stats[k] = v


# ── ts_to_date_str (backward compat) ─────────────────────────────────────────

def ts_to_date_str(ts) -> str:
    if not ts:
        return "—"
    try:
        if isinstance(ts, str) and len(ts) >= 10 and ts[4] == "-":
            return ts[:10]
        return datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return str(ts)[:10]


# ── High-level adapter: H2H ───────────────────────────────────────────────────

def get_h2h_summary(tour: str, p1: str, p2: str, surface: str | None = None) -> dict:
    """
    Returns H2H summary compatible with the UI:
      {total, p1_wins, p2_wins, surface_matches, surface_p1_wins, surface_p2_wins,
       h2h_rate, matches (DataFrame), surface_matches_df (DataFrame)}
    p1 / p2 should be player IDs (strings).
    Falls back to Sackmann if Matchstat returns nothing.
    """
    empty = {
        "total": 0, "p1_wins": 0, "p2_wins": 0,
        "surface_matches": 0, "surface_p1_wins": 0, "surface_p2_wins": 0,
        "h2h_rate": 0.5, "matches": pd.DataFrame(), "surface_matches_df": pd.DataFrame(),
    }

    p1_id, p2_id = str(p1), str(p2)
    tour_lc = tour.lower()

    resp = get_h2h_matches(p1_id, p2_id, tour_lc, page_size=100, page_no=1)
    raw = _extract_matches(resp)

    if not raw:
        return _sackmann_h2h_fallback(tour, p1, p2, surface, empty)

    total = len(raw)

    def _is_p1_winner(m: dict) -> bool:
        winner = str(m.get("match_winner") or m.get("winnerId") or "")
        if winner:
            return winner == p1_id
        # fallback: assume player1 is our p1 (less reliable)
        return str(m.get("player1Id") or "") == p1_id

    p1_wins = sum(1 for m in raw if _is_p1_winner(m))
    p2_wins = total - p1_wins

    court_ids = SURFACE_ID_MAP.get(surface, []) if surface else []
    surf_matches = [
        m for m in raw
        if not court_ids or
           int(m.get("gameCourt") or m.get("courtId") or 0) in court_ids
    ] if surface else []

    surf_total = len(surf_matches)
    surf_p1w = sum(1 for m in surf_matches if _is_p1_winner(m))
    surf_p2w = surf_total - surf_p1w
    h2h_rate = surf_p1w / surf_total if surf_total > 0 else (p1_wins / total if total > 0 else 0.5)

    def _extract_name(field_val) -> str:
        if isinstance(field_val, dict):
            return field_val.get("name") or ""
        return str(field_val) if field_val else ""

    def _build_df(match_list):
        rows = []
        for m in match_list:
            winner_id_m = str(m.get("match_winner") or m.get("winnerId") or "")
            is_p1 = str(m.get("player1Id") or "") == p1_id
            won = (p1_id == winner_id_m) if winner_id_m else is_p1
            court_id = m.get("gameCourt") or m.get("courtId") or 0
            try:
                court_id = int(court_id)
            except Exception:
                court_id = 0
            surf_name = SURFACE_MAP.get(court_id, "Hard")
            date_raw = m.get("date") or m.get("gameDate") or m.get("matchDate") or ""
            if isinstance(date_raw, (int, float)) and date_raw > 1e8:
                try:
                    date_raw = datetime.utcfromtimestamp(date_raw).strftime("%Y-%m-%d")
                except Exception:
                    date_raw = str(date_raw)
            opp_raw = m.get("player2") if is_p1 else m.get("player1")
            opp_name = (_extract_name(opp_raw)
                        or (m.get("player2Name") if is_p1 else m.get("player1Name"))
                        or "")
            rows.append({
                "Match Date": str(date_raw)[:10],
                "Tournament": m.get("tournamentName") or m.get("tournament") or "",
                "Surface": surf_name,
                "Result": "W" if won else "L",
                "Opponent": opp_name,
                "Score": m.get("result") or m.get("score") or "",
            })
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    return {
        "total": total,
        "p1_wins": p1_wins,
        "p2_wins": p2_wins,
        "surface_matches": surf_total,
        "surface_p1_wins": surf_p1w,
        "surface_p2_wins": surf_p2w,
        "h2h_rate": h2h_rate,
        "matches": _build_df(raw),
        "surface_matches_df": _build_df(surf_matches),
    }


def _sackmann_h2h_fallback(tour, p1, p2, surface, empty):
    try:
        from src.api.sackmann import get_h2h_summary as sack
        return sack(tour, p1, p2, surface=surface)
    except Exception:
        return empty


def get_h2h_stat_avg(tour: str, p1: str, p2: str, surface: str | None = None) -> dict:
    """Returns {ace, df, games_avg} averages for p1 in H2H matches."""
    empty = {"ace": None, "df": None, "games_avg": None}
    p1_id, p2_id = str(p1), str(p2)
    court_ids = SURFACE_ID_MAP.get(surface, []) if surface else []

    resp = get_h2h_matches(p1_id, p2_id, tour.lower(), page_size=50, page_no=1)
    raw = _extract_matches(resp)
    if not raw:
        try:
            from src.api.sackmann import get_h2h_stat_avg as sack
            return sack(tour, p1, p2, surface=surface)
        except Exception:
            return empty

    if court_ids:
        raw = [m for m in raw if int(m.get("gameCourt") or m.get("courtId") or 0) in court_ids]
    if not raw:
        return empty

    ace_sum = df_sum = games_sum = n = 0
    for m in raw:
        # Match objects don't expose per-player ace/df in H2H lists; use 0
        games_sum += m.get("totalGames") or m.get("games") or 0
        n += 1

    return {
        "ace": round(ace_sum / n, 2) if n else None,
        "df":  round(df_sum / n, 2) if n else None,
        "games_avg": round(games_sum / n, 1) if n and games_sum else None,
    }


# ── format_h2h_table (compatible with head_to_head.py) ───────────────────────

def format_h2h_table(df: pd.DataFrame, p1_name: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    expected = {"Match Date", "Tournament", "Surface", "Result", "Opponent", "Score"}
    if expected.issubset(set(df.columns)):
        return df.sort_values("Match Date", ascending=False).reset_index(drop=True)
    return df


# ── Tournament record modifier ────────────────────────────────────────────────

def get_tournament_record_modifier(player_id: str, tournament_id: str, tour: str = "ATP") -> float:
    """Returns ±5% modifier based on player's record at this tournament."""
    if not tournament_id:
        return 0.0
    resp = get_player_tournament_record(str(player_id), str(tournament_id), tour.lower())
    if not resp:
        return 0.0
    data = resp
    if isinstance(resp, dict):
        data = resp.get("data") or resp.get("results") or resp
    w = t = 0
    if isinstance(data, dict):
        w = int(data.get("wins") or data.get("w") or 0)
        l = int(data.get("losses") or data.get("l") or 0)
        t = int(data.get("total") or data.get("matches") or (w + l))
    elif isinstance(data, list):
        for item in data:
            w += int(item.get("wins") or 0)
            t += int(item.get("total") or item.get("matches") or 0)
    if t < 3:
        return 0.0
    wr = w / t
    if wr >= 0.75:   return 5.0
    elif wr >= 0.60: return 2.5
    elif wr <= 0.25: return -5.0
    elif wr <= 0.40: return -2.5
    return 0.0


# ── Connection / endpoint test ────────────────────────────────────────────────

def run_connection_test() -> None:
    logger.info("=== Matchstat API connection test ===")

    # Test 1: search
    r1 = search_api("Sinner")
    if r1 is not None:
        logger.info("search('Sinner') OK — keys: %s | preview: %s",
                    list(r1.keys()) if isinstance(r1, dict) else type(r1),
                    json.dumps(r1)[:300])
    else:
        logger.error("search('Sinner') FAILED")

    # Test 2: singles ranking
    r2 = get_singles_ranking("atp")
    if r2 is not None:
        logger.info("get_singles_ranking('atp') OK — keys: %s | preview: %s",
                    list(r2.keys()) if isinstance(r2, dict) else type(r2),
                    json.dumps(r2)[:300])
    else:
        logger.error("get_singles_ranking('atp') FAILED")

    # Test 3: player profile
    r3 = get_player_info("106421", "atp")
    if r3 is not None:
        logger.info("get_player_info(106421) OK — keys: %s | preview: %s",
                    list(r3.keys()) if isinstance(r3, dict) else type(r3),
                    json.dumps(r3)[:300])
    else:
        logger.error("get_player_info(106421) FAILED")
