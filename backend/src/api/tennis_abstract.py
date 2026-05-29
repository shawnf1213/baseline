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
from datetime import datetime, timedelta
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
TA_BASE      = "https://www.tennisabstract.com/cgi-bin/player.cgi"
TA_JSFRAGS   = "https://www.tennisabstract.com/jsfrags/{slug}.js"

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


def _fetch_jsfrags(ta_name: str) -> Optional[str]:
    """Fetch the jsfrags JS file which contains pre-rendered HTML tables."""
    url = TA_JSFRAGS.format(slug=ta_name)
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
            "Referer": "https://www.tennisabstract.com/",
        })
        if proxy_url:
            session.proxies = {"http": proxy_url, "https": proxy_url}
        r = session.get(url, timeout=20)
        if r.status_code != 200:
            logger.warning("[TA] jsfrags HTTP %d for %s", r.status_code, ta_name)
            return None
        logger.info("[TA] jsfrags fetched %s (%d bytes)", ta_name, len(r.text))
        return r.text
    except Exception as exc:
        logger.error("[TA] jsfrags fetch error for %s: %s", ta_name, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# HTML table parsing from jsfrags
# ─────────────────────────────────────────────────────────────────────────────

def _extract_soup_from_jsfrags(js_text: str):
    """
    Extract the main HTML fragment from the jsfrags JS template literal and
    return a BeautifulSoup object, or None if parsing fails.
    """
    try:
        from bs4 import BeautifulSoup
        # jsfrags uses JS template literals: var player_frag = `...`;
        tl_matches = re.findall(r'var\s+\w+\s*=\s*`([\s\S]*?)`\s*;', js_text)
        if not tl_matches:
            return None
        # Pick the largest fragment (the main one with all tables)
        html_frag = max(tl_matches, key=len)
        return BeautifulSoup(html_frag, "html.parser")
    except Exception as exc:
        logger.error("[TA] soup extraction error: %s", exc)
        return None


def _parse_career_splits_soup(soup) -> dict:
    """
    Parse the career-splits table for per-surface serve statistics AND
    handedness splits (vs. Lefties / vs. Righties).

    Returns dict keyed by surface ("Hard", "Clay", "Grass") plus handedness
    splits under key "handedness_splits":
      {
        "vs_left":  {"ace_pct", "bp_saved_pct", "first_won_pct", "matches"},
        "vs_right": {...},
      }
    """
    t = soup.find("table", id="career-splits")
    if not t:
        return {}

    rows = t.find_all("tr")
    if not rows:
        return {}

    headers = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
    h = {v: i for i, v in enumerate(headers)}

    surface_map = {"Hard": "Hard", "Clay": "Clay", "Grass": "Grass"}
    # Labels TA uses for handedness splits (case-insensitive checked below)
    lefty_labels  = {"vs. lefties", "vs lefties", "vs. left", "lefties"}
    righty_labels = {"vs. righties", "vs righties", "vs. right", "righties"}

    stats = {}
    handedness_splits: dict = {}

    for row in rows[1:]:
        cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
        if not cells:
            continue
        split_label = cells[0].strip()
        split_lower = split_label.lower()

        def pct(key, _cells=cells, _h=h):
            idx = _h.get(key)
            if idx is None or idx >= len(_cells):
                return None
            return _pf(_cells[idx])

        m_idx = h.get("M")
        try:
            n_matches = int(cells[m_idx]) if m_idx is not None and m_idx < len(cells) else 0
        except (ValueError, TypeError):
            n_matches = 0

        if split_label in surface_map:
            surf = surface_map[split_label]
            stats[surf] = {
                "matches":        n_matches,
                "ace_pct":        pct("A%"),
                "df_pct":         pct("DF%"),
                "first_in_pct":   pct("1stIn"),
                "first_won_pct":  pct("1st%"),
                "second_won_pct": pct("2nd%"),
                "bp_saved_pct":   None,   # filled by _enrich_bpsaved_soup
                "bp_conv_pct":    None,
            }
        elif split_lower in lefty_labels:
            bp_sv = pct("BPSvd") or pct("BP Svd")
            handedness_splits["vs_left"] = {
                "matches":       n_matches,
                "ace_pct":       pct("A%"),
                "first_won_pct": pct("1st%"),
                "bp_saved_pct":  bp_sv,
                "bp_conv_pct":   round(100 - bp_sv, 1) if bp_sv is not None else None,
            }
        elif split_lower in righty_labels:
            bp_sv = pct("BPSvd") or pct("BP Svd")
            handedness_splits["vs_right"] = {
                "matches":       n_matches,
                "ace_pct":       pct("A%"),
                "first_won_pct": pct("1st%"),
                "bp_saved_pct":  bp_sv,
                "bp_conv_pct":   round(100 - bp_sv, 1) if bp_sv is not None else None,
            }

    if handedness_splits:
        logger.info("[TA] handedness_splits: %s", list(handedness_splits.keys()))
    stats["handedness_splits"] = handedness_splits
    return stats


def _enrich_bpsaved_soup(soup, surface_stats: dict) -> None:
    """
    Populate bp_saved_pct (and bp_conv_pct) in surface_stats by aggregating
    the X/Y BPSvd fractions from the recent-results table.
    """
    t = soup.find("table", id="recent-results")
    if not t:
        return

    rows = t.find_all("tr")
    if not rows:
        return

    headers = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
    h = {v: i for i, v in enumerate(headers)}

    surf_col   = h.get("Surface")
    bpsvd_col  = h.get("BPSvd")
    if surf_col is None or bpsvd_col is None:
        return

    bp_data: dict = defaultdict(lambda: [0, 0])   # surf -> [saved, total]

    for row in rows[1:]:
        cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
        if len(cells) <= max(surf_col, bpsvd_col):
            continue

        surf = _normalize_surface(cells[surf_col])
        if not surf or surf not in surface_stats:
            continue

        m = re.match(r"(\d+)/(\d+)", cells[bpsvd_col])
        if m:
            bp_data[surf][0] += int(m.group(1))
            bp_data[surf][1] += int(m.group(2))

    for surf, (saved, total) in bp_data.items():
        if total > 0 and surf in surface_stats:
            pct = round(100.0 * saved / total, 1)
            surface_stats[surf]["bp_saved_pct"] = pct
            surface_stats[surf]["bp_conv_pct"]  = round(100.0 - pct, 1)


def _parse_rank_splits_soup(soup) -> dict:
    """
    Extract rank-split win rates from the career-splits table
    (rows labelled 'vs Top 10', etc.).
    """
    splits = {"top10": None, "11to50": None, "51plus": None}
    t = soup.find("table", id="career-splits")
    if not t:
        return splits

    rows = t.find_all("tr")
    if not rows:
        return splits

    headers = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
    h = {v: i for i, v in enumerate(headers)}
    win_pct_col = h.get("Win%")

    for row in rows[1:]:
        cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
        if not cells or win_pct_col is None or win_pct_col >= len(cells):
            continue
        label = cells[0].lower()
        val   = _pf(cells[win_pct_col])
        if val is None:
            continue
        if "top 10" in label or "top10" in label:
            splits["top10"] = val

    return splits


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
    Includes a 'Result' field ('W' or 'L') for win-rate computation.
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
            result = col6
        else:
            # 15-col layout: result+score merged in col[6]; 7=DR, 8=A%, ...13=BPSvd
            ace_idx, df_idx, fin_idx, fwon_idx, swon_idx, bpsv_idx = 8, 9, 10, 11, 12, 13
            score_idx = 6
            # First character of merged field is W or L
            result = col6[0] if col6 and col6[0] in ("W", "L") else ""

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
            "Result":     result,
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
# Rich stats building from raw match rows
# ─────────────────────────────────────────────────────────────────────────────

def _parse_match_year(date_str: str) -> Optional[int]:
    """Extract 4-digit year from Tennis Abstract date strings like '2025-04-13'."""
    if not date_str:
        return None
    m = re.search(r'\b(20\d{2})\b', date_str)
    if m:
        return int(m.group(1))
    return None


_DATE_PATTERN = re.compile(r'(20\d{2})[-/](\d{1,2})[-/](\d{1,2})')
_YEAR_ONLY    = re.compile(r'\b(20\d{2})\b')


def _parse_match_date(date_str: str):
    """
    Parse a Tennis Abstract date string into a `datetime.date`.

    Handles:
      - "2025-04-13" / "2025/04/13"        (primary TA format)
      - "20250413"                          (compact)
      - Year-only fallback: returns Jul 1 of the matched year so the date
        still falls inside a 52-week window for matches from the current year
    Returns None only when nothing parseable is found.
    """
    if not date_str:
        return None
    s = str(date_str).strip()

    m = _DATE_PATTERN.search(s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except (ValueError, TypeError):
            pass

    # Compact YYYYMMDD
    if len(s) >= 8 and s[:8].isdigit():
        try:
            return datetime.strptime(s[:8], "%Y%m%d").date()
        except ValueError:
            pass

    # Last resort: year-only → assume mid-year so it still lands inside a
    # 52-week window for the current year's matches.
    m = _YEAR_ONLY.search(s)
    if m:
        try:
            return datetime(int(m.group(1)), 7, 1).date()
        except (ValueError, TypeError):
            pass
    return None


def _filter_matches_within(matches: list, days: int) -> list:
    """
    Return the subset of `matches` whose Date is within `days` days of today.
    Matches with unparseable dates are dropped.
    """
    if not matches:
        return []
    cutoff = (datetime.utcnow() - timedelta(days=days)).date()
    out = []
    for m in matches:
        d = _parse_match_date(m.get("Date"))
        if d and d >= cutoff:
            out.append(m)
    return out


def _agg_ta_matches(match_list: list) -> dict:
    """
    Aggregate a list of Tennis Abstract match dicts into a surface stats block.
    Returns the same shape as surface_stats entries.
    """
    if not match_list:
        return {"matches": 0}

    n = len(match_list)

    def avg(key):
        vals = [_pf(r.get(key)) for r in match_list]
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    # BP saved: aggregate fraction denominators for accuracy
    bp_saved_n, bp_total_n = 0, 0
    for m in match_list:
        bpsv = str(m.get("BPSvd", ""))
        fm = re.match(r"(\d+)/(\d+)", bpsv)
        if fm:
            bp_saved_n += int(fm.group(1))
            bp_total_n += int(fm.group(2))

    bp_saved_pct = round(100.0 * bp_saved_n / bp_total_n, 1) if bp_total_n > 0 else avg("BPSvd")
    bp_conv_pct = round(100.0 - bp_saved_pct, 1) if bp_saved_pct is not None else None

    wins = sum(1 for m in match_list if m.get("Result") == "W")

    return {
        "matches":        n,
        "win_rate":       round(wins / n * 100, 1) if n > 0 else None,
        "ace_pct":        avg("A%"),
        "df_pct":         avg("DF%"),
        "first_in_pct":   avg("1stIn"),
        "first_won_pct":  avg("1st%"),
        "second_won_pct": avg("2nd%"),
        "bp_saved_pct":   bp_saved_pct,
        "bp_conv_pct":    bp_conv_pct,
    }


def _build_rich_stats(matches: list) -> dict:
    """
    Build rich multi-tier aggregations from raw parsed match rows.
    Returns a dict consumed by get_blended_stats().
    """
    current_year = datetime.utcnow().year
    recent_years = {current_year, current_year - 1, current_year - 2, current_year - 3}

    # Bucket matches
    recent_3yr: dict = defaultdict(list)
    curr_yr: dict    = defaultdict(list)
    by_surface: dict = defaultdict(list)

    for m in matches:
        yr  = _parse_match_year(m.get("Date", ""))
        srf = m.get("Surface")
        if not srf:
            continue

        by_surface[srf].append(m)
        by_surface["All"].append(m)

        if yr and yr in recent_years:
            recent_3yr[srf].append(m)
            recent_3yr["All"].append(m)

        if yr and yr == current_year:
            curr_yr[srf].append(m)
            curr_yr["All"].append(m)

    def _per_surf(bucket: dict) -> dict:
        return {k: _agg_ta_matches(v) for k, v in bucket.items()}

    # Last 20 on each surface
    last20: dict = {}
    for srf in ("Hard", "Clay", "Grass"):
        last20[srf] = _agg_ta_matches(by_surface[srf][:20])

    # Surface match log — up to 10 most recent raw rows per surface
    surf_log: dict = {}
    for srf in ("Hard", "Clay", "Grass"):
        surf_log[srf] = by_surface[srf][:10]

    # ── Recent-form windows (for Prop Projection tab) ─────────────────────────
    # last_52_weeks: matches in the last 364 days — primary source for props
    # last_2yr:      matches in the last 730 days — fallback when 52w is thin
    last_52w_all   = _filter_matches_within(matches, 364)
    last_2yr_all   = _filter_matches_within(matches, 730)

    last_52w_by_surf: dict = defaultdict(list)
    last_2yr_by_surf: dict = defaultdict(list)
    for m in last_52w_all:
        srf = m.get("Surface")
        if srf:
            last_52w_by_surf[srf].append(m)
            last_52w_by_surf["All"].append(m)
    for m in last_2yr_all:
        srf = m.get("Surface")
        if srf:
            last_2yr_by_surf[srf].append(m)
            last_2yr_by_surf["All"].append(m)

    return {
        "all_surfaces":       _agg_ta_matches(by_surface.get("All", [])),
        "by_surface":         _per_surf(by_surface),
        "recent_3yr":         _per_surf(recent_3yr),
        "current_year":       _per_surf(curr_yr),
        "last_20_on_surface": last20,
        "surface_match_log":  surf_log,
        "last_10_matches":    matches[:10],
        # Recent-form windows for Prop Projection tab — career data is NOT used
        # for prop projections because it includes seasons where the player
        # may have been at a very different level.
        "last_52_weeks":      _per_surf(last_52w_by_surf),
        "last_2yr_fallback":  _per_surf(last_2yr_by_surf),
    }


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

def _compute_ace_against_from_matches(matches: list) -> dict:
    """
    Compute ace-against-per-match per surface from TA match rows.

    TA match rows don't directly expose opponent aces, so this is a best-effort
    approximation: players with lower BPSvd tend to face more aces (opponents
    serve more dominantly against them). We return None per surface if sample
    is too small — SS ace_against_per_match (computed from opp_aces field) is
    the preferred source.
    """
    # TA match rows don't have opponent ace counts — return empty dict.
    # The Sofascore ace_against_per_match computed from opp_aces is used instead.
    return {}


def _parse_html(html: str, ta_name: str) -> dict:
    """Extract player metadata and raw match array from the main static HTML."""
    handedness  = _extract_handedness(html)
    rank_splits = _extract_rank_splits_html(html)

    # Extract raw match array from embedded JS variable
    raw_rows = _extract_match_array(html)
    matches  = _parse_match_rows(raw_rows) if raw_rows else []

    # Build surface stats from raw matches (used if jsfrags unavailable)
    surface_stats_from_matches = _aggregate_matches(matches) if matches else {}

    # Build rich multi-tier stats (legacy; kept for backward compat)
    rich_stats = _build_rich_stats(matches) if matches else {}

    # Handedness splits — populated from jsfrags career-splits table
    # (not available from plain HTML without the jsfrags file)
    handedness_splits: dict = {}

    logger.info(
        "[TA] %s parsed: hand=%s  match_rows=%d  surfaces=%s",
        ta_name, handedness, len(matches),
        list(surface_stats_from_matches.keys()),
    )

    return {
        "handedness":        handedness,
        "handedness_splits": handedness_splits,   # vs_left / vs_right (from jsfrags)
        "ace_against_per_match": None,            # computed from SS opp_aces; placeholder
        "matches":           matches,
        "surface_stats":     surface_stats_from_matches,
        "rich_stats":        rich_stats,
        "career_splits":     [],
        "mcp_serve":         [],
        "mcp_return":        [],
        "h2h_records":       [],
        "rank_splits":       rank_splits,
        "_source":           "curl_cffi_html_table",
    }


def _parse_jsfrags(js_text: str, ta_name: str) -> dict:
    """Parse surface stats and handedness splits from the jsfrags JS file."""
    soup = _extract_soup_from_jsfrags(js_text)
    if not soup:
        return {}

    surface_stats = _parse_career_splits_soup(soup)  # now includes handedness_splits key
    _enrich_bpsaved_soup(soup, surface_stats)
    rank_splits   = _parse_rank_splits_soup(soup)

    # Pull out the handedness splits extracted by the updated career_splits parser
    handedness_splits = surface_stats.pop("handedness_splits", {})

    logger.info(
        "[TA] %s jsfrags: surfaces=%s  handedness_splits=%s  rank_splits=%s",
        ta_name, list(surface_stats.keys()), list(handedness_splits.keys()), rank_splits,
    )
    return {
        "surface_stats":     surface_stats,
        "handedness_splits": handedness_splits,
        "rank_splits":       rank_splits,
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

    # Run both fetches concurrently in the thread pool
    loop = asyncio.get_event_loop()

    html, jsfrags_text = await asyncio.gather(
        loop.run_in_executor(None, _fetch_html,     ta_name),
        loop.run_in_executor(None, _fetch_jsfrags,  ta_name),
    )

    data = _parse_html(html, ta_name) if html else None

    # If main page not found, try alternative slugs
    if not html:
        for alt in _alt_name_formats(player_name):
            logger.info("[TA] trying alt slug %s", alt)
            alt_html, alt_jsfrags = await asyncio.gather(
                loop.run_in_executor(None, _fetch_html,    alt),
                loop.run_in_executor(None, _fetch_jsfrags, alt),
            )
            if alt_html:
                data        = _parse_html(alt_html, alt)
                jsfrags_text = alt_jsfrags
                break

    if data is None:
        data = {"handedness": None, "handedness_splits": {}, "ace_against_per_match": None,
                "matches": [], "surface_stats": {}, "rich_stats": {},
                "career_splits": [], "mcp_serve": [], "mcp_return": [],
                "h2h_records": [], "rank_splits": {}, "_source": "curl_cffi_html_table"}

    # Merge jsfrags surface stats into data (jsfrags career-splits table is more
    # reliable for lifetime aggregates than the raw match array averages).
    if jsfrags_text:
        jf = _parse_jsfrags(jsfrags_text, ta_name)
        if jf.get("surface_stats"):
            # Jsfrags gives career lifetime surface stats — use as primary.
            # Preserve match counts from raw-match aggregation if jsfrags lacks them.
            jf_ss = jf["surface_stats"]
            raw_ss = data.get("surface_stats", {})
            for surf, stats in jf_ss.items():
                if stats.get("matches", 0) == 0 and raw_ss.get(surf, {}).get("matches", 0) > 0:
                    stats["matches"] = raw_ss[surf]["matches"]
            data["surface_stats"] = jf_ss
        if jf.get("rank_splits"):
            data["rank_splits"] = jf["rank_splits"]
        # Merge handedness splits from jsfrags career-splits table
        if jf.get("handedness_splits"):
            data["handedness_splits"] = jf["handedness_splits"]
            logger.info("[TA] %s handedness_splits=%s", ta_name, list(jf["handedness_splits"].keys()))

    # Ensure rich_stats is present (built from raw match rows in _parse_html)
    if not data.get("rich_stats") and data.get("matches"):
        data["rich_stats"] = _build_rich_stats(data["matches"])

    # Cache result (even None — prevents hammering on persistent 404)
    _cache[ta_name] = {"ts": now, "data": data}

    if data:
        rs = data.get("rich_stats") or {}
        logger.info(
            "[TA] %s -> hand=%s  raw_matches=%d  surfaces=%s  3yr_clay=%d",
            ta_name,
            data.get("handedness"),
            len(data.get("matches", [])),
            list(data.get("surface_stats", {}).keys()),
            (rs.get("recent_3yr") or {}).get("Clay", {}).get("matches", 0),
        )
    else:
        logger.warning("[TA] no data for %s", ta_name)

    return data


def _is_useful(data: Optional[dict]) -> bool:
    """Return True if data contains at least handedness or surface stats."""
    if not data:
        return False
    return bool(data.get("handedness") or data.get("surface_stats"))


# ─────────────────────────────────────────────────────────────────────────────
# Recent-window picker — used ONLY by the Prop Projection tab.
#
# Players' levels shift over years (rising stars, declining vets); prop
# projections must reflect *current* form, not career averages. This helper
# returns the most recent TA surface stats with a controlled fallback:
#
#   1. last_52_weeks on the surface, if ≥ 5 matches
#   2. last_2yr on the surface (amber warning) if 52w sample is too thin
#   3. None — caller must skip TA enrichment and rely on Sofascore tiers
#
# Career stats are never returned by this function on purpose.
# ─────────────────────────────────────────────────────────────────────────────
def pick_ta_recent_stats(player_ta: Optional[dict], surface: str) -> dict:
    """
    Returns a dict with:
      stats          — aggregated TA stats for the chosen window (or None)
      tier           — '52w' | '2yr' | 'none' | 'ta_unavailable'
      surface_n      — matches in the chosen window on the selected surface
      all_surfaces_n — matches in the last 52 weeks across all surfaces
      warning        — None | 'limited' | 'insufficient' | 'ta_unavailable'
                       'limited'         → <5 surface matches in 52w (fallback fired)
                       'insufficient'    → <10 total matches in 52w  (red warning)
                       'ta_unavailable'  → TA match log missing — can't compute
                                           recency at all. No confidence penalty;
                                           Sofascore tiers carry the projection.
      note           — human-readable description for the UI
    """
    if not player_ta:
        return {"stats": None, "tier": "ta_unavailable", "surface_n": 0,
                "all_surfaces_n": 0, "warning": "ta_unavailable",
                "note": "Tennis Abstract data unavailable for this player"}

    rich      = player_ta.get("rich_stats") or {}
    last_52   = rich.get("last_52_weeks") or {}
    last_2yr  = rich.get("last_2yr_fallback") or {}

    # Detect "match log unavailable" up front. TA sometimes returns career
    # surface_stats from the jsfrags table but no raw match array (so the
    # 52w window can't be built at all). That's *not* the same as a player
    # being inactive — we mustn't treat 0/0 the same as 0/30. The marker is
    # whether the player_ta carries any matches at all.
    n_raw_matches = len(player_ta.get("matches") or [])
    if n_raw_matches == 0 and not last_52 and not last_2yr:
        return {
            "stats":          None,
            "tier":           "ta_unavailable",
            "surface_n":      0,
            "all_surfaces_n": 0,
            "warning":        "ta_unavailable",
            "note":           "Tennis Abstract match log not available — "
                              "projection driven by Sofascore tiers only",
        }

    surf_52   = last_52.get(surface) or {}
    n_52_surf = (surf_52.get("matches") or 0)
    n_52_all  = ((last_52.get("All") or {}).get("matches") or 0)

    # Insufficient-data condition is independent of the tier choice
    insufficient = n_52_all < 10
    warning = "insufficient" if insufficient else None

    if n_52_surf >= 5:
        return {
            "stats":          surf_52,
            "tier":           "52w",
            "surface_n":      n_52_surf,
            "all_surfaces_n": n_52_all,
            "warning":        warning,
            "note":           f"Last 52 weeks: {n_52_surf} matches on {surface}",
        }

    # Fall back to 2-year window
    surf_2yr   = last_2yr.get(surface) or {}
    n_2yr_surf = (surf_2yr.get("matches") or 0)
    limited_warning = warning or "limited"

    if n_2yr_surf > 0:
        return {
            "stats":          surf_2yr,
            "tier":           "2yr",
            "surface_n":      n_2yr_surf,
            "all_surfaces_n": n_52_all,
            "warning":        limited_warning,
            "note":           f"Last 2 years: {n_2yr_surf} matches on {surface} "
                              f"(only {n_52_surf} in last 52 weeks)",
        }

    return {
        "stats":          None,
        "tier":           "none",
        "surface_n":      n_52_surf,
        "all_surfaces_n": n_52_all,
        "warning":        limited_warning,
        "note":           "No recent TA matches on this surface within the last 2 years",
    }


def build_props_ta_view(player_ta: Optional[dict], surface: str) -> tuple:
    """
    Build a TA dict whose `surface_stats[surface]` is replaced with the recent
    window (52w or 2yr). All other fields (handedness, rank_splits, etc.) are
    preserved untouched. The full `surface_stats` for *other* surfaces is
    cleared so projection code can't accidentally use career data.

    Returns (props_ta_view, picker_result_dict).
    """
    picked = pick_ta_recent_stats(player_ta, surface)
    if not player_ta:
        return None, picked

    # Shallow copy + override surface_stats. The other keys (handedness etc.)
    # are not surface-specific so they are fine to keep.
    view = dict(player_ta)
    surface_stats = {}
    if picked["stats"]:
        surface_stats[surface] = picked["stats"]
    view["surface_stats"] = surface_stats
    view["_recent_tier"]  = picked["tier"]
    view["_recent_n"]     = picked["surface_n"]
    return view, picked
