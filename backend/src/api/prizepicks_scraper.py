"""
PrizePicks tennis board scraper.

Hits the public partner-api endpoint:
    https://partner-api.prizepicks.com/projections?per_page=1000

No authentication, no league discovery, no pagination — one call returns
every active projection across every sport, and we filter tennis in-memory.

Returns a normalised list of props:
    [
      {
        "player_name":     "Carlos Alcaraz",
        "opponent_name":   "Jannik Sinner",     # may be None
        "prop_type":       "Aces",              # one of our 4 eligible types
        "prop_line":       6.5,
        "match_time":      "2026-05-30T13:00:00Z",
        "tournament":      "Roland Garros",
        "league":          "TENNIS",            # raw league name from PP
        "pp_projection_id": "1234567",
      },
      ...
    ]
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# ── Endpoint ────────────────────────────────────────────────────────────────
_PP_API_URL = "https://partner-api.prizepicks.com/projections"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


# ── Eligible prop types ─────────────────────────────────────────────────────
# Map raw PrizePicks stat_type (case-insensitive) → our internal vocabulary.
# Anything not in this map is silently dropped.
_PROP_MAP = {
    # Aces variants
    "aces":                 "Aces",
    # Double faults variants
    "double faults":        "Double Faults",
    "double fault":         "Double Faults",
    "df":                   "Double Faults",
    # Break points variants
    "break points won":     "Break Points Won",
    "break points":         "Break Points Won",
    "breaks":               "Break Points Won",
    "break points scored":  "Break Points Won",
    # Total games variants
    "total games":          "Total Games",
    "games played":         "Total Games",
    "match games":          "Total Games",
}


def _normalize_prop_type(raw: str) -> Optional[str]:
    if not raw:
        return None
    return _PROP_MAP.get(str(raw).strip().lower())


# ── Tennis detection ────────────────────────────────────────────────────────
def _is_tennis_league(name: Optional[str]) -> bool:
    if not name:
        return False
    n = str(name).lower()
    # PrizePicks tennis leagues observed: "TENNIS" (ATP), "WTA",
    # "TENNIS DOUBLES" (excluded — we only do singles)
    if "doubles" in n:
        return False
    return ("tennis" in n) or ("wta" in n) or ("atp" in n)


# ── In-memory cache ─────────────────────────────────────────────────────────
_CACHE: dict = {"ts": 0.0, "data": None}
_CACHE_TTL = 30 * 60   # 30 minutes


def clear_cache() -> None:
    _CACHE["ts"]   = 0.0
    _CACHE["data"] = None


# ── Low-level fetch ─────────────────────────────────────────────────────────
def _fetch_raw(per_page: int = 1000) -> dict:
    """GET the partner-api endpoint and return parsed JSON. No proxy needed."""
    r = requests.get(
        _PP_API_URL,
        params={"per_page": per_page, "single_stat": "true"},
        headers=_HEADERS,
        timeout=20,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"PrizePicks partner-api returned HTTP {r.status_code}: {r.text[:200]}"
        )
    try:
        return r.json()
    except Exception as exc:
        raise RuntimeError(f"PrizePicks partner-api returned non-JSON: {exc}")


# ── Index helpers ───────────────────────────────────────────────────────────
def _build_included_index(included: list) -> dict:
    """Index a JSON:API `included` list by (type, id)."""
    idx: dict = {}
    for obj in included or []:
        t = obj.get("type")
        i = obj.get("id")
        if t and i:
            idx[(t, str(i))] = obj
    return idx


def _player_from_rel(rels: dict, idx: dict) -> tuple:
    """
    Returns (player_name, player_league) from the new_player/player relationship.
    Tries new_player first then falls back to player.
    """
    for rel_key in ("new_player", "player"):
        rel = (rels.get(rel_key) or {}).get("data") or {}
        ptype, pid = rel.get("type"), str(rel.get("id") or "")
        if not (ptype and pid):
            continue
        obj = idx.get((ptype, pid))
        if not obj:
            continue
        attrs = obj.get("attributes") or {}
        name = (attrs.get("display_name")
                or attrs.get("name")
                or attrs.get("short_name"))
        league = attrs.get("league")
        if name:
            return name, league
    return None, None


def _league_name_from_rel(rels: dict, idx: dict) -> Optional[str]:
    """Pull the league name from the league relationship if present."""
    rel = (rels.get("league") or {}).get("data") or {}
    ltype, lid = rel.get("type"), str(rel.get("id") or "")
    if ltype and lid:
        obj = idx.get((ltype, lid))
        if obj:
            return (obj.get("attributes") or {}).get("name")
    return None


def _opponent_from_description(desc: str) -> Optional[str]:
    """
    PrizePicks partner-api puts the opponent name directly in the
    `description` field (verified live: e.g. "Zachary Svajda"). Older API
    variants used "X vs Y" — we support both.
    """
    if not desc:
        return None
    s = str(desc).strip()
    if not s:
        return None

    # Older "X vs Y" / "X v. Y" / "X v Y" variants
    for sep in (" vs ", " VS ", " v. ", " v "):
        if sep in s:
            rhs = s.split(sep, 1)[1]
            opp = rhs.split(",")[0].split("(")[0].strip()
            return opp.title() if opp.islower() else opp

    # partner-api: description IS the opponent name (already a clean string).
    # Skip if it's obviously something else (numeric, very long, contains @, etc).
    if any(ch in s for ch in ("@", "{", "}", ";")):
        return None
    if s.replace(" ", "").isdigit():
        return None
    if len(s) > 60:
        return None
    return s


# ── Public entry point ─────────────────────────────────────────────────────
def scrape_board(force_refresh: bool = False) -> dict:
    """
    Returns:
      {
        "props":         [ ...normalised prop dicts... ],
        "scraped_at":    epoch seconds,
        "ok":            bool,
        "error":         None or str,
        "cached":        bool,
        "n_total":       total projections in API response (all sports),
        "n_tennis":      tennis projections (any stat type),
        "n_eligible":    tennis projections matching our 4 prop types,
        "stat_types_seen": dict { raw_stat_type: count } for tennis only,
        "unmapped_stat_types": list of tennis stat_types we don't yet map,
      }
    """
    now = time.time()
    if not force_refresh and _CACHE["data"] and (now - _CACHE["ts"]) < _CACHE_TTL:
        cached = dict(_CACHE["data"])
        cached["cached"] = True
        return cached

    try:
        payload = _fetch_raw(per_page=1000)
    except Exception as exc:
        logger.exception("[PP] partner-api fetch failed: %s", exc)
        if _CACHE["data"]:
            stale = dict(_CACHE["data"])
            stale["cached"] = True
            stale["ok"]     = False
            stale["error"]  = f"Live fetch failed — showing cached data: {exc}"
            return stale
        return {
            "props":              [],
            "scraped_at":         now,
            "ok":                 False,
            "error":              f"PrizePicks board unavailable — try refreshing. ({exc})",
            "cached":             False,
            "n_total":            0,
            "n_tennis":           0,
            "n_eligible":         0,
            "stat_types_seen":    {},
            "unmapped_stat_types": [],
        }

    data     = payload.get("data") or []
    included = payload.get("included") or []
    idx      = _build_included_index(included)

    logger.info(
        "[PP] partner-api returned %d projections (%d included objects)",
        len(data), len(included),
    )

    tennis_props: list = []
    stat_type_counts: dict = {}

    # For debug: capture the first 3 tennis projections' full field set
    debug_samples: list = []

    for proj in data:
        attrs = proj.get("attributes") or {}
        rels  = proj.get("relationships") or {}

        # Tennis detection — check both the projection's league relationship
        # AND the player's league attribute. Either is sufficient.
        league_from_proj   = _league_name_from_rel(rels, idx)
        player_name, player_league = _player_from_rel(rels, idx)
        league = league_from_proj or player_league

        if not _is_tennis_league(league):
            continue
        if not player_name:
            continue
        # Filter out doubles teams — names like "Kempen M / Klepac A" carry
        # a "/" which our singles-only model can't handle.
        if "/" in player_name:
            continue

        raw_stat = attrs.get("stat_type") or ""
        stat_type_counts[raw_stat] = stat_type_counts.get(raw_stat, 0) + 1

        # Capture sample data for the first 3 tennis projections (any stat type)
        if len(debug_samples) < 3:
            debug_samples.append({
                "projection_id":      proj.get("id"),
                "raw_attributes":     attrs,
                "raw_relationships":  list(rels.keys()),
                "resolved_player":    player_name,
                "resolved_league":    league,
            })

        prop_type = _normalize_prop_type(raw_stat)
        if not prop_type:
            continue   # eligible-tennis-but-not-our-stat-type

        line_val = attrs.get("line_score")
        if line_val is None:
            continue
        try:
            line_val = float(line_val)
        except (TypeError, ValueError):
            continue

        description = attrs.get("description") or ""
        opponent = _opponent_from_description(description)

        tennis_props.append({
            "player_name":      player_name,
            "opponent_name":    opponent,
            "prop_type":        prop_type,
            "prop_line":        line_val,
            "match_time":       attrs.get("start_time") or attrs.get("starts_at"),
            "tournament":       attrs.get("game") or attrs.get("tournament"),
            "description":      description,
            "league":           league,
            "pp_projection_id": str(proj.get("id") or ""),
        })

    # Surface unmapped stat types so we can extend the map later
    unmapped = sorted({
        st for st in stat_type_counts
        if st and _normalize_prop_type(st) is None
    })

    # Log debug samples so the exact API shape is visible in Railway logs
    if debug_samples:
        logger.info("[PP] DEBUG — first 3 tennis projections:")
        for i, sample in enumerate(debug_samples, 1):
            logger.info("[PP]   sample %d: %s", i, sample)

    logger.info(
        "[PP] %d tennis projections; %d eligible after stat-type filter. "
        "stat_types_seen=%s. unmapped=%s",
        sum(stat_type_counts.values()), len(tennis_props),
        stat_type_counts, unmapped,
    )

    result = {
        "props":              tennis_props,
        "scraped_at":         now,
        "ok":                 True,
        "error":              None,
        "cached":             False,
        "n_total":            len(data),
        "n_tennis":           sum(stat_type_counts.values()),
        "n_eligible":         len(tennis_props),
        "stat_types_seen":    stat_type_counts,
        "unmapped_stat_types": unmapped,
    }
    _CACHE["ts"]   = now
    _CACHE["data"] = result
    return result


# ── Debug helper used by /api/board/test ───────────────────────────────────
def fetch_raw_sample() -> dict:
    """
    For the GET /api/board/test endpoint. Returns the raw partner-api payload
    plus the first 5 tennis-eligible projections with every field surfaced
    so we can verify the API shape without any processing layer in between.
    """
    payload = _fetch_raw(per_page=1000)
    data     = payload.get("data") or []
    included = payload.get("included") or []
    idx      = _build_included_index(included)

    samples: list = []
    for proj in data:
        attrs = proj.get("attributes") or {}
        rels  = proj.get("relationships") or {}
        league_from_proj = _league_name_from_rel(rels, idx)
        player_name, player_league = _player_from_rel(rels, idx)
        league = league_from_proj or player_league
        if not _is_tennis_league(league):
            continue
        samples.append({
            "projection_id":      proj.get("id"),
            "league":             league,
            "resolved_player":    player_name,
            "raw_attributes":     attrs,
            "relationship_keys":  list(rels.keys()),
        })
        if len(samples) >= 5:
            break

    return {
        "endpoint":        _PP_API_URL,
        "n_data":          len(data),
        "n_included":      len(included),
        "n_tennis_found":  len(samples),
        "samples":         samples,
    }
