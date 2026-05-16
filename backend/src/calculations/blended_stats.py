"""
Blended stats module — combines Tennis Abstract career data with recent
Sofascore form to produce a single unified stats dict.

Weights (spec §3):
  TA career surface stats      25 %
  TA recent 3yr surface stats  35 %
  TA last 20 on surface        25 %
  Sofascore last 5 on surface  15 %  (only when >= 3 SS matches available)

Surface-adjustment factors applied when using all-surface baseline (spec §3):
  Clay:  ace_pct × 0.75,  df_pct × 1.10,  bp_conv_pct × 1.05
  Grass: ace_pct × 1.35
  Hard:  no adjustment

Public API
----------
  get_blended_stats(player_ta, sofascore_surface_log, surface, tour) -> dict
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Average service points per match by tour — used to convert pct → count
_AVG_SERVICE_PTS = {"ATP": 80, "WTA": 70}

# Clay/Grass adjustment factors when falling back to all-surface averages
_SURFACE_ADJUST = {
    "Clay":  {"ace_pct": 0.75, "df_pct": 1.10, "bp_conv_pct": 1.05},
    "Grass": {"ace_pct": 1.35},
    "Hard":  {},
}


def _safe(val, default=0.0):
    return val if val is not None else default


def _avg_from_ss_log(log: list, key: str) -> Optional[float]:
    """Average a stat key across a list of sofascore_surface_log entries."""
    vals = [m[key] for m in log if m.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def _ta_tier_to_blended(tier_stats: dict, surface: str, tour: str,
                        use_fallback: bool = False) -> Optional[dict]:
    """
    Convert a TA surface stats tier (ace_pct, df_pct, first_won_pct, etc.)
    to the common blended format.  Returns None if tier has no meaningful data.
    """
    if not tier_stats or tier_stats.get("matches", 0) == 0:
        return None

    avg_pts = _AVG_SERVICE_PTS.get(tour, 80)
    adj = _SURFACE_ADJUST.get(surface, {}) if use_fallback else {}

    def _p(key):
        raw = tier_stats.get(key)
        if raw is None:
            return None
        factor = adj.get(key, 1.0)
        return raw * factor

    ace_pct      = _p("ace_pct")
    df_pct       = _p("df_pct")
    first_won    = _p("first_won_pct")
    second_won   = _p("second_won_pct")
    first_in     = tier_stats.get("first_in_pct")
    bp_conv      = _p("bp_conv_pct")
    bp_saved     = tier_stats.get("bp_saved_pct")

    # Convert pct → per-match count for ace and DF
    aces_pm = (ace_pct / 100) * avg_pts if ace_pct is not None else None
    df_pm   = (df_pct  / 100) * avg_pts if df_pct  is not None else None

    return {
        "aces":               aces_pm,
        "double_faults":      df_pm,
        "first_serve_pct":    first_in,
        "first_serve_pts_won": first_won,
        "second_serve_pts_won": second_won,
        "bp_converted":       bp_conv,
        "bp_saved":           bp_saved,
        "win_rate":           tier_stats.get("win_rate"),
        "matches":            tier_stats.get("matches", 0),
    }


def _ss_log_to_blended(ss_log: list) -> Optional[dict]:
    """Convert last-5 Sofascore log entries to the blended format."""
    last5 = ss_log[:5]
    if len(last5) < 3:
        return None  # not enough for a meaningful SS contribution

    def avg(key):
        vals = [m[key] for m in last5 if m.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    wins = sum(1 for m in last5 if m.get("won", False))
    return {
        "aces":               avg("aces"),
        "double_faults":      avg("double_faults"),
        "first_serve_pts_won": avg("first_serve_pts_won"),
        "second_serve_pts_won": avg("second_serve_pts_won"),
        "bp_converted":       avg("bp_converted"),
        "bp_faced_count":     avg("bp_faced_count"),
        "win_rate":           wins / len(last5) * 100 if last5 else None,
        "matches":            len(last5),
    }


def _weighted_avg(key: str, tiers: list, weights: list) -> Optional[float]:
    """
    Compute a weighted average of `key` across tiers, ignoring None values
    and renormalising remaining weights.
    """
    valid = [(w, t[key]) for w, t in zip(weights, tiers)
             if t is not None and t.get(key) is not None]
    if not valid:
        return None
    total_w = sum(w for w, _ in valid)
    if total_w == 0:
        return None
    return sum(w * v for w, v in valid) / total_w


def get_blended_stats(
    player_ta: Optional[dict],
    sofascore_surface_log: list,
    surface: str,
    tour: str = "ATP",
) -> dict:
    """
    Build a unified blended stats dict from TA career data and Sofascore recent form.

    Returns a dict with the same keys as Sofascore surface stats (aces,
    double_faults, first_serve_pts_won, second_serve_pts_won, bp_converted,
    bp_faced_count, win_rate, matches_played) plus blend metadata fields
    (_blended, _ta_career_matches, _ta_3yr_matches, _ta_last20_matches,
    _ss_recent_matches, _surface_fallback, _data_quality).
    """
    avg_pts = _AVG_SERVICE_PTS.get(tour, 80)

    # ── Extract TA tiers ──────────────────────────────────────────────────────
    career_stats = None
    recent_3yr   = None
    last20       = None
    surface_fallback = False

    if player_ta:
        ss_dict = player_ta.get("surface_stats") or {}
        rs_dict = player_ta.get("rich_stats") or {}

        # Tier 1: TA career surface stats (from jsfrags career-splits table)
        surface_tier = ss_dict.get(surface) or {}
        if surface_tier.get("matches", 0) < 5:
            # Fall back to All-surface with adjustments
            all_tier = ss_dict.get("All") or rs_dict.get("all_surfaces") or {}
            if all_tier.get("matches", 0) >= 5:
                surface_tier = all_tier
                surface_fallback = True
                logger.info("[BLEND] %s: career surface fallback to All (%d matches)",
                            surface, all_tier.get("matches", 0))

        career_stats = _ta_tier_to_blended(surface_tier, surface, tour, use_fallback=surface_fallback)

        # Tier 2: TA recent 3yr surface stats (from raw match rows)
        r3yr_dict = rs_dict.get("recent_3yr") or {}
        r3yr_surf  = r3yr_dict.get(surface) or {}
        if r3yr_surf.get("matches", 0) >= 3:
            recent_3yr = _ta_tier_to_blended(r3yr_surf, surface, tour)
        elif surface_fallback:
            r3yr_all = r3yr_dict.get("All") or {}
            if r3yr_all.get("matches", 0) >= 3:
                recent_3yr = _ta_tier_to_blended(r3yr_all, surface, tour, use_fallback=True)

        # Tier 3: TA last 20 on surface (from raw match rows)
        l20_dict = rs_dict.get("last_20_on_surface") or {}
        l20_surf  = l20_dict.get(surface) or {}
        if l20_surf.get("matches", 0) >= 3:
            last20 = _ta_tier_to_blended(l20_surf, surface, tour)

    # ── Sofascore tier (last 5 on surface) ───────────────────────────────────
    ss_tier = _ss_log_to_blended(sofascore_surface_log)

    # ── Determine weights ─────────────────────────────────────────────────────
    ss_matches   = len([m for m in sofascore_surface_log[:5] if m.get("aces") is not None])
    use_ss       = ss_tier is not None and ss_matches >= 3

    # Base weights (from spec); SS only if sufficient
    w_career = 0.25
    w_3yr    = 0.35
    w_last20 = 0.25
    w_ss     = 0.15 if use_ss else 0.0

    # Redistribute missing tier weight proportionally
    tiers   = [career_stats, recent_3yr, last20, ss_tier if use_ss else None]
    weights = [w_career, w_3yr, w_last20, w_ss]

    # Build metadata
    ta_career_matches = (career_stats.get("matches", 0) if career_stats else 0)
    ta_3yr_matches    = (recent_3yr.get("matches", 0)   if recent_3yr else 0)
    ta_last20_matches = (last20.get("matches", 0)       if last20 else 0)

    # ── Blend each stat ───────────────────────────────────────────────────────
    STAT_KEYS = [
        "aces", "double_faults", "first_serve_pts_won", "second_serve_pts_won",
        "bp_converted", "win_rate",
    ]

    blended: dict = {}
    for key in STAT_KEYS:
        blended[key] = _weighted_avg(key, tiers, weights)

    # Return stats (from Sofascore only — TA doesn't track this directly)
    ss_all  = sofascore_surface_log[:10]
    blended["return_first_serve_pts_won"]  = _avg_from_ss_log(ss_all, "first_serve_pts_won")
    blended["return_second_serve_pts_won"] = None  # not in SS log

    # BP faced comes from Sofascore surface log (opponent serving stats)
    blended["bp_faced_count"] = _avg_from_ss_log(ss_all, "bp_faced_count")

    # matches_played: use the best available career count for confidence scoring
    all_surface_matches = max(ta_career_matches, ta_3yr_matches, ta_last20_matches)
    blended["matches_played"] = all_surface_matches or ss_matches

    # ── Data quality flag ─────────────────────────────────────────────────────
    if ta_career_matches >= 20 and not surface_fallback:
        data_quality = "rich"
    elif ta_career_matches >= 5 or (ta_3yr_matches >= 5 and not surface_fallback):
        data_quality = "moderate"
    else:
        data_quality = "thin"

    # ── Metadata fields ───────────────────────────────────────────────────────
    blended.update({
        "_blended":            True,
        "_ta_career_matches":  ta_career_matches,
        "_ta_3yr_matches":     ta_3yr_matches,
        "_ta_last20_matches":  ta_last20_matches,
        "_ss_recent_matches":  ss_matches,
        "_surface_fallback":   surface_fallback,
        "_data_quality":       data_quality,
    })

    logger.info(
        "[BLEND] surface=%s fallback=%s career=%d 3yr=%d last20=%d ss=%d quality=%s",
        surface, surface_fallback,
        ta_career_matches, ta_3yr_matches, ta_last20_matches, ss_matches,
        data_quality,
    )

    return blended
