"""
Unified match pool — merges Sofascore + Sackmann match dicts into a single
deduplicated dataset per player.  Tennis Abstract contributes aggregate career
splits handled separately in blended_stats.py (it does not expose individual
match rows).

All matches are normalised into a common schema before merging so downstream
code can filter, aggregate, and chart without caring which source supplied them.

Field-mapping reference
-----------------------
Universal ← Sofascore (all_match_stats)       ← Sackmann (_parse_sackmann_row)
aces          aces                               aces
dfs           double_faults                      double_faults
first_serve_pct  first_serve_pct                first_serve_pct
first_serve_won_pct  first_serve_pts_won        first_serve_won_pct
second_serve_won_pct second_serve_pts_won        second_serve_won_pct
bp_faced      bp_faced_count                     bp_faced
bp_saved_pct  bp_saved  (already %)              bp_saved_pct
bp_won        bp_converted_count  (integer)      bp_won  (integer)
bp_won_pct    bp_converted  (%)                  bp_conv_pct  (%)
opp_bp_faced  — (not available in SS per-match)  opp_bp_faced
ret_pts_won_1st  return_first_serve_pts_won      ret_pts_won_1st
ret_pts_won_2nd  return_second_serve_pts_won     ret_pts_won_2nd
total_games   total_match_games                  total_games

Public API
----------
  normalize_sofascore_match(m)        -> dict
  normalize_sackmann_match(m)         -> dict
  merge_and_deduplicate(ss, sack)     -> list[dict]
  aggregate_unified_stats(pool, ...)  -> dict | None
  build_unified_chart_log(pool, ...)  -> list[dict]
"""

import re
import logging
from datetime import datetime
from typing import Optional, List

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm_opp(name: str) -> str:
    """
    Reduce an opponent name to its surname for dedup key generation.
    Handles "Raphael Collignon", "Collignon R.", "R. Collignon" → "collignon".
    """
    if not name:
        return "unknown"
    clean = re.sub(r"[^a-zA-Z\s]", "", str(name)).strip().lower()
    parts = clean.split()
    return parts[-1] if parts else clean or "unknown"


def _fmt_date_display(date_str: str, ts: int = 0) -> str:
    """
    Format 'YYYY-MM-DD' (or Unix timestamp) into chart display string 'Apr 13'.
    Appends a 2-digit year suffix when the match is not from the current year,
    e.g. 'May 26 '25' — prevents confusion when the month/day alone looks like
    a future date (e.g. Roland Garros 2025 matches appearing in a 2026 session).
    """
    try:
        if date_str and len(date_str) >= 10:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        elif ts:
            dt = datetime.utcfromtimestamp(ts)
        else:
            return date_str or ""
        current_year = datetime.utcnow().year
        # Cross-platform day-without-zero (%-d Linux, %#d Windows — use str(day) instead)
        label = dt.strftime("%b") + " " + str(dt.day)
        if dt.year != current_year:
            label += f" '{str(dt.year)[2:]}"   # e.g. "May 26 '25"
        return label
    except Exception:
        return date_str or ""


# ── Normalizers ───────────────────────────────────────────────────────────────

def normalize_sofascore_match(m: dict) -> dict:
    """
    Convert one sofascore all_match_stats dict to the unified schema.

    `has_stats` is True when at least one of the key serve stat columns was
    successfully parsed from the Sofascore statistics API.  Challenger events
    often return empty statistics, so those matches have has_stats=False but
    still carry result / surface / date information which is valuable for
    win-rate and match-count display.
    """
    date     = m.get("date", "")
    opponent = m.get("opponent_name", "")
    won      = m.get("won", False)

    has_stats = any(
        m.get(k) is not None
        for k in ("aces", "double_faults", "bp_converted_count", "first_serve_pts_won")
    )

    return {
        # Dedup + identity
        "match_id":           f"{date}_{_norm_opp(opponent)}",
        "date":               date,
        "tournament":         m.get("tournament", ""),
        "surface":            m.get("surface", ""),
        "result":             "W" if won else "L",
        "opponent":           opponent,
        "score":              m.get("score", ""),
        "source":             "sofascore",
        "has_stats":          has_stats,
        # Serve stats
        "aces":               m.get("aces"),
        "dfs":                m.get("double_faults"),
        "first_serve_pct":    m.get("first_serve_pct"),
        "first_serve_won_pct":  m.get("first_serve_pts_won"),
        "second_serve_won_pct": m.get("second_serve_pts_won"),
        "bp_faced":           m.get("bp_faced_count"),
        "bp_saved_pct":       m.get("bp_saved"),
        # Return / break-point stats
        "bp_won":             m.get("bp_converted_count"),
        "bp_won_pct":         m.get("bp_converted"),
        "opp_bp_faced":       None,   # not available per-match in SS
        "ret_pts_won_1st":    m.get("return_first_serve_pts_won"),
        "ret_pts_won_2nd":    m.get("return_second_serve_pts_won"),
        # Match totals
        "total_games":        m.get("total_match_games"),
        "sv_gms":             None,
        # Metadata for sorting
        "timestamp":          m.get("timestamp", 0),
        "event_id":           m.get("event_id"),
    }


def normalize_sackmann_match(m: dict) -> dict:
    """
    Convert one Sackmann match dict (from _parse_sackmann_row) to the unified
    schema.  All percentage fields are already on the 0-100 scale.
    """
    date     = str(m.get("date", ""))[:10]
    opponent = m.get("opponent", "")

    has_stats = any(
        m.get(k) is not None
        for k in ("aces", "bp_won", "first_serve_pct")
    )

    return {
        "match_id":           f"{date}_{_norm_opp(opponent)}",
        "date":               date,
        "tournament":         m.get("tournament", ""),
        "surface":            m.get("surface", ""),
        "result":             m.get("result", ""),
        "opponent":           opponent,
        "score":              m.get("score", ""),
        "source":             "sackmann",
        "has_stats":          has_stats,
        "aces":               m.get("aces"),
        "dfs":                m.get("double_faults"),
        "first_serve_pct":    m.get("first_serve_pct"),
        "first_serve_won_pct":  m.get("first_serve_won_pct"),
        "second_serve_won_pct": m.get("second_serve_won_pct"),
        "bp_faced":           m.get("bp_faced"),
        "bp_saved_pct":       m.get("bp_saved_pct"),
        "bp_won":             m.get("bp_won"),
        "bp_won_pct":         m.get("bp_conv_pct"),
        "opp_bp_faced":       m.get("opp_bp_faced"),
        "ret_pts_won_1st":    m.get("ret_pts_won_1st"),
        "ret_pts_won_2nd":    m.get("ret_pts_won_2nd"),
        "total_games":        m.get("total_games"),
        "sv_gms":             m.get("sv_gms"),
        # Sackmann has date only — no sub-day precision
        "timestamp":          0,
        "event_id":           None,
    }


# ── Merge + deduplication ─────────────────────────────────────────────────────

def merge_and_deduplicate(
    ss_matches: List[dict],
    sack_matches: List[dict],
) -> List[dict]:
    """
    Merge Sofascore and Sackmann into one deduplicated pool.

    Priority: Sofascore > Sackmann.
    Dedup key: "{YYYY-MM-DD}_{opponent_surname}" — intentionally coarse to
    handle minor name format differences between sources.

    When Sofascore and Sackmann both have the same match:
      - Sofascore with stats always wins.
      - Sofascore without stats wins for recency (keeps most recent record).
      - Sackmann is kept only when there is no Sofascore record at all.
    """
    pool: dict = {}

    # Lowest priority: Sackmann (2015-2020 historical)
    for m in sack_matches:
        mid = m.get("match_id")
        if mid:
            pool[mid] = m

    # Highest priority: Sofascore (2021-present)
    for m in ss_matches:
        mid = m.get("match_id")
        if not mid:
            continue
        existing = pool.get(mid)
        if not existing:
            pool[mid] = m
        elif m.get("has_stats"):
            pool[mid] = m          # SS with stats beats Sackmann always
        elif not existing.get("has_stats"):
            pool[mid] = m          # SS without stats still preferred for recency

    # Sort: primary = date string (ISO sorts correctly), secondary = timestamp
    unified = sorted(
        pool.values(),
        key=lambda x: (x.get("date", ""), x.get("timestamp", 0)),
        reverse=True,
    )

    logger.info(
        "MERGE_POOL | sofascore=%d | sackmann=%d | unified=%d | dupes_removed=%d",
        len(ss_matches), len(sack_matches), len(unified),
        max(0, len(ss_matches) + len(sack_matches) - len(unified)),
    )

    return unified


# ── Aggregation ───────────────────────────────────────────────────────────────

def aggregate_unified_stats(
    pool: List[dict],
    surface_filter: Optional[str] = None,
    max_matches: Optional[int] = None,
) -> Optional[dict]:
    """
    Aggregate stats from the unified match pool.

    match_count  = ALL matches found (with or without stats) — for win-rate
                   display and match count in the UI.
    stat averages = only matches with has_stats=True — for projection.

    Returns None when no matches pass the filter.
    """
    matches = pool
    if surface_filter:
        sl = surface_filter.lower()
        matches = [m for m in matches if (m.get("surface") or "").lower() == sl]
    if max_matches:
        matches = matches[:max_matches]

    if not matches:
        return None

    stat_m = [m for m in matches if m.get("has_stats")]
    wins   = sum(1 for m in matches if m.get("result") == "W")

    def _avg(field: str) -> Optional[float]:
        vals = [m[field] for m in stat_m if m.get(field) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    return {
        "matches":               len(matches),
        "stat_matches":          len(stat_m),
        "win_rate":              round(wins / len(matches) * 100, 2) if matches else 0,
        "aces_per_match":        _avg("aces"),
        "df_per_match":          _avg("dfs"),
        "first_serve_pct":       _avg("first_serve_pct"),
        "first_serve_won_pct":   _avg("first_serve_won_pct"),
        "second_serve_won_pct":  _avg("second_serve_won_pct"),
        "bp_converted":          _avg("bp_won_pct"),
        "bp_saved":              _avg("bp_saved_pct"),
        "bp_faced_per_match":    _avg("bp_faced"),
        "bp_won_per_match":      _avg("bp_won"),
        "ret_pts_won_1st":       _avg("ret_pts_won_1st"),
        "ret_pts_won_2nd":       _avg("ret_pts_won_2nd"),
        "total_games_per_match": _avg("total_games"),
        "sources":               sorted({m["source"] for m in matches}),
    }


# ── Chart log ─────────────────────────────────────────────────────────────────

def build_unified_chart_log(
    pool: List[dict],
    surface: Optional[str] = None,
    limit: int = 10,
) -> List[dict]:
    """
    Build a chart-log-compatible list from the unified pool.

    When surface is None (default), returns the last `limit` matches across
    ALL surfaces — used for the "Last 5 Matches" bar chart which shows whether
    the prop line was met regardless of surface.

    When surface is provided, filters to that surface only (legacy behaviour).

    Stat fields are None for matches where has_stats=False — the frontend
    renders gray N/A stubs for those bars, preserving win/loss information.
    """
    if surface:
        sl = surface.lower()
        surf_matches = [
            m for m in pool
            if (m.get("surface") or "").lower() == sl
        ][:limit]
    else:
        surf_matches = pool[:limit]

    out: List[dict] = []
    for m in surf_matches:
        date_str = _fmt_date_display(m.get("date", ""), m.get("timestamp", 0))
        opp      = m.get("opponent", "Unknown")
        opp_abbr = opp.split()[-1] if opp.split() else opp

        out.append({
            "date":                date_str,
            "date_ts":             m.get("timestamp", 0),
            "tournament":          m.get("tournament", ""),
            "surface":             m.get("surface", ""),
            "opponent":            opp,
            "opponent_abbr":       opp_abbr,
            "won":                 m.get("result") == "W",
            "score":               m.get("score", ""),
            # These may be None for challenger/stat-poor matches → N/A bars in chart
            "total_match_games":   m.get("total_games"),
            "aces":                m.get("aces"),
            "double_faults":       m.get("dfs"),
            "bp_converted_count":  m.get("bp_won"),
            "bp_converted":        m.get("bp_won_pct"),
            "bp_faced_count":      m.get("bp_faced"),
            "first_serve_pts_won": m.get("first_serve_won_pct"),
            "second_serve_pts_won": m.get("second_serve_won_pct"),
            "source":              m.get("source", ""),
        })

    return out
