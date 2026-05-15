"""
Tennis Abstract client — curl-cffi + JS regex extraction, 24-hour cache.

Tennis Abstract pages are JavaScript-rendered but the match data is embedded
as JavaScript arrays in <script> tags inside the static HTML (44 KB).  We
fetch the raw HTML with curl-cffi (no headless browser needed) and extract
the data arrays with regex + json.loads.

Public interface
----------------
  format_ta_name(name: str) -> str
  get_player_ta_stats(name: str, tour: str = "ATP") -> dict | None   [async]

Returned dict shape (all fields optional — None if not found):
  {
    "handedness":   "R" | "L" | None,
    "surface_stats": {
        "All":  {"matches": int, "ace_pct": float, "df_pct": float,
                 "first_in_pct": float, "first_won_pct": float,
                 "second_won_pct": float, "bp_saved_pct": float,
                 "bp_conv_pct": float},
        "Hard": {...}, "Clay": {...}, "Grass": {...},
    },
    "rank_splits":  {"top10": float|None, "11to50": float|None,
                     "51plus": float|None},
    "matches":      [{"Date","Tournament","Surface","Rd","Rk","vRk",
                      "Score","A%","DF%","1stIn","1st%","2nd%","BPSvd"}, ...],
    "career_splits":  [],
    "mcp_serve":      [],
    "mcp_return":     [],
    "h2h_records":    [],
    "_source":        "curl_cffi_regex",
  }
"""

import asyncio
import json
import logging
import os
import random
import re
import time
import unicodedata
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)

# ── Proxy env vars ─────────────────────────────────────────────────────────────
_PROXY_HOST  = os.getenv("PROXY_HOST",     "gate.decodo.com")
_PROXY_USER  = os.getenv("PROXY_USERNAME", "")
_PROXY_PASS  = os.getenv("PROXY_PASSWORD", "")
_PROXY_PORTS = [
    int(p.strip())
    for p in os.getenv("PROXY_PORT_LIST", "").split(",")
    if p.strip().isdigit()
]

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache: dict = {}
_CACHE_TTL   = 86_400   # 24 hours

# ── Constants ──────────────────────────────────────────────────────────────────
TA_BASE = "https://www.tennisabstract.com/cgi-bin/player.cgi"

# Tennis Abstract match log — fixed column indices (0-based)
# Confirmed layout from TA source: the JS array `var d = [...]` has
# columns in this order:
_COL = {
    "date":       0,
    "tournament": 1,
    "surface":    2,   # "H", "C", "G"
    "round":      3,
    "rank":       4,   # player rank at time
    "opp_rank":   5,
    "result":     6,   # "W" or "L" (sometimes combined with score at col 7)
    "score":      7,
    "dr":         8,   # Dominance Ratio
    "ace_pct":    9,   # A%
    "df_pct":     10,  # DF%
    "first_in":   11,  # 1stIn
    "first_won":  12,  # 1st%
    "second_won": 13,  # 2nd%
    "bp_saved":   14,  # BPSvd%
    "time":       15,  # optional
}

# ─────────────────────────────────────────────────────────────────────────────
# Name formatting
# ─────────────────────────────────────────────────────────────────────────────

def format_ta_name(player_name: str) -> str:
    """
    Convert display name to Tennis Abstract URL slug.
      "Alexander Zverev"   -> "AlexanderZverev"
      "Jo-Wilfried Tsonga" -> "Jo-WilfriedTsonga"
    Strips spaces/apostrophes, strips diacritics, keeps hyphens.
    """
    nfkd     = unicodedata.normalize("NFKD", player_name)
    ascii_nm = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[ ']", "", ascii_nm)


def _alt_name_formats(name: str) -> list:
    """Generate alternative slug variants to try if the primary slug fails."""
    parts = name.strip().split()
    if len(parts) < 2:
        return []
    first, *rest = parts
    last  = rest[-1]
    slug_primary    = format_ta_name(name)
    slug_last_first = format_ta_name(f"{last} {first}")
    alts = []
    if slug_last_first != slug_primary:
        alts.append(slug_last_first)
    # Drop suffixes (Jr., II, etc.)
    clean = " ".join(p for p in parts
                     if not re.match(r'^(Jr|Sr|II|III)\.?$', p, re.I))
    if clean != name:
        alts.append(format_ta_name(clean))
    return alts


# ─────────────────────────────────────────────────────────────────────────────
# Proxy helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pick_proxy_url() -> Optional[str]:
    if not (_PROXY_PORTS and _PROXY_USER and _PROXY_HOST):
        return None
    port = random.choice(_PROXY_PORTS)
    return f"http://{_PROXY_USER}:{_PROXY_PASS}@{_PROXY_HOST}:{port}"


# ─────────────────────────────────────────────────────────────────────────────
# HTML fetch via curl-cffi
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_html(ta_name: str) -> Optional[str]:
    """Fetch raw HTML from Tennis Abstract with curl-cffi."""
    url       = f"{TA_BASE}?p={ta_name}"
    proxy_url = _pick_proxy_url()
    try:
        from curl_cffi import requests as cf
        session = cf.Session(impersonate="chrome120")
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept":   "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer":  "https://www.tennisabstract.com/",
        })
        if proxy_url:
            session.proxies = {"http": proxy_url, "https": proxy_url}
        r = session.get(url, timeout=20)
        if r.status_code != 200:
            logger.warning("[TA] HTTP %d for %s", r.status_code, ta_name)
            return None
        logger.info("[TA] fetched %s (%d bytes)", ta_name, len(r.text))
        return r.text
    except Exception as exc:
        logger.error("[TA] fetch error for %s: %s", ta_name, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# JS array extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_js_array_text(html: str, var_name: str) -> Optional[str]:
    """
    Find `var <var_name> = [...]` and return the raw text of the array value,
    using balanced-bracket counting so we capture nested arrays correctly.
    """
    start_pat = re.compile(
        rf'var\s+{re.escape(var_name)}\s*=\s*\[', re.DOTALL
    )
    m = start_pat.search(html)
    if not m:
        return None

    start = m.end() - 1          # index of the opening '['
    depth       = 0
    in_string   = False
    str_char    = None
    escape_next = False

    for i in range(start, len(html)):
        c = html[i]
        if escape_next:
            escape_next = False
            continue
        if c == "\\" and in_string:
            escape_next = True
            continue
        if in_string:
            if c == str_char:
                in_string = False
        else:
            if c in ('"', "'"):
                in_string = True
                str_char  = c
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return html[start : i + 1]
    return None


def _parse_js_array(raw_text: str) -> Optional[list]:
    """
    Parse the raw JavaScript array text to a Python list.
    Tries json.loads first; falls back to cleaning trailing commas.
    """
    if not raw_text:
        return None
    # Direct parse
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        pass
    # Remove trailing commas before ] or }
    cleaned = re.sub(r",\s*(?=[}\]])", "", raw_text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    return None


def _extract_match_array(html: str) -> list:
    """
    Try several known Tennis Abstract variable names for the match log.
    Returns a list of rows (each row is a list of values) or [].
    """
    for var in ("d", "data", "matchdata", "matches"):
        raw = _extract_js_array_text(html, var)
        if raw:
            parsed = _parse_js_array(raw)
            if isinstance(parsed, list) and parsed:
                # Sanity check: rows should be lists with at least ~10 elements
                sample = [r for r in parsed if isinstance(r, list) and len(r) >= 10]
                if sample:
                    logger.info("[TA] extracted var %s: %d rows", var, len(sample))
                    return parsed
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Handedness extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_handedness(html: str) -> Optional[str]:
    """
    Extract player handedness (R / L) from static HTML.
    Tries multiple patterns in order of reliability.
    """
    # Pattern 1: JavaScript variable   var plhand = "R";
    m = re.search(r'var\s+plhand\s*=\s*["\']([RL])["\']', html, re.I)
    if m:
        return m.group(1).upper()

    # Pattern 2: plays / hand keyword followed by R or L
    m = re.search(
        r'(?:plays|hand(?:ed)?|handed)\s*[:\-=]?\s*["\']?\b([RL])\b["\']?',
        html, re.I
    )
    if m:
        return m.group(1).upper()

    # Pattern 3: "right-handed" / "left-handed" text
    if re.search(r'right[- ]?handed', html, re.I):
        return "R"
    if re.search(r'left[- ]?handed', html, re.I):
        return "L"

    # Pattern 4: id="plhand" element content
    m = re.search(r'id=["\']plhand["\'][^>]*>([RL])<', html, re.I)
    if m:
        return m.group(1).upper()

    # Pattern 5: "Plays: Right" or "Plays: Left" (full word)
    m = re.search(r'plays\s*:?\s*(right|left)', html, re.I)
    if m:
        hand = m.group(1).lower()
        return "R" if hand == "right" else "L"

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Rank-splits extraction from HTML
# ─────────────────────────────────────────────────────────────────────────────

def _extract_rank_splits_html(html: str) -> dict:
    """
    Attempt to find win-rate splits vs rank groups from the career-splits
    section.  TA embeds these in a separate JS variable or table.
    Returns {"top10": float|None, "11to50": float|None, "51plus": float|None}.
    """
    splits = {"top10": None, "11to50": None, "51plus": None}

    # Try to find a splits JS variable (var splits = [...] or var csplit = [...])
    for var in ("splits", "csplit", "career_splits", "ranksplits"):
        raw = _extract_js_array_text(html, var)
        if raw:
            parsed = _parse_js_array(raw)
            if isinstance(parsed, list):
                for row in parsed:
                    if not isinstance(row, (list, dict)):
                        continue
                    if isinstance(row, dict):
                        label = str(row.get("") or row.get("Split") or
                                    row.get("Category") or "").lower()
                        val   = _pf(str(row.get("W%") or row.get("Win%") or ""))
                    else:
                        if len(row) < 2:
                            continue
                        label = str(row[0]).lower()
                        val   = _pf(str(row[-1]))
                    if val is None:
                        continue
                    if "top 10" in label or "top10" in label:
                        splits["top10"] = val
                    elif ("11" in label and "50" in label) or "11-50" in label:
                        splits["11to50"] = val
                    elif "51" in label or "50+" in label or "outside" in label:
                        splits["51plus"] = val
                if any(v is not None for v in splits.values()):
                    return splits

    # Fallback: look for rank-split win% in the raw HTML text
    # Pattern: "Top 10  W 4 L 12" or similar
    patterns = [
        (r'(?:top\s*10|rank\s*1-10)[^\d]*(\d+)\s*[-/W]\s*(\d+)', "top10"),
        (r'(?:rank\s*11[-–]50|11\s*to\s*50)[^\d]*(\d+)\s*[-/W]\s*(\d+)', "11to50"),
        (r'(?:rank\s*51\+|51\s*(?:plus|and\s+above|–\s*\d+))[^\d]*(\d+)\s*[-/W]\s*(\d+)', "51plus"),
    ]
    for pat, key in patterns:
        m = re.search(pat, html, re.I)
        if m:
            wins   = int(m.group(1))
            losses = int(m.group(2))
            total  = wins + losses
            if total > 0:
                splits[key] = round(wins / total * 100, 1)

    return splits


# ─────────────────────────────────────────────────────────────────────────────
# Match-row parsing
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_surface(raw: str) -> Optional[str]:
    r = str(raw).strip().lower()
    if r in ("h", "hard", "i", "indoor hard"):
        return "Hard"
    if r in ("c", "clay", "r", "red clay"):
        return "Clay"
    if r in ("g", "grass", "lawn"):
        return "Grass"
    # Full-word check as fallback
    if "clay" in r:
        return "Clay"
    if "grass" in r or "lawn" in r:
        return "Grass"
    if "hard" in r:
        return "Hard"
    return None


def _pf(s) -> Optional[float]:
    """Parse numeric / percent string to float, or None."""
    if s is None:
        return None
    try:
        return float(str(s).strip().rstrip("%").replace(",", "."))
    except Exception:
        return None


def _parse_match_rows(rows: list) -> list:
    """
    Convert raw JS array rows into normalised match dicts.
    Handles both 15-column and 16-column (with separate W/L) layouts.
    """
    parsed = []
    for row in rows:
        if not isinstance(row, list):
            continue

        n = len(row)
        if n < 10:
            continue

        # Detect layout: if col[6] is "W" or "L" we have separate result/score
        # Otherwise col[6] may start with "W " or "L " (result embedded in score)
        col6 = str(row[6]).strip().upper() if n > 6 else ""

        if col6 in ("W", "L"):
            # 16-col layout: 0..5 = meta, 6=result, 7=score, 8=DR, 9=A%, ...14=BPSvd
            ace_idx, df_idx, fin_idx, fwon_idx, swon_idx, bpsv_idx = 9, 10, 11, 12, 13, 14
            score_idx = 7
        else:
            # 15-col layout: result+score merged in col[6]; 7=DR, 8=A%, ...13=BPSvd
            ace_idx, df_idx, fin_idx, fwon_idx, swon_idx, bpsv_idx = 8, 9, 10, 11, 12, 13
            score_idx = 6

        def _get(idx):
            return row[idx] if n > idx else None

        surface = _normalize_surface(_get(2) or "")
        if not surface:
            continue   # skip rows with unknown surface

        parsed.append({
            "Date":       str(_get(0) or ""),
            "Tournament": str(_get(1) or ""),
            "Surface":    surface,
            "Rd":         str(_get(3) or ""),
            "Rk":         str(_get(4) or ""),
            "vRk":        str(_get(5) or ""),
            "Score":      str(_get(score_idx) or ""),
            "A%":         str(_get(ace_idx)  or ""),
            "DF%":        str(_get(df_idx)   or ""),
            "1stIn":      str(_get(fin_idx)  or ""),
            "1st%":       str(_get(fwon_idx) or ""),
            "2nd%":       str(_get(swon_idx) or ""),
            "BPSvd":      str(_get(bpsv_idx) or ""),
        })

    return parsed


# ─────────────────────────────────────────────────────────────────────────────
# Surface stat aggregation
# ─────────────────────────────────────────────────────────────────────────────

def _aggregate_matches(matches: list) -> dict:
    """Compute per-surface average stats from parsed match dicts."""
    if not matches:
        return {}

    by_surface: dict = defaultdict(list)
    for m in matches:
        surf = m.get("Surface")
        if surf:
            by_surface[surf].append(m)
            by_surface["All"].append(m)

    result = {}
    for surf, rows in by_surface.items():
        def avg(key):
            vals = [_pf(r.get(key)) for r in rows]
            vals = [v for v in vals if v is not None]
            return round(sum(vals) / len(vals), 2) if vals else None

        bp_saved = avg("BPSvd")
        result[surf] = {
            "matches":        len(rows),
            "ace_pct":        avg("A%"),
            "df_pct":         avg("DF%"),
            "first_in_pct":   avg("1stIn"),
            "first_won_pct":  avg("1st%"),
            "second_won_pct": avg("2nd%"),
            "bp_saved_pct":   bp_saved,
            "bp_conv_pct":    (
                round(100 - bp_saved, 2) if bp_saved is not None else None
            ),
        }

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Main parse function
# ─────────────────────────────────────────────────────────────────────────────

def _parse_html(html: str, ta_name: str) -> dict:
    """Extract all TA data from static HTML."""
    handedness   = _extract_handedness(html)
    raw_rows     = _extract_match_array(html)
    matches      = _parse_match_rows(raw_rows)
    surface_stats= _aggregate_matches(matches)
    rank_splits  = _extract_rank_splits_html(html)

    logger.info(
        "[TA] %s parsed: hand=%s  matches=%d  surfaces=%s",
        ta_name, handedness, len(matches), list(surface_stats.keys()),
    )

    return {
        "handedness":    handedness,
        "matches":       matches,
        "surface_stats": surface_stats,
        "career_splits": [],
        "mcp_serve":     [],
        "mcp_return":    [],
        "h2h_records":   [],
        "rank_splits":   rank_splits,
        "_source":       "curl_cffi_regex",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public async API
# ─────────────────────────────────────────────────────────────────────────────

async def get_player_ta_stats(
    player_name: str,
    tour: str = "ATP",
) -> Optional[dict]:
    """
    Fetch and cache Tennis Abstract stats for player_name.

    Uses curl-cffi to fetch the static HTML, then extracts the JavaScript
    data arrays embedded in the page via regex.  Falls back to alternative
    name slugs if the primary slug returns no usable data.

    Returns None only if every attempt fails.  Never raises.
    """
    if not player_name or not player_name.strip():
        return None

    ta_name = format_ta_name(player_name)
    now     = time.time()

    cached = _cache.get(ta_name)
    if cached and (now - cached["ts"]) < _CACHE_TTL:
        logger.debug("[TA] cache hit for %s", ta_name)
        return cached["data"]

    logger.info("[TA] fetching %s  slug=%s", player_name, ta_name)

    # Run curl-cffi fetch in thread pool (it's blocking I/O)
    loop = asyncio.get_event_loop()

    html = await loop.run_in_executor(None, _fetch_html, ta_name)

    data = _parse_html(html, ta_name) if html else None

    # If we got nothing useful, try alternative slugs
    if not _is_useful(data):
        for alt in _alt_name_formats(player_name):
            logger.info("[TA] trying alt slug %s", alt)
            alt_html = await loop.run_in_executor(None, _fetch_html, alt)
            alt_data = _parse_html(alt_html, alt) if alt_html else None
            if _is_useful(alt_data):
                data = alt_data
                break

    # Cache result (even None — prevents hammering on persistent 404)
    _cache[ta_name] = {"ts": now, "data": data}

    if data:
        logger.info(
            "[TA] %s -> hand=%s  matches=%d  surfaces=%s",
            ta_name,
            data.get("handedness"),
            len(data.get("matches", [])),
            list(data.get("surface_stats", {}).keys()),
        )
    else:
        logger.warning("[TA] no data for %s", ta_name)

    return data


def _is_useful(data: Optional[dict]) -> bool:
    """Return True if data contains at least handedness or surface stats."""
    if not data:
        return False
    return bool(data.get("handedness") or data.get("surface_stats"))
