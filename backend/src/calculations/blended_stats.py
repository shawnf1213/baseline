"""
Blended stats module — builds a single unified stats dict from Sofascore
data tiers.  Tennis Abstract is the *enrichment* layer (handedness,
ace-against, splits) and is not part of the blending math here.

Weights (spec Part 2):
  SS all-time surface stats      25 %
  SS recent 3-year surface stats 35 %
  SS last 20 on surface          25 %
  SS last 5 on surface           15 %  (only when >= 3 stat matches available)

Surface-adjustment factors — applied when the player has fewer than 10
career matches on the target surface (falls back to all-surface stats):
  Clay:  aces × 0.75,  double_faults × 1.10,  bp_converted × 1.05
  Grass: aces × 1.35
  Hard:  no adjustment

Public API
----------
  get_blended_stats(player_ss_data, sofascore_surface_log, surface,
                    tour, player_ta, sackmann_stats, sackmann_all_stats)
  -> dict
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Clay/Grass adjustment factors when falling back to all-surface averages.
# Keys match the field names in the Sofascore _agg_split output.
_SURFACE_ADJUST_SS = {
    "Clay":  {"aces": 0.75, "double_faults": 1.10, "bp_converted": 1.05},
    "Grass": {"aces": 1.35},
    "Hard":  {},
}


def _safe(val, default=0.0):
    return val if val is not None else default


def _avg_from_ss_log(log: list, key: str) -> Optional[float]:
    """Average a stat key across a list of sofascore_surface_log entries."""
    vals = [m[key] for m in log if m.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def _ss_tier_to_blended(
    ss_stats: Optional[dict],
    surface: str,
    use_fallback: bool = False,
) -> Optional[dict]:
    """
    Convert a Sofascore _agg_split dict to the blended format.

    ss_stats keys:  matches_played, stat_matches, win_rate, aces,
                    double_faults, first_serve_pct, first_serve_pts_won,
                    second_serve_pts_won, bp_converted, bp_saved,
                    return_first_serve_pts_won, return_second_serve_pts_won,
                    bp_converted_count, bp_faced_count, total_match_games

    Returns None when ss_stats has no data or zero matches.
    """
    if not ss_stats or ss_stats.get("matches_played", 0) == 0:
        return None

    adj = _SURFACE_ADJUST_SS.get(surface, {}) if use_fallback else {}

    def _p(key: str):
        raw = ss_stats.get(key)
        if raw is None:
            return None
        return raw * adj.get(key, 1.0)

    return {
        "aces":                        _p("aces"),
        "double_faults":               _p("double_faults"),
        "first_serve_pct":             ss_stats.get("first_serve_pct"),
        "first_serve_pts_won":         ss_stats.get("first_serve_pts_won"),
        "second_serve_pts_won":        ss_stats.get("second_serve_pts_won"),
        "bp_converted":                _p("bp_converted"),
        "bp_saved":                    ss_stats.get("bp_saved"),
        "return_first_serve_pts_won":  ss_stats.get("return_first_serve_pts_won"),
        "return_second_serve_pts_won": ss_stats.get("return_second_serve_pts_won"),
        "win_rate":                    ss_stats.get("win_rate"),
        "matches":                     ss_stats.get("matches_played", 0),
        "bp_faced_count":              ss_stats.get("bp_faced_count"),
    }


def _ss_log5_to_blended(ss_log: list) -> Optional[dict]:
    """Convert last-5 Sofascore surface log entries to the blended format."""
    last5 = ss_log[:5]
    if len(last5) < 3:
        return None  # not enough for a meaningful contribution

    def avg(key):
        vals = [m[key] for m in last5 if m.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    wins = sum(1 for m in last5 if m.get("won", False))
    return {
        "aces":                   avg("aces"),
        "double_faults":          avg("double_faults"),
        "first_serve_pts_won":    avg("first_serve_pts_won"),
        "second_serve_pts_won":   avg("second_serve_pts_won"),
        "bp_converted":           avg("bp_converted"),
        "bp_faced_count":         avg("bp_faced_count"),
        "win_rate":               wins / len(last5) * 100 if last5 else None,
        "matches":                len(last5),
    }


def _weighted_avg(key: str, tiers: list, weights: list) -> Optional[float]:
    """
    Weighted average of `key` across tiers, renormalising when some are None.
    """
    valid = [
        (w, t[key])
        for w, t in zip(weights, tiers)
        if t is not None and t.get(key) is not None
    ]
    if not valid:
        return None
    total_w = sum(w for w, _ in valid)
    if total_w == 0:
        return None
    return sum(w * v for w, v in valid) / total_w


# ── Sackmann compatibility helpers (kept for backward compat) ─────────────────

def _sackmann_to_blended_tier(sack: Optional[dict]) -> Optional[dict]:
    if not sack or sack.get("matches", 0) == 0:
        return None
    return {
        "aces":                sack.get("aces"),
        "double_faults":       sack.get("double_faults"),
        "first_serve_pct":     sack.get("first_serve_pct"),
        "first_serve_pts_won": sack.get("first_serve_pts_won"),
        "second_serve_pts_won":sack.get("second_serve_pts_won"),
        "bp_converted":        sack.get("bp_converted"),
        "bp_saved":            sack.get("bp_saved"),
        "return_first_serve_pts_won":  sack.get("return_first_serve_pts_won"),
        "return_second_serve_pts_won": sack.get("return_second_serve_pts_won"),
        "win_rate":            sack.get("win_rate"),
        "matches":             sack.get("matches", 0),
    }


# ── Main public function ──────────────────────────────────────────────────────

def get_blended_stats(
    player_ss_data: Optional[dict],
    sofascore_surface_log: list,
    surface: str,
    tour: str = "ATP",
    player_ta: Optional[dict] = None,       # enrichment only — not blended
    sackmann_stats: Optional[dict] = None,
    sackmann_all_stats: Optional[dict] = None,
) -> dict:
    """
    Build a unified blended stats dict from Sofascore data tiers.

    Blending layers (Sofascore-only per Part 2 of spec):
      Tier 1: all-time surface stats        25 %
      Tier 2: recent 3-year surface stats   35 %
      Tier 3: last-20 on surface            25 %
      Tier 4: last-5 on surface             15 % (only when >= 3 matches)

    When the target surface has < 10 career matches, tiers fall back to
    all-surface stats with surface-adjustment factors applied.

    player_ta   — accepted for API compat but used only for enrichment
                  metadata (_ta_available flag). Not blended into stats.
    sackmann_*  — accepted for API compat; used as final low-priority supplement
                  when SS career data is very thin (< 5 matches).
    """
    # ── Extract SS tiers for target surface ───────────────────────────────────
    surf_all_time  = (player_ss_data or {}).get(f"{surface}_all_time_stats")
    surf_3yr       = (player_ss_data or {}).get(f"{surface}_recent_3yr_stats")
    surf_last20    = (player_ss_data or {}).get(f"{surface}_last_20")

    all_time_matches = (surf_all_time or {}).get("matches_played", 0)
    surface_fallback = all_time_matches < 10

    if surface_fallback:
        # Fall back to all-surface tiers with surface adjustment
        surf_all_time = (player_ss_data or {}).get("All_all_time_stats")
        surf_3yr      = (player_ss_data or {}).get("All_recent_3yr_stats")
        surf_last20   = (player_ss_data or {}).get("All_last_20")
        logger.info(
            "[BLEND] surface=%s fallback to All (only %d career matches on surface)",
            surface, all_time_matches,
        )

    tier1 = _ss_tier_to_blended(surf_all_time, surface, use_fallback=surface_fallback)
    tier2 = _ss_tier_to_blended(surf_3yr,      surface, use_fallback=surface_fallback)
    tier3 = _ss_tier_to_blended(surf_last20,   surface, use_fallback=surface_fallback)

    # Tier 4: last-5 from the surface log (stat-rich only)
    ss_matches_with_stats = len([
        m for m in sofascore_surface_log[:5]
        if m.get("aces") is not None
    ])
    use_last5 = ss_matches_with_stats >= 3
    tier4 = _ss_log5_to_blended(sofascore_surface_log) if use_last5 else None

    # Weights: renormalise automatically in _weighted_avg if any tier is None
    w1, w2, w3, w4 = 0.25, 0.35, 0.25, (0.15 if use_last5 else 0.0)

    tiers   = [tier1, tier2, tier3, tier4]
    weights = [w1, w2, w3, w4]

    # Career match counts for metadata / confidence scoring
    ss_career_matches  = (tier1["matches"] if tier1 else 0)
    ss_3yr_matches     = (tier2["matches"] if tier2 else 0)
    ss_last20_matches  = (tier3["matches"] if tier3 else 0)

    # ── Blend each stat ───────────────────────────────────────────────────────
    STAT_KEYS = [
        "aces", "double_faults", "first_serve_pts_won", "second_serve_pts_won",
        "bp_converted", "win_rate",
    ]

    blended: dict = {}
    for key in STAT_KEYS:
        val = _weighted_avg(key, tiers, weights)

        # WIN_RATE guard — exclude 0% win rate when we have meaningful data
        if key == "win_rate" and val is not None and val == 0.0 and ss_career_matches > 5:
            logger.warning(
                "WIN_RATE_GUARD | surface=%s | blended_win_rate=0.0 with "
                "career_matches=%d — excluding (likely parse error)",
                surface, ss_career_matches,
            )
            val = None

        blended[key] = round(val, 4) if val is not None else None

    # ── Sackmann supplement (very thin SS data only) ───────────────────────────
    sack_tier    = None
    sack_matches = 0
    sack_w       = 0.0
    primary_w    = 1.0

    if sackmann_stats or sackmann_all_stats:
        try:
            from src.api.sackmann import apply_surface_adjustments as _sack_adj
            sack_source  = sackmann_stats or _sack_adj(sackmann_all_stats, surface)
            sack_tier    = _sackmann_to_blended_tier(sack_source)
            sack_matches = (sack_source or {}).get("matches", 0)
        except Exception as exc:
            logger.debug("Sackmann supplement error: %s", exc)
            sack_tier = None

        # Only blend Sackmann when SS career data is very thin
        if ss_career_matches >= 10:
            primary_w, sack_w = 0.95, 0.05
        elif ss_career_matches >= 5:
            primary_w, sack_w = 0.85, 0.15
        elif ss_career_matches >= 2:
            primary_w, sack_w = 0.70, 0.30
        else:
            primary_w, sack_w = 0.0,  1.0

        if sack_tier and sack_w > 0:
            for key in STAT_KEYS:
                pval = blended.get(key)
                sval = sack_tier.get(key)
                if pval is not None and sval is not None:
                    blended[key] = round(pval * primary_w + sval * sack_w, 4)
                elif sval is not None and pval is None:
                    blended[key] = sval

            logger.info(
                "[BLEND] Sackmann supplement | surface=%s | ss_career=%d | "
                "sack=%d | weights=%.0f%%/%.0f%%",
                surface, ss_career_matches, sack_matches,
                primary_w * 100, sack_w * 100,
            )

    # ── Return / BP fields (from SS log directly) ─────────────────────────────
    ss_all = sofascore_surface_log[:10]
    ret_1st = _avg_from_ss_log(ss_all, "first_serve_pts_won")
    ret_2nd = None
    if ret_1st is None and tier1:
        ret_1st = tier1.get("return_first_serve_pts_won")
        ret_2nd = tier1.get("return_second_serve_pts_won")

    blended["return_first_serve_pts_won"]  = ret_1st
    blended["return_second_serve_pts_won"] = ret_2nd

    bp_faced = _avg_from_ss_log(ss_all, "bp_faced_count")
    if bp_faced is None and tier1:
        bp_faced = tier1.get("bp_faced_count")
    if bp_faced is None and sack_tier:
        try:
            from src.api.sackmann import apply_surface_adjustments as _sack_adj
            sack_source = sackmann_stats or _sack_adj(sackmann_all_stats, surface)
            bp_faced = (sack_source or {}).get("bp_faced_per_match")
        except Exception:
            pass
    blended["bp_faced_count"] = bp_faced

    # matches_played: career match count for confidence scoring
    blended["matches_played"] = ss_career_matches or ss_3yr_matches or ss_last20_matches

    # ── Data quality flag ──────────────────────────────────────────────────────
    if ss_career_matches >= 20 and not surface_fallback:
        data_quality = "rich"
    elif ss_career_matches >= 5 or (ss_3yr_matches >= 5 and not surface_fallback):
        data_quality = "moderate"
    else:
        data_quality = "thin"

    # Confidence penalty when SS data is very thin
    conf_penalty = -10 if ss_career_matches < 2 and sack_matches > 0 else 0
    data_warning = (
        f"Limited surface data — historical baseline (2015-2020) supplemented"
        if ss_career_matches < 2 and sack_matches > 0
        else None
    )

    # ── Metadata ──────────────────────────────────────────────────────────────
    blended.update({
        "_blended":               True,
        "_ss_career_matches":     ss_career_matches,
        "_ss_3yr_matches":        ss_3yr_matches,
        "_ss_last20_matches":     ss_last20_matches,
        "_ss_recent_matches":     ss_matches_with_stats,
        # Legacy TA metadata keys — kept for compatibility with confidence.py
        "_ta_career_matches":     ss_career_matches,
        "_ta_3yr_matches":        ss_3yr_matches,
        "_ta_last20_matches":     ss_last20_matches,
        "_surface_fallback":      surface_fallback,
        "_data_quality":          data_quality,
        "_sackmann_matches":      sack_matches,
        "_sackmann_weight":       sack_w,
        "_primary_weight":        primary_w,
        "_confidence_penalty":    conf_penalty,
        "_data_warning":          data_warning,
        "_ta_available":          player_ta is not None,
    })

    logger.info(
        "[BLEND] surface=%s fallback=%s career=%d 3yr=%d last20=%d ss5=%d "
        "sack=%d quality=%s",
        surface, surface_fallback,
        ss_career_matches, ss_3yr_matches, ss_last20_matches, ss_matches_with_stats,
        sack_matches, data_quality,
    )

    return blended
