"""
PrizePicks tennis board scraper.

Hits PrizePicks' public-facing projections API directly through the Decodo
proxy. This is much lighter than driving Playwright and works reliably from
Railway containers (no browser binaries needed).

Caches scraped boards for 30 minutes. Callers must use scrape_board() —
which handles caching transparently — rather than the lower-level helpers.

Returns a normalised list of props:
    [
      {
        "player_name":     "Carlos Alcaraz",
        "opponent_name":   "Jannik Sinner",     # may be None
        "prop_type":       "Aces",              # one of our 4 eligible types
        "prop_line":       6.5,
        "match_time":      "2026-05-30T13:00:00Z",
        "tournament":      "Roland Garros",
        "league":          "TENNIS",            # "TENNIS" (ATP) or "WTA"
        "pp_projection_id": "1234567",
      },
      ...
    ]
"""
from __future__ import annotations

import logging
import os
import random
import time
from typing import Optional

from curl_cffi import requests as cc_requests

logger = logging.getLogger(__name__)

# ── Decodo proxy config (shared pattern with sofascore/tennis_abstract) ──
_PROXY_HOST  = os.getenv("PROXY_HOST", "gate.decodo.com")
_PROXY_USER  = os.getenv("PROXY_USERNAME")
_PROXY_PASS  = os.getenv("PROXY_PASSWORD")
_PROXY_PORTS = [
    int(p.strip())
    for p in os.getenv("PROXY_PORT_LIST", "").split(",")
    if p.strip().isdigit()
]


def _proxy_url() -> Optional[str]:
    if not (_PROXY_PORTS and _PROXY_USER and _PROXY_HOST):
        return None
    port = random.choice(_PROXY_PORTS)
    return f"http://{_PROXY_USER}:{_PROXY_PASS}@{_PROXY_HOST}:{port}"


# ── Eligible prop types ─────────────────────────────────────────────────────
# Map PrizePicks stat_type strings → our internal prop type vocabulary.
# PrizePicks uses inconsistent naming so we match permissively.
_ELIGIBLE_PROP_MAP = {
    "fantasy score":        None,    # skipped
    "total games":          "Total Games",
    "total games won":      None,    # skipped — different prop
    "aces":                 "Aces",
    "double faults":        "Double Faults",
    "break points won":     "Break Points Won",
    "break points":         "Break Points Won",   # PP variants
    "break points scored":  "Break Points Won",
    "games won":            None,    # skipped — single-player count, different
    "sets won":             None,    # skipped
    "match games":          "Total Games",
}


def _normalize_prop_type(raw: str) -> Optional[str]:
    if not raw:
        return None
    key = str(raw).strip().lower()
    return _ELIGIBLE_PROP_MAP.get(key)


# ── In-memory cache ─────────────────────────────────────────────────────────
_CACHE: dict = {"ts": 0.0, "data": None}
_CACHE_TTL = 30 * 60   # 30 minutes


def clear_cache() -> None:
    _CACHE["ts"]   = 0.0
    _CACHE["data"] = None


# ── Low-level fetch ─────────────────────────────────────────────────────────
_PP_LEAGUES_URL     = "https://api.prizepicks.com/leagues?per_page=200"
_PP_PROJECTIONS_URL = "https://api.prizepicks.com/projections"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin":          "https://app.prizepicks.com",
    "Referer":         "https://app.prizepicks.com/",
}


def _fetch_json(url: str, params: Optional[dict] = None, retries: int = 3):
    """GET `url` through Decodo proxy. Retries on proxy/timeout errors."""
    last_err = None
    for attempt in range(retries):
        proxy = _proxy_url()
        proxies = {"http": proxy, "https": proxy} if proxy else None
        try:
            r = cc_requests.get(
                url,
                params=params,
                headers=_HEADERS,
                proxies=proxies,
                impersonate="chrome120",
                timeout=20,
            )
            if r.status_code == 200:
                return r.json()
            logger.warning("[PP] %s %s -> HTTP %d (attempt %d)",
                           url, params or {}, r.status_code, attempt + 1)
            last_err = f"HTTP {r.status_code}"
        except Exception as exc:
            logger.warning("[PP] fetch error attempt %d: %s", attempt + 1, exc)
            last_err = str(exc)
        time.sleep(0.6 + attempt * 0.4)
    raise RuntimeError(f"PrizePicks fetch failed for {url}: {last_err}")


# ── League discovery ────────────────────────────────────────────────────────
# Tennis leagues on PrizePicks are labelled in the `attributes.name` field —
# typical values include "TENNIS" (ATP men's), "WTA" (women's), and sometimes
# "TENNIS DOUBLES". We discover them dynamically so league-id changes don't
# break the scraper.
def _discover_tennis_leagues() -> list:
    try:
        data = _fetch_json(_PP_LEAGUES_URL)
    except Exception as exc:
        logger.error("[PP] league discovery failed: %s", exc)
        return []
    leagues = []
    for item in data.get("data", []):
        attrs = item.get("attributes") or {}
        name  = (attrs.get("name") or "").strip()
        if not name:
            continue
        name_lc = name.lower()
        if "tennis" in name_lc or "wta" in name_lc or "atp" in name_lc:
            leagues.append({"id": str(item.get("id")), "name": name})
    logger.info("[PP] discovered tennis leagues: %s", leagues)
    return leagues


# ── Projection parsing ──────────────────────────────────────────────────────
def _parse_projections(payload: dict, league_name: str) -> list:
    """
    PrizePicks responses are JSON:API formatted:
      data:     list of projection objects (with relationships)
      included: list of related objects (players, stat_types, leagues, ...)
    We need to join projection → player → stat_type to build a flat record.
    """
    if not isinstance(payload, dict):
        return []

    included = payload.get("included") or []
    # Index included objects by (type, id) for quick lookup
    idx: dict = {}
    for obj in included:
        t = obj.get("type")
        i = obj.get("id")
        if t and i:
            idx[(t, str(i))] = obj

    props = []
    for proj in payload.get("data", []):
        attrs = proj.get("attributes") or {}
        rels  = proj.get("relationships") or {}

        prop_type = _normalize_prop_type(attrs.get("stat_type"))
        if not prop_type:
            continue

        line_val = attrs.get("line_score")
        if line_val is None:
            continue
        try:
            line_val = float(line_val)
        except (TypeError, ValueError):
            continue

        # Resolve player name
        player_name = None
        player_rel  = (rels.get("new_player") or {}).get("data") or {}
        ptype, pid  = player_rel.get("type"), str(player_rel.get("id") or "")
        if ptype and pid:
            p_obj = idx.get((ptype, pid))
            if p_obj:
                p_attrs = p_obj.get("attributes") or {}
                player_name = p_attrs.get("display_name") or p_attrs.get("name")

        if not player_name:
            continue

        # Opponent / tournament — PrizePicks often embeds these in
        # description fields. We don't always have them.
        description = attrs.get("description") or ""
        opponent_name = None
        if " vs " in description.lower():
            try:
                rhs = description.lower().split(" vs ")[1]
                opponent_name = rhs.split(",")[0].split("(")[0].strip().title()
            except Exception:
                pass

        props.append({
            "player_name":      player_name,
            "opponent_name":    opponent_name,
            "prop_type":        prop_type,
            "prop_line":        line_val,
            "match_time":       attrs.get("start_time") or attrs.get("starts_at"),
            "tournament":       attrs.get("game") or attrs.get("tournament"),
            "description":      description,
            "league":           league_name,
            "pp_projection_id": str(proj.get("id") or ""),
        })

    return props


# ── Public entry point ─────────────────────────────────────────────────────
def scrape_board(force_refresh: bool = False) -> dict:
    """
    Returns:
      {
        "props":         [ ...normalised prop dicts... ],
        "scraped_at":    epoch seconds,
        "leagues_found": [ {id, name} ... ],
        "ok":            bool,
        "error":         None or str,
        "cached":        bool — True if served from cache,
      }
    """
    now = time.time()
    if not force_refresh and _CACHE["data"] and (now - _CACHE["ts"]) < _CACHE_TTL:
        cached = dict(_CACHE["data"])
        cached["cached"] = True
        return cached

    try:
        leagues = _discover_tennis_leagues()
        if not leagues:
            raise RuntimeError("No tennis leagues found on PrizePicks board")

        all_props: list = []
        for lg in leagues:
            try:
                payload = _fetch_json(
                    _PP_PROJECTIONS_URL,
                    params={"league_id": lg["id"], "per_page": 250, "single_stat": "true"},
                )
                parsed = _parse_projections(payload, lg["name"])
                logger.info("[PP] league=%s id=%s -> %d eligible props",
                            lg["name"], lg["id"], len(parsed))
                all_props.extend(parsed)
            except Exception as exc:
                logger.warning("[PP] league %s fetch failed: %s", lg["name"], exc)

        result = {
            "props":         all_props,
            "scraped_at":    now,
            "leagues_found": leagues,
            "ok":            True,
            "error":         None,
            "cached":        False,
        }
        _CACHE["ts"]   = now
        _CACHE["data"] = result
        return result

    except Exception as exc:
        logger.exception("[PP] scrape_board failed: %s", exc)
        # On total failure, return any cached data if we have it; otherwise
        # report the error so the UI can show a clear message.
        if _CACHE["data"]:
            stale = dict(_CACHE["data"])
            stale["cached"] = True
            stale["ok"]     = False
            stale["error"]  = f"Live scrape failed — showing cached data: {exc}"
            return stale
        return {
            "props":         [],
            "scraped_at":    now,
            "leagues_found": [],
            "ok":            False,
            "error":         f"Unable to reach PrizePicks board: {exc}",
            "cached":        False,
        }
