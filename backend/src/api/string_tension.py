"""
Dynamic String Tension ST Pace Index scraper.

Fetches https://stringtension.com/tournament-calendar/{year} and extracts
the ST Pace Index for each tournament. Results are cached for 6 hours so
they survive across multiple prop calculations without hammering the site.

Fallback: if the scrape fails for any reason, callers use the hardcoded
COURT_CPR values from constants.py. The scraper never raises — it always
returns a (possibly empty) dict.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_CACHE: dict = {"ts": 0.0, "data": None}
_CACHE_TTL = 6 * 3600   # 6 hours

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Patterns we look for in the page HTML — strings like "ST Pace Index: 37.7"
# or "Pace Index: 37.7". We're liberal here to cover different page layouts.
# One calendar ROW in the flattened page text, e.g.
#   "250 ATP Umag Jul 13, 2026 S Clay · Court Pace Index: 32.5"
# Captures (name, surface, pace). "Court" is optional so the older "ST Pace
# Index" / bare "Pace Index" wording still parses if the site reverts.
_ROW_PATTERN = re.compile(
    r"([A-Z][A-Za-z'’\-\. ]{2,40}?)\s+"        # tournament name (tour prefix trimmed later)
    r"[A-Z][a-z]{2}\s+\d{1,2},\s*\d{4}\s+"     # "Jul 13, 2026"
    r"[A-Z]?\s*(Clay|Hard|Grass|Carpet)\s*"    # surface
    r".{0,3}?(?:Court\s+|ST\s+)?Pace\s+Index\s*[:\-–]\s*([\d.]+)",
    re.IGNORECASE,
)

_PACE_PATTERNS = [
    re.compile(r"Court\s+Pace\s+Index\s*[:\-–]\s*([\d.]+)", re.IGNORECASE),
    re.compile(r"ST\s+Pace\s+Index\s*[:\-–]\s*([\d.]+)", re.IGNORECASE),
    re.compile(r"Pace\s+Index\s*[:\-–]\s*([\d.]+)", re.IGNORECASE),
    re.compile(r"SPI\s*[:\-–]\s*([\d.]+)", re.IGNORECASE),
]

# Patterns that typically precede a tournament name block on the page
_TOURNAMENT_SECTION_PATTERN = re.compile(
    r"(?P<name>[A-Z][A-Za-z\s'\-]{4,60})"   # tournament name (rough)
    r"(?:.{0,400}?)"                          # some content in between
    r"(?:ST\s+Pace\s+Index|Pace\s+Index|SPI)"
    r"\s*[:\-–]\s*(?P<pace>[\d.]+)",
    re.DOTALL | re.IGNORECASE,
)


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _fetch_calendar(year: int) -> Optional[str]:
    url = f"https://stringtension.com/tournament-calendar/{year}"
    try:
        r = requests.get(url, headers=_HEADERS, timeout=12)
        if r.status_code == 200:
            logger.info("[ST] fetched calendar %d (%d chars)", year, len(r.text))
            return r.text
        logger.warning("[ST] calendar %d returned HTTP %d", year, r.status_code)
    except Exception as exc:
        logger.warning("[ST] calendar %d fetch failed: %s", year, exc)
    return None


def _parse_calendar(html: str) -> dict:
    """
    Parse all (tournament_name, pace_index) pairs from the HTML.
    Returns {tournament_name: pace_index} with raw names as scraped.
    """
    results: dict = {}

    # ── PRIMARY: parse the FLATTENED page text ───────────────────────────────
    # The site rebuilt as a React app and renamed the metric. Two changes, each
    # enough on its own to kill the old parse:
    #   1. "ST Pace Index" is now "Court Pace Index".
    #   2. The tournament name and the value are in SEPARATE elements, so the
    #      old pattern — which required a name and a value close together in
    #      raw HTML — could never match again.
    # The result was silent: _parse_calendar returned {}, every lookup answered
    # ('no_data'), and every court fell back to the hardcoded COURT_CPR in
    # constants.py. Nothing errored, so nothing surfaced it.
    #
    # Stripping the tags first turns each row back into the line a reader sees:
    #   "250 ATP Umag Jul 13, 2026 S Clay · Court Pace Index: 32.5"
    # which is stable against markup churn in a way an HTML-shaped regex is not.
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for m in _ROW_PATTERN.finditer(text):
        name = re.sub(r"^(ATP|WTA)\s+", "", m.group(1).strip()).strip()
        try:
            pace = float(m.group(3))
        except (TypeError, ValueError):
            continue
        if name and 10.0 <= pace <= 80.0:
            results[name] = pace
    if results:
        logger.info("[ST] parsed %d tournaments from flattened text", len(results))
        return results

    for m in _TOURNAMENT_SECTION_PATTERN.finditer(html):
        name = m.group("name").strip()
        try:
            pace = float(m.group("pace"))
            if 10.0 <= pace <= 80.0:   # sanity range
                results[name] = pace
        except (TypeError, ValueError):
            pass

    # Fallback: scan the whole page for orphan "ST Pace Index: X" values
    # and associate them with the nearest preceding heading-like text.
    if not results:
        raw_hits = []
        for pat in _PACE_PATTERNS:
            for m in pat.finditer(html):
                try:
                    pace = float(m.group(1))
                    if 10.0 <= pace <= 80.0:
                        raw_hits.append((m.start(), pace))
                except (TypeError, ValueError):
                    pass
        if raw_hits:
            logger.info("[ST] fallback: found %d raw pace values in page", len(raw_hits))
            # We can't reliably assign names without tournament structure,
            # so return them indexed by position for diagnostic logging only.
            for pos, pace in raw_hits[:10]:
                logger.info("[ST]   pos=%d pace=%.1f", pos, pace)

    logger.info("[ST] parsed %d named tournaments from calendar", len(results))
    return results


def _get_cache_data(force: bool = False) -> dict:
    now = time.time()
    if not force and _CACHE["data"] is not None and (now - _CACHE["ts"]) < _CACHE_TTL:
        return _CACHE["data"]

    year = datetime.utcnow().year
    html = _fetch_calendar(year)
    if html:
        data = _parse_calendar(html)
    else:
        data = {}

    _CACHE["ts"]   = now
    _CACHE["data"] = data
    return data


def lookup_pace_index(tournament_name: str, fallback: Optional[float] = None) -> tuple:
    """
    Look up the ST Pace Index for a tournament from the scraped calendar.

    Returns (pace_index, source) where source is:
      'st_live'     — matched from this year's scraped calendar
      'fallback'    — the provided fallback value (from COURT_CPR)
      'no_data'     — no pace index available

    Never raises.
    """
    if not tournament_name:
        return fallback, "fallback" if fallback else "no_data"
    try:
        data = _get_cache_data()
        if not data:
            return fallback, "fallback" if fallback is not None else "no_data"

        # Try exact match first (case-insensitive), then fuzzy match at ≥0.80
        name_lc = tournament_name.lower().strip()
        for scraped_name, pace in data.items():
            if scraped_name.lower().strip() == name_lc:
                logger.info("[ST] exact match: %r → %.1f", tournament_name, pace)
                return pace, "st_live"

        best_sim, best_name, best_pace = 0.0, None, None
        for scraped_name, pace in data.items():
            sim = _similarity(tournament_name, scraped_name)
            if sim > best_sim:
                best_sim, best_name, best_pace = sim, scraped_name, pace

        if best_sim >= 0.80 and best_pace is not None:
            logger.info("[ST] fuzzy match: %r → %r (%.2f) → %.1f",
                        tournament_name, best_name, best_sim, best_pace)
            return best_pace, "st_live"

        logger.debug("[ST] no match for %r (best=%.2f)", tournament_name, best_sim)
    except Exception as exc:
        logger.debug("[ST] lookup_pace_index failed: %s", exc)

    return fallback, "fallback" if fallback is not None else "no_data"


def get_full_calendar() -> dict:
    """Return the full cached calendar dict for diagnostic endpoints."""
    try:
        return _get_cache_data()
    except Exception:
        return {}
