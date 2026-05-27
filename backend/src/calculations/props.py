import logging

from src.constants import COURT_CPR, CPR_NEUTRAL, ATP_TOUR_AVERAGES

logger = logging.getLogger(__name__)

# Tour-average aces faced per match — used to normalise opponent ace-against rate
_TOUR_AVG_ACE_AGAINST = {"ATP": 5.5, "WTA": 3.0}

# ── Tour-average stats by surface ─────────────────────────────────────────────
# Used as fallback when opponent has < 3 SS matches on this surface.
TOUR_AVG_BY_SURFACE = {
    "ATP": {
        "Clay": {
            "bp_faced_per_match": 9.8,
            "bp_saved_pct":       62.0,
            "first_serve_pct":    62.0,
            "first_serve_won":    69.0,
            "second_serve_won":   50.0,
            "aces_per_match":     3.2,
            "df_per_match":       2.1,
        },
        "Hard": {
            "bp_faced_per_match": 8.2,
            "bp_saved_pct":       64.0,
            "first_serve_pct":    63.0,
            "first_serve_won":    72.0,
            "second_serve_won":   52.0,
            "aces_per_match":     4.8,
            "df_per_match":       1.8,
        },
        "Grass": {
            "bp_faced_per_match": 7.1,
            "bp_saved_pct":       68.0,
            "first_serve_pct":    65.0,
            "first_serve_won":    76.0,
            "second_serve_won":   54.0,
            "aces_per_match":     6.2,
            "df_per_match":       1.6,
        },
    },
    "WTA": {
        "Clay": {
            "bp_faced_per_match": 10.4,
            "bp_saved_pct":       58.0,
            "first_serve_pct":    60.0,
            "first_serve_won":    65.0,
            "second_serve_won":   47.0,
            "aces_per_match":     1.2,
            "df_per_match":       2.8,
        },
        "Hard": {
            "bp_faced_per_match": 9.1,
            "bp_saved_pct":       60.0,
            "first_serve_pct":    61.0,
            "first_serve_won":    67.0,
            "second_serve_won":   48.0,
            "aces_per_match":     1.8,
            "df_per_match":       2.5,
        },
        "Grass": {
            "bp_faced_per_match": 7.8,
            "bp_saved_pct":       63.0,
            "first_serve_pct":    63.0,
            "first_serve_won":    70.0,
            "second_serve_won":   50.0,
            "aces_per_match":     2.1,
            "df_per_match":       2.2,
        },
    },
}

def _tour_avg(tour: str, surface: str) -> dict:
    """Return tour-average stat dict for a given tour and surface."""
    t = TOUR_AVG_BY_SURFACE.get(tour, TOUR_AVG_BY_SURFACE["ATP"])
    return t.get(surface, t["Hard"])

# ── Sanity bounds ─────────────────────────────────────────────────────────────
# "ATP_GS" key is used when match_format == "best_of_5" (ATP Grand Slams).
# BO5 max is higher because men can win more BPs across 5 sets.
PROJECTION_SANITY_BOUNDS = {
    "Break Points Won": {
        "ATP":    {"min": 1.5, "max": 12.0},
        "ATP_GS": {"min": 2.0, "max": 18.0},  # best-of-5 Grand Slam
        "WTA":    {"min": 1.5, "max": 14.0},
    },
    "Aces": {
        "ATP":    {"min": 0.5, "max": 18.0},
        "ATP_GS": {"min": 0.5, "max": 26.0},  # BO5 allows higher ace totals
        "WTA":    {"min": 0.2, "max": 8.0},
    },
    "Double Faults": {
        "ATP":    {"min": 0.3, "max": 8.0},
        "ATP_GS": {"min": 0.3, "max": 12.0},
        "WTA":    {"min": 0.3, "max": 10.0},
    },
    "Total Games": {
        "ATP":    {"min": 14.0, "max": 39.0},
        "ATP_GS": {"min": 20.0, "max": 55.0},  # BO5 range
        "WTA":    {"min": 12.0, "max": 39.0},
    },
}


def sanity_check_projection(prop_type: str, projection: float,
                             tour: str, player_name: str,
                             surface: str,
                             match_format: str = "best_of_3") -> bool:
    """
    Return True if projection is within realistic bounds, False if it fails.
    Logs a warning on failure. Caller should apply a tour-average fallback
    or flag the result when this returns False.

    Uses ATP_GS bounds when match_format == "best_of_5" (Grand Slam men's).
    """
    prop_bounds = PROJECTION_SANITY_BOUNDS.get(prop_type, {})
    # Select the right key: Grand Slam ATP → ATP_GS, otherwise tour as-is
    bound_key = "ATP_GS" if (tour == "ATP" and match_format == "best_of_5") else tour
    bounds = prop_bounds.get(bound_key) or prop_bounds.get(tour, {})
    if not bounds:
        return True
    if projection < bounds["min"] or projection > bounds["max"]:
        logger.warning(
            "SANITY_FAIL | player=%s | prop=%s | surface=%s | tour=%s | "
            "format=%s | projection=%.2f outside [%.1f, %.1f]",
            player_name, prop_type, surface, tour, match_format,
            projection, bounds["min"], bounds["max"],
        )
        return False
    return True

# Tour-average first-serve points won % — used for TA opponent suppression
_TOUR_AVG_FIRST_WON = {"ATP": 72.0, "WTA": 65.0}

# Average service points per match by tour and format.
# ATP best-of-3: ~80 sp/player; best-of-5 (Grand Slam): ~115 sp/player.
# WTA is always best-of-3.
_AVG_SERVICE_PTS = {
    "ATP": {"best_of_3": 80, "best_of_5": 115},
    "WTA": {"best_of_3": 70, "best_of_5": 70},
}

# Handedness matchup ace factors (server_hand, returner_hand) -> factor
#
# R server vs L returner:
#   The classic "wide" deuce-court serve becomes a body serve for a lefty;
#   the "body" serve goes wide (readable for lefty). Ace angles are disrupted.
#   Reduce by ~8% on clay/hard (midpoint of spec's 6-10%), ~4% on grass (3-5%).
#
# L server vs R returner:
#   The left-hander's natural wide ad-court serve is harder for righties to read.
#   Increase by ~6.5% (midpoint of spec's 5-8%).
#
# Same handedness: no adjustment.
_HAND_ACE_FACTORS_CLAY_HARD = {
    ("R", "L"): 0.92,   # -8%
    ("L", "R"): 1.065,  # +6.5%
    ("R", "R"): 1.00,
    ("L", "L"): 1.00,
}
_HAND_ACE_FACTORS_GRASS = {
    ("R", "L"): 0.96,   # -4% (less angle-dependent on fast grass)
    ("L", "R"): 1.065,  # same boost
    ("R", "R"): 1.00,
    ("L", "L"): 1.00,
}


def _safe(val, default=0.0):
    return val if val is not None else default


def _confidence(p_matches: int, o_matches: int, has_h2h: bool) -> int:
    score = 35
    if p_matches >= 8:
        score += 25
    elif p_matches >= 4:
        score += 15
    elif p_matches >= 1:
        score += 5
    if o_matches >= 8:
        score += 20
    elif o_matches >= 4:
        score += 12
    elif o_matches >= 1:
        score += 5
    if has_h2h:
        score += 15
    return min(93, score)


def project_aces(
    player_stats: dict,
    opponent_stats: dict,
    court: str,
    h2h_ace_avg: float = None,
    cpr_override: int = None,
    player_ta: dict = None,
    opponent_ta: dict = None,
    tour: str = "ATP",
    surface: str = "Hard",
    match_format: str = "best_of_3",
) -> dict:
    """
    5-layer ace projection model:
      L1 — base ace rate: TA surface stats (primary) or Sofascore (fallback)
      L2 — opponent suppression: TA first_won_pct (primary) blended with Sofascore
      L3 — handedness matchup adjustment (Tennis Abstract)
      L4 — opponent ace-against (TA ace_pct blended with Sofascore ace-against)
      L5 — surface/court CPR (court pace rating)
    """
    # BO5 Grand Slams have ~115 service points vs ~80 for BO3 tours.
    # Use the correct denominator when converting ace% → aces/match.
    _sp_map = _AVG_SERVICE_PTS.get(tour, {"best_of_3": 80, "best_of_5": 80})
    avg_service_pts = _sp_map.get(match_format, _sp_map["best_of_3"])
    ta_used = False
    ta_surface_matches = 0

    # ── L1: Base ace rate — TA surface stats preferred ────────────────────────
    sofascore_base_raw = _safe(player_stats.get("aces"))
    # Sofascore blended data is a per-match average over mixed BO3 + BO5 matches.
    # Scale up modestly for Grand Slam BO5 projection (partial correction).
    _bo5_ace_ss_scale = BO5_SS_SCALE.get(surface, 1.30)
    sofascore_base = (
        sofascore_base_raw * _bo5_ace_ss_scale
        if match_format == "best_of_5" and tour == "ATP"
        else sofascore_base_raw
    )

    ta_base = None
    ta_surf = None
    if player_ta:
        ta_surf = player_ta.get("surface_stats", {}).get(surface)
    if ta_surf and ta_surf.get("ace_pct") is not None:
        ace_pct = ta_surf["ace_pct"]
        # avg_service_pts is already set to 115 for BO5 — this is the primary fix
        ta_base = (ace_pct / 100) * avg_service_pts
        ta_used = True
        ta_surface_matches = ta_surf.get("matches", 0) or 0

    base = ta_base if ta_used else sofascore_base
    if base == 0 or base is None:
        return {"projection": None, "lean": None, "confidence": 0,
                "note": "No ace data available for this surface.",
                "ta_used": ta_used, "ta_surface_matches": ta_surface_matches}

    cpr = cpr_override if cpr_override is not None else COURT_CPR.get(court, CPR_NEUTRAL)

    # ── L2: Opponent suppression — TA first_won_pct blended with Sofascore ───
    opp_ta_surf = None
    if opponent_ta:
        opp_ta_surf = opponent_ta.get("surface_stats", {}).get(surface)

    # Sofascore suppression (always computed as fallback component)
    opp_ret1 = _safe(opponent_stats.get("return_first_serve_pts_won"))
    tour_avg_ret1 = ATP_TOUR_AVERAGES["return_first_serve_pts_won"]
    if opp_ret1 > 0:
        if opp_ret1 > tour_avg_ret1:
            ss_suppression = 1 - (opp_ret1 - tour_avg_ret1) / 120
        else:
            ss_suppression = 1 + (tour_avg_ret1 - opp_ret1) / 200
    else:
        ss_suppression = 1.0

    # TA suppression via opponent first_won_pct
    if opp_ta_surf and opp_ta_surf.get("first_won_pct") is not None:
        tour_avg_fw = _TOUR_AVG_FIRST_WON.get(tour, 72.0)
        opp_fw = opp_ta_surf["first_won_pct"]
        raw_ta_supp = 1.0 - (opp_fw - tour_avg_fw) / tour_avg_fw
        raw_ta_supp = max(0.70, min(1.30, raw_ta_supp))
        # Blend 60% TA + 40% Sofascore if both available
        if opp_ret1 > 0:
            suppression = 0.60 * raw_ta_supp + 0.40 * ss_suppression
        else:
            suppression = raw_ta_supp
    else:
        suppression = ss_suppression

    # ── L3: Handedness matchup (Tennis Abstract) ──────────────────────────────
    hand_factor = 1.0
    player_hand = player_ta.get("handedness") if player_ta else None
    opp_hand    = opponent_ta.get("handedness") if opponent_ta else None

    if player_hand and opp_hand:
        factor_table = (
            _HAND_ACE_FACTORS_GRASS
            if surface == "Grass"
            else _HAND_ACE_FACTORS_CLAY_HARD
        )
        # Try TA handedness splits first (ace_pct vs lefties/righties)
        ta_splits   = (player_ta.get("handedness_splits") or {}) if player_ta else {}
        vs_key      = "vs_left" if opp_hand == "L" else "vs_right"
        vs_split    = ta_splits.get(vs_key) or {}
        vs_ace_pct  = vs_split.get("ace_pct")

        # Also check legacy vs_left/vs_right keys (old format)
        if vs_ace_pct is None:
            vs_data = (player_ta.get(vs_key) or {}) if player_ta else {}
            vs_spw  = vs_data.get("serve_pts_won")
            overall_spw = player_ta.get("first_serve_pts_won") if player_ta else None
            if vs_spw and overall_spw and overall_spw > 0:
                ratio = vs_spw / overall_spw
                hand_factor = max(0.85, min(1.20, ratio))
            else:
                hand_factor = factor_table.get((player_hand, opp_hand), 1.0)
        else:
            # Use TA handedness ace_pct vs tour-average ace_pct on this surface
            ta_surf_stats = (player_ta.get("surface_stats") or {}).get(surface) or {}
            overall_ace_pct = ta_surf_stats.get("ace_pct") or (player_ta.get("surface_stats") or {}).get("All", {}).get("ace_pct")
            if overall_ace_pct and overall_ace_pct > 0:
                ratio = vs_ace_pct / overall_ace_pct
                hand_factor = max(0.80, min(1.25, ratio))
            else:
                hand_factor = factor_table.get((player_hand, opp_hand), 1.0)

    # ── L4: Opponent ace-against (SS primary, TA secondary) ──────────────────
    ace_against_factor = 1.0
    opp_ace_against = None
    # SS ace-against (from opp_aces field computed in sofascore_client) — injected
    # into opponent_stats by main.py before reaching here.
    ss_opp_ace_against = opponent_stats.get("ace_against_per_match")
    # TA ace_against as fallback
    if ss_opp_ace_against is None and opponent_ta:
        ss_opp_ace_against = opponent_ta.get("ace_against_per_match")
    # TA ace_pct for opponent is their own ace rate (correlated with server quality)
    # Use it as a proxy for how many aces they face
    ta_opp_ace_against = None
    if opp_ta_surf and opp_ta_surf.get("ace_pct") is not None:
        ta_opp_ace_against = (opp_ta_surf["ace_pct"] / 100) * avg_service_pts

    if ta_opp_ace_against is not None and ss_opp_ace_against is not None and ss_opp_ace_against > 0:
        blended_against = 0.60 * ta_opp_ace_against + 0.40 * ss_opp_ace_against
        opp_ace_against = blended_against
    elif ss_opp_ace_against is not None:
        opp_ace_against = ss_opp_ace_against
    elif ta_opp_ace_against is not None:
        opp_ace_against = ta_opp_ace_against

    if opp_ace_against and opp_ace_against > 0:
        tour_avg_ag = _TOUR_AVG_ACE_AGAINST.get(tour, 5.5)
        raw_factor = opp_ace_against / tour_avg_ag
        ace_against_factor = max(0.70, min(1.50, raw_factor))

    # ── L5: Court speed (CPR) ─────────────────────────────────────────────────
    cpr_factor = 1 + (cpr - CPR_NEUTRAL) / 100

    # ── Combine layers (TA projection) ────────────────────────────────────────
    ta_proj = base * ace_against_factor * hand_factor * suppression * cpr_factor

    # ── Sofascore recency blend ───────────────────────────────────────────────
    p_matches = player_stats.get("matches_played", 0) or 0
    o_matches = opponent_stats.get("matches_played", 0) or 0

    if ta_used and sofascore_base > 0 and p_matches >= 3:
        ss_proj = sofascore_base * ace_against_factor * hand_factor * ss_suppression * cpr_factor
        proj = 0.70 * ta_proj + 0.30 * ss_proj
    else:
        proj = ta_proj

    # ── H2H blend ────────────────────────────────────────────────────────────
    if h2h_ace_avg is not None and h2h_ace_avg > 0:
        proj = proj * 0.70 + h2h_ace_avg * 0.30

    conf = _confidence(p_matches, o_matches, h2h_ace_avg is not None)

    # ── Confidence adjustment for TA sample size ──────────────────────────────
    if ta_used and ta_surface_matches < 20:
        conf = max(0, conf - 8)
    elif ta_used and ta_surface_matches > 50:
        conf = min(95, conf + 5)

    return {
        "projection":          round(proj, 1),
        "lean":                "OVER" if proj > base * 1.05 else "UNDER" if proj < base * 0.95 else "NEUTRAL",
        "confidence":          conf,
        "base_avg":            round(base, 1),
        "ace_against_factor":  round(ace_against_factor, 3),
        "hand_factor":         round(hand_factor, 3),
        "suppression_factor":  round(suppression, 3),
        "cpr_factor":          round(cpr_factor, 3),
        "cpr":                 cpr,
        "player_hand":         player_hand,
        "opp_hand":            opp_hand,
        "opp_ace_against":     round(opp_ace_against, 1) if opp_ace_against else None,
        "ta_used":             ta_used,
        "ta_surface_matches":  ta_surface_matches,
    }


def project_double_faults(
    player_stats: dict,
    opponent_stats: dict,
    h2h_df_avg: float = None,
    player_ta: dict = None,
    opponent_ta: dict = None,
    tour: str = "ATP",
    surface: str = "Hard",
    match_format: str = "best_of_3",
) -> dict:
    # BO5 Grand Slams have ~115 service points vs ~80 for BO3 tours.
    _sp_map = _AVG_SERVICE_PTS.get(tour, {"best_of_3": 80, "best_of_5": 80})
    avg_service_pts = _sp_map.get(match_format, _sp_map["best_of_3"])

    ta_used = False
    ta_surface_matches = 0

    # ── Base DF rate: TA surface stats preferred ──────────────────────────────
    sofascore_base_raw = _safe(player_stats.get("double_faults"))
    # Scale Sofascore blended average for BO5 (partial correction for mixed data)
    _bo5_df_ss_scale = BO5_SS_SCALE.get(surface, 1.30)
    sofascore_base = (
        sofascore_base_raw * _bo5_df_ss_scale
        if match_format == "best_of_5" and tour == "ATP"
        else sofascore_base_raw
    )

    ta_base = None
    ta_surf = None
    if player_ta:
        ta_surf = player_ta.get("surface_stats", {}).get(surface)
    if ta_surf and ta_surf.get("df_pct") is not None:
        df_pct = ta_surf["df_pct"]
        # avg_service_pts is already 115 for BO5 — the primary scaling mechanism
        ta_base = (df_pct / 100) * avg_service_pts
        ta_used = True
        ta_surface_matches = ta_surf.get("matches", 0) or 0

    base = ta_base if ta_used else sofascore_base
    if base == 0 or base is None:
        return {"projection": None, "lean": None, "confidence": 0,
                "note": "No double fault data available for this surface.",
                "ta_used": ta_used, "ta_surface_matches": ta_surface_matches}

    # ── Opponent pressure factor (Sofascore) ──────────────────────────────────
    opp_ret1 = _safe(opponent_stats.get("return_first_serve_pts_won"))
    opp_ret2 = _safe(opponent_stats.get("return_second_serve_pts_won"))
    opp_ret_avg = (opp_ret1 + opp_ret2) / 2 if (opp_ret1 + opp_ret2) > 0 else 0

    tour_avg_ret = 40.0
    if opp_ret_avg > 0:
        if opp_ret_avg > tour_avg_ret:
            pressure = 1 + (opp_ret_avg - tour_avg_ret) / 200
        else:
            pressure = 1 - (tour_avg_ret - opp_ret_avg) / 300
    else:
        pressure = 1.0

    ta_val = base * pressure

    # ── Sofascore recency blend (DFs are streakier — give recency more weight)
    p_matches = player_stats.get("matches_played", 0) or 0
    o_matches = opponent_stats.get("matches_played", 0) or 0

    if ta_used and sofascore_base > 0 and p_matches >= 3:
        ss_val = sofascore_base * pressure
        proj = 0.65 * ta_val + 0.35 * ss_val
    else:
        proj = ta_val

    # ── H2H blend ────────────────────────────────────────────────────────────
    if h2h_df_avg is not None and h2h_df_avg > 0:
        proj = proj * 0.70 + h2h_df_avg * 0.30

    conf = _confidence(p_matches, o_matches, h2h_df_avg is not None)

    # ── Confidence adjustment for TA sample size ──────────────────────────────
    if ta_used and ta_surface_matches < 20:
        conf = max(0, conf - 8)
    elif ta_used and ta_surface_matches > 50:
        conf = min(95, conf + 5)

    return {
        "projection": round(proj, 1),
        "lean": "OVER" if proj > base * 1.1 else "UNDER" if proj < base * 0.9 else "NEUTRAL",
        "confidence": conf,
        "base_avg": round(base, 1),
        "pressure_factor": round(pressure, 3),
        "ta_used": ta_used,
        "ta_surface_matches": ta_surface_matches,
    }


GRAND_SLAMS = {"Australian Open", "US Open", "Roland Garros", "Wimbledon"}

# ---------------------------------------------------------------------------
# Best-of-5 (Grand Slam) break-point scaling
# ---------------------------------------------------------------------------
# ATP men's Grand Slams are played best-of-5 sets.  The per-match BP volume is
# ~1.5× higher than on the regular tour (best-of-3).  Two tables are needed:
#
#   BO5_TOUR_AVG_BP  — used when the tour-average fallback fires at a GS
#                      (replaces the BO3-calibrated TOUR_AVG_BY_SURFACE values)
#
#   BO5_SS_SCALE     — partial upward scale applied to Sofascore blended data
#                      at a GS.  The blended average mixes BO3 + BO5 matches so
#                      a full 1.5× would over-inflate; ~1.30 is a conservative
#                      but meaningful correction.
#
# Ratios derived from historical ATP Grand Slam BP averages:
#   Hard  8.2 → 12.5  (×1.52)   Australian Open, US Open
#   Clay  9.8 → 14.5  (×1.48)   Roland Garros
#   Grass 7.1 → 10.5  (×1.48)   Wimbledon
BO5_TOUR_AVG_BP = {
    "ATP": {
        "Hard":  12.5,
        "Clay":  14.5,
        "Grass": 10.5,
    }
}
BO5_SS_SCALE = {
    "Hard":  1.30,
    "Clay":  1.28,
    "Grass": 1.30,
}

# ---------------------------------------------------------------------------
# Surface-specific BP scale factors for match format.
#
#   best_of_5 (ATP Grand Slams only):
#     Clay  1.60 — Roland Garros: slowest surface, most break opportunities,
#                  longest rallies, highest BP volume per set
#     Hard  1.50 — Australian Open / US Open: moderate pace, solid BP volume
#     Grass 1.40 — Wimbledon: fastest surface, serve dominates, fewest breaks
#                  even in a BO5 setting
#
#   best_of_3 (all other tour events):
#     Clay  1.08 — slower surface means more deuce games and BP opportunities
#     Hard  1.00 — baseline reference
#     Grass 0.93 — serve dominates, points shorter, fewer service game breaks
# ---------------------------------------------------------------------------
SURFACE_FORMAT_BP_SCALE = {
    "best_of_5": {"Clay": 1.60, "Hard": 1.50, "Grass": 1.40},
    "best_of_3": {"Clay": 1.08, "Hard": 1.00, "Grass": 0.93},
}

# ---------------------------------------------------------------------------
# Break-back momentum model constants
#
# Each opponent break statistically increases the broken player's urgency to
# break back immediately — clay produces the largest effect (longer rallies,
# more deuce games = more windows), grass the smallest (short points).
#
# _BO5_MOMENTUM_MULT amplifies the bonus in 5-set matches because momentum
# swings compound across more sets.
# ---------------------------------------------------------------------------
_SURFACE_MOMENTUM_MULT = {"Clay": 0.28, "Hard": 0.25, "Grass": 0.20}
_BO5_MOMENTUM_MULT     = 1.15

# ---------------------------------------------------------------------------
# Match environment detection
# ---------------------------------------------------------------------------
ENVIRONMENT_LABELS = {
    "HIGH_BREAK":  "High Break",
    "SERVE_DOM":   "Serve Dominant",
    "RET_EDGE":    "Returner Edge",
    "WEAK_SERVE":  "Weak Serve Match",
    "STANDARD":    "Standard",
}


def _return_pts_won(stats: dict) -> float:
    """Average of 1st and 2nd serve return pts won %; fallback 33."""
    r1 = stats.get("return_first_serve_pts_won")
    r2 = stats.get("return_second_serve_pts_won")
    if r1 is not None and r2 is not None:
        return (r1 + r2) / 2
    return r1 if r1 is not None else (r2 if r2 is not None else 33.0)


def _hold_rate_proxy(stats: dict) -> float:
    """
    Approximate service-game hold rate (0–1) from serve stats.
    Formula: first_serve_pct × first_serve_won + (1-first_serve_pct) × second_serve_won
    Falls back to tour-average values for any missing field.
    """
    sp1w = _safe(stats.get("first_serve_pts_won"),  72.0) / 100.0
    sp2w = _safe(stats.get("second_serve_pts_won"),  50.0) / 100.0
    fin  = _safe(stats.get("first_serve_pct"),       63.0) / 100.0
    return fin * sp1w + (1.0 - fin) * sp2w


def detect_environment(p1_stats: dict, p2_stats: dict, surface: str = "Hard") -> str:
    """
    Classify match environment using expected breaks per set.

    Returns one of: HIGH_BREAK / SERVE_DOM / RET_EDGE / STANDARD
    Surface is used to adjust break frequency (clay plays longer → more breaks).
    """
    p1_hold = _hold_rate_proxy(p1_stats)
    p2_hold = _hold_rate_proxy(p2_stats)
    combined_hold = (p1_hold + p2_hold) / 2.0

    # Expected total breaks per set: each player's break chance × 6 service games/set
    exp_breaks_per_set = (1.0 - p1_hold) * 6.0 + (1.0 - p2_hold) * 6.0

    # Surface adjustment — clay generates ~12% more breaks, grass ~12% fewer
    surf_break_adj = {"Clay": 1.12, "Hard": 1.0, "Grass": 0.88}
    adj_breaks = exp_breaks_per_set * surf_break_adj.get(surface, 1.0)

    if combined_hold < 0.62 or adj_breaks > 4.5:
        return "HIGH_BREAK"
    elif combined_hold > 0.78 and adj_breaks < 2.5:
        return "SERVE_DOM"
    elif adj_breaks > 3.5:
        return "RET_EDGE"
    return "STANDARD"


# ── Break opportunity scaling ──────────────────────────────────────────────────
def _apply_break_opportunity_scaling(
    base_proj: float,
    match_format: str,
    surface: str,
) -> tuple:
    """
    Apply a small dynamic multiplier reflecting the feedback loop between
    break frequency and total BP opportunities (more breaks → more service
    games played → marginally more BP chances).

    NOTE: Grand Slam BO5 scaling of the *opportunity pool* (estimated_bp_opps)
    is applied earlier in project_break_points, before base_proj is computed.
    This function handles only the within-match feedback loop, which is small
    and format-agnostic after the opportunity pool has been correctly sized.

    Returns (scaled_projection, opportunity_multiplier).
    """
    expected_breaks = base_proj   # base_proj IS the expected-break estimate

    # Graduated opportunity multiplier — capped at +5% to avoid over-inflation.
    # The base projection already encodes BP-faced rate through estimated_bp_opps;
    # this adjustment reflects the marginal feedback that more breaks
    # = more service games = fractionally more BP chances.
    if expected_breaks < 2.0:
        opp_mult = 1.0
    elif expected_breaks < 4.0:
        opp_mult = 1.0 + (expected_breaks - 2.0) * 0.015   # +1.5% per break above 2
    elif expected_breaks < 8.0:
        opp_mult = min(1.05, 1.03 + (expected_breaks - 4.0) * 0.005)
    else:
        opp_mult = 1.05  # cap at +5%

    scaled = base_proj * opp_mult
    logger.info(
        "BP_SCALING | base=%.2f | expected_breaks=%.2f | "
        "opp_mult=%.3f | scaled=%.2f | surface=%s | format=%s",
        base_proj, expected_breaks, opp_mult, scaled, surface, match_format,
    )
    return scaled, opp_mult


def _returner_dominance_factor(
    player_stats: dict,
    opponent_stats: dict,
    tour: str,
) -> float:
    """
    Factor in how dominant the player is as a returner against THIS specific server.

    A returner who wins significantly more return points than tour average against
    this server's serve quality will create additional deuce/BP situations.

    Returns a multiplier in [0.92, 1.10].
    """
    TOUR_AVG_RET_1ST = {"ATP": 0.38, "WTA": 0.40}
    TOUR_AVG_RET_2ND = {"ATP": 0.54, "WTA": 0.55}

    avg_ret_1st = TOUR_AVG_RET_1ST.get(tour, 0.38)
    avg_ret_2nd = TOUR_AVG_RET_2ND.get(tour, 0.54)

    # Player return stats (as fractions)
    p_ret1 = _safe(player_stats.get("return_first_serve_pts_won"), avg_ret_1st * 100) / 100.0
    p_ret2 = _safe(player_stats.get("return_second_serve_pts_won"), avg_ret_2nd * 100) / 100.0

    # Opponent serve-in rate (weights for 1st vs 2nd serve exposure)
    opp_fin = _safe(opponent_stats.get("first_serve_pct"), 63.0) / 100.0
    first_w  = opp_fin
    second_w = 1.0 - opp_fin

    return_edge_1st = (p_ret1 - avg_ret_1st) * first_w
    return_edge_2nd = (p_ret2 - avg_ret_2nd) * second_w
    combined_edge   = return_edge_1st + return_edge_2nd

    if combined_edge > 0.08:
        factor = 1.10
    elif combined_edge > 0.05:
        factor = 1.06
    elif combined_edge > 0.02:
        factor = 1.03
    elif combined_edge > -0.02:
        factor = 1.0
    elif combined_edge > -0.05:
        factor = 0.96
    else:
        factor = 0.92

    logger.info(
        "RETURNER_DOMINANCE | ret_edge_1st=%.3f | ret_edge_2nd=%.3f | "
        "combined=%.3f | factor=%.3f",
        return_edge_1st, return_edge_2nd, combined_edge, factor,
    )
    return factor


# ---------------------------------------------------------------------------
# Total Games
# ---------------------------------------------------------------------------
def _expected_sets(tour: str, court: str, p1_wr: float = 50.0, p2_wr: float = 50.0) -> float:
    if tour == "WTA":
        base = 2.1
    elif court in GRAND_SLAMS:
        return 3.6
    else:
        base = 2.3
    balance = abs(p1_wr - p2_wr)
    if balance > 20:
        return max(2.0, base - 0.2)
    if balance < 10:
        return min(2.6, base + 0.15)
    return base


def project_total_games(
    player_stats: dict,
    opponent_stats: dict,
    surface: str,
    h2h_games_avg: float = None,
    tour: str = "ATP",
    court: str = "",
    player_ta: dict = None,
    opponent_ta: dict = None,
) -> dict:
    ta_used = False
    ta_surface_matches = 0

    # ── Sofascore hold rates ──────────────────────────────────────────────────
    p1_srv_ss = _safe(player_stats.get("first_serve_pts_won"), 72.0)
    p2_srv_ss = _safe(opponent_stats.get("first_serve_pts_won"), 72.0)
    combined_hold_ss = (p1_srv_ss + p2_srv_ss) / 2

    # ── TA hold rates: compute from first_in_pct, first_won_pct, second_won_pct
    p1_ta_surf = player_ta.get("surface_stats", {}).get(surface) if player_ta else None
    p2_ta_surf = opponent_ta.get("surface_stats", {}).get(surface) if opponent_ta else None

    def _ta_hold(surf_stats):
        if not surf_stats:
            return None
        fin = surf_stats.get("first_in_pct")
        fw  = surf_stats.get("first_won_pct")
        sw  = surf_stats.get("second_won_pct")
        if fin is None or fw is None or sw is None:
            return None
        return (fin / 100) * (fw / 100) + (1 - fin / 100) * (sw / 100)

    p1_ta_hold = _ta_hold(p1_ta_surf)
    p2_ta_hold = _ta_hold(p2_ta_surf)

    if p1_ta_hold is not None and p2_ta_hold is not None:
        ta_combined_hold = ((p1_ta_hold + p2_ta_hold) / 2) * 100  # scale to % for formula
        ta_used = True
        ta_surface_matches = (
            (p1_ta_surf.get("matches", 0) or 0) + (p2_ta_surf.get("matches", 0) or 0)
        ) // 2

    # ── Blend hold rates ──────────────────────────────────────────────────────
    if ta_used:
        combined_hold = 0.70 * ta_combined_hold + 0.30 * combined_hold_ss
    else:
        combined_hold = combined_hold_ss

    p1_srv = p1_srv_ss  # keep for reporting
    p2_srv = p2_srv_ss

    # ── Games per set from combined hold rate ─────────────────────────────────
    if combined_hold > 75:
        games_per_set = 9.5 + (combined_hold - 75) / 15
        games_per_set = min(10.5, games_per_set)
    elif combined_hold >= 65:
        games_per_set = 8.5 + (combined_hold - 65) / 10
    else:
        games_per_set = max(7.5, 7.5 + (combined_hold - 50) / 15)

    # ── Expected sets adjusted for match balance ──────────────────────────────
    p1_wr = _safe(player_stats.get("win_rate"), 50.0)
    p2_wr = _safe(opponent_stats.get("win_rate"), 50.0)
    exp_sets = _expected_sets(tour, court, p1_wr, p2_wr)

    # ── Raw total games ───────────────────────────────────────────────────────
    proj = games_per_set * exp_sets

    # ── H2H blend at 35% if available ────────────────────────────────────────
    if h2h_games_avg is not None and h2h_games_avg > 0:
        proj = proj * 0.65 + h2h_games_avg * 0.35

    # ── CPR surface adjustment ────────────────────────────────────────────────
    from src.constants import COURT_CPR
    cpr = COURT_CPR.get(court, CPR_NEUTRAL)
    if cpr <= 28:
        gps_adj = 0.4
    elif cpr >= 43:
        gps_adj = -0.3
    else:
        gps_adj = 0.0
    proj += gps_adj * exp_sets

    env = detect_environment(player_stats, opponent_stats, surface=surface)

    p_matches = player_stats.get("matches_played", 0) or 0
    o_matches = opponent_stats.get("matches_played", 0) or 0
    conf = _confidence(p_matches, o_matches, h2h_games_avg is not None)

    # ── Confidence adjustment for TA sample size ──────────────────────────────
    if ta_used and ta_surface_matches < 20:
        conf = max(0, conf - 8)
    elif ta_used and ta_surface_matches > 50:
        conf = min(95, conf + 5)

    proj_no_h2h = games_per_set * exp_sets + gps_adj * exp_sets
    lean = "OVER" if proj > proj_no_h2h * 1.02 else "UNDER" if proj < proj_no_h2h * 0.98 else "NEUTRAL"

    return {
        "projection":          round(proj, 1),
        "lean":                lean,
        "confidence":          conf,
        "games_per_set":       round(games_per_set, 1),
        "expected_sets":       exp_sets,
        "combined_hold":       round(combined_hold, 1),
        "p1_srv":              round(p1_srv, 1),
        "p2_srv":              round(p2_srv, 1),
        "format":              f"Best of {'5' if court in GRAND_SLAMS and tour == 'ATP' else '3'}",
        "environment":         env,
        "cpr":                 cpr,
        "ta_used":             ta_used,
        "ta_surface_matches":  ta_surface_matches,
    }


# ---------------------------------------------------------------------------
# Break Points Won
# ---------------------------------------------------------------------------
def project_break_points(
    player_stats: dict,
    opponent_stats: dict,
    player_all_stats: dict = None,   # all-surface blended stats (Step 9 sanity)
    opponent_all_stats: dict = None, # all-surface opponent stats (opportunity blending)
    h2h_bp_avg: float = None,
    cpr_override: int = None,
    h2h_match_count: int = 0,
    player_ta: dict = None,
    opponent_ta: dict = None,
    surface: str = "Hard",
    tour: str = "ATP",
    opp_ss_matches: int = 0,
    match_format: str = "best_of_3",
    court: str = "",
) -> dict:
    """
    Bidirectional break points won projection model.

    Formula (Step 7):
      projected_bp_won =
          opportunities_created          (Step 1: opp BP faced per match on surface)
        × conversion_rate                (Step 2: player BP conv% on surface, blended)
        × serve_quality_adj              (Step 3: opponent hold rate modifier)
        × momentum_factor                (Step 4: break-back effect)
        × bo_scale                       (Step 5: 1.6 for BO5, 1.0 for BO3)
        × surface_calibration            (Step 6: surface-specific + CPR)

    Grand Slam BO5 only fires when match_format == "best_of_5" (ATP only).
    """
    ta_used = False
    ta_surface_matches = 0
    used_opp_tour_avg  = False

    is_bo5 = (match_format == "best_of_5" and tour == "ATP")
    _fmt_key = "best_of_5" if is_bo5 else "best_of_3"
    bo_scale = SURFACE_FORMAT_BP_SCALE[_fmt_key].get(surface, 1.50 if is_bo5 else 1.00)

    player_name = player_stats.get("player_name", "?")
    opp_name    = opponent_stats.get("player_name", "?")

    logger.info(
        "BP_BIDIR_START | player=%s | opp=%s | surface=%s | tour=%s | "
        "format=%s | bo_scale=%.1f | raw_opp_bp_faced=%s | court=%s",
        player_name, opp_name, surface, tour,
        match_format, bo_scale,
        opponent_stats.get("bp_faced_count"), court or "generic",
    )

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1 — Opportunity pool: opponent BP faced per match (serve stat)
    #
    # "Opponent BP faced" = BPs the opponent faces on their serve per match
    # = break point opportunities the PLAYER creates as a returner.
    # This is bp_faced_count (serve stat) — never return_bp_opportunities.
    #
    # Blending: surface-specific + overall, weighted by opponent surface sample.
    #   ≥ 10 surface matches  → 65 % surface + 35 % overall
    #   5 – 9 surface matches → 40 % surface + 60 % overall
    #   < 5  surface matches  → overall only
    # ─────────────────────────────────────────────────────────────────────────
    raw_opp_bp_faced    = opponent_stats.get("bp_faced_count")         # surface-specific serve stat
    overall_opp_bp_faced = opponent_stats.get("overall_bp_faced_count") # all-surface serve stat
    tour_avg_bp         = _tour_avg(tour, surface)["bp_faced_per_match"]
    min_credible_bp     = tour_avg_bp * 0.25
    opp_surf_sample     = (opponent_stats.get("surface_matches", 0)
                           or opponent_stats.get("matches_played", 0) or 0)

    surf_credible    = raw_opp_bp_faced is not None and raw_opp_bp_faced >= min_credible_bp
    overall_credible = overall_opp_bp_faced is not None and overall_opp_bp_faced >= min_credible_bp

    opp_opps_tier = "tour_avg"
    if surf_credible and overall_credible:
        if opp_surf_sample >= 10:
            estimated_bp_opps = 0.65 * raw_opp_bp_faced + 0.35 * overall_opp_bp_faced
            opp_opps_tier = f"65/35 n={opp_surf_sample}"
        elif opp_surf_sample >= 5:
            estimated_bp_opps = 0.40 * raw_opp_bp_faced + 0.60 * overall_opp_bp_faced
            opp_opps_tier = f"40/60 n={opp_surf_sample}"
        else:
            estimated_bp_opps = overall_opp_bp_faced
            opp_opps_tier = f"overall_only n={opp_surf_sample}"
    elif surf_credible:
        estimated_bp_opps = raw_opp_bp_faced
        opp_opps_tier = "surface_only"
    elif overall_credible:
        estimated_bp_opps = overall_opp_bp_faced
        opp_opps_tier = "overall_fallback"
    else:
        estimated_bp_opps = tour_avg_bp
        used_opp_tour_avg = True

    logger.info(
        "BP_BIDIR_OPP | surf_raw=%s | overall=%s | opp_surf_n=%d | "
        "tier=%s | estimated_opps=%.2f | tour_avg=%.2f",
        raw_opp_bp_faced, overall_opp_bp_faced, opp_surf_sample,
        opp_opps_tier, estimated_bp_opps, tour_avg_bp,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2 — Player BP conversion rate
    #
    # 2a. Surface+Overall SS blend (sample-size weighted):
    #   ≥ 10 surface matches  → 65 % surface + 35 % overall
    #   5 – 9 surface matches → 40 % surface + 60 % overall
    #   < 5  surface matches  → overall only (flag for UI)
    #
    # 2b. Then blend with Tennis Abstract (35 % TA + 65 % SS blend from 2a)
    #     if ≥ 5 TA surface matches available.
    # ─────────────────────────────────────────────────────────────────────────
    conv_rate_source = ""
    player_ta_surf = None

    if player_ta:
        player_ta_surf = player_ta.get("surface_stats", {}).get(surface)

    ta_conv_pct = None
    if (player_ta_surf
            and player_ta_surf.get("bp_conv_pct") is not None
            and player_ta_surf.get("matches", 0) >= 5):
        ta_conv_pct        = player_ta_surf["bp_conv_pct"]
        ta_used            = True
        ta_surface_matches = player_ta_surf.get("matches", 0) or 0

    # 2a — Surface+overall SS blend
    ss_surf_conv    = player_stats.get("bp_converted")           # surface-specific sum/sum rate
    ss_overall_conv = player_stats.get("overall_bp_converted")   # all-surface sum/sum rate
    surf_sample     = (player_stats.get("surface_matches", 0)
                       or player_stats.get("matches_played", 0) or 0)
    overall_sample  = player_stats.get("overall_matches_played", 0) or 0

    surf_only_flag = False   # raised when surface sample < 5 → UI can show warning

    if ss_surf_conv and ss_overall_conv:
        if surf_sample >= 10:
            ss_conv_pct   = 0.65 * ss_surf_conv + 0.35 * ss_overall_conv
            conv_surf_tier = f"SS:65/35 surf_n={surf_sample}"
        elif surf_sample >= 5:
            ss_conv_pct   = 0.40 * ss_surf_conv + 0.60 * ss_overall_conv
            conv_surf_tier = f"SS:40/60 surf_n={surf_sample}"
        else:
            ss_conv_pct   = ss_overall_conv
            conv_surf_tier = f"SS:overall_only surf_n={surf_sample}"
            surf_only_flag = True
    elif ss_surf_conv:
        ss_conv_pct   = ss_surf_conv
        conv_surf_tier = "SS:surface_only"
    elif ss_overall_conv:
        ss_conv_pct   = ss_overall_conv
        conv_surf_tier = "SS:overall_only"
        surf_only_flag = True
    else:
        ss_conv_pct   = None
        conv_surf_tier = "SS:none"

    # 2b — TA blend (TA 35% + SS blend 65%)
    if ta_conv_pct is not None and ss_conv_pct and ss_conv_pct > 0:
        conv_rate_pct    = 0.35 * ta_conv_pct + 0.65 * ss_conv_pct
        conv_rate_source = f"TA(35%)+{conv_surf_tier}"
    elif ta_conv_pct is not None:
        conv_rate_pct    = ta_conv_pct
        conv_rate_source = f"TA {surface}"
    elif ss_conv_pct and ss_conv_pct > 0:
        conv_rate_pct    = ss_conv_pct
        conv_rate_source = conv_surf_tier
    else:
        conv_rate_pct    = None
        conv_rate_source = "none"

    logger.info(
        "BP_BIDIR_CONV | conv_rate_pct=%s | source=%s | "
        "ss_surf=%.1f%% | ss_overall=%.1f%% | surf_n=%d | ta=%.1f%% | "
        "surf_tier=%s",
        conv_rate_pct,
        conv_rate_source,
        ss_surf_conv or 0.0,
        ss_overall_conv or 0.0,
        surf_sample,
        ta_conv_pct or 0.0,
        conv_surf_tier,
    )

    # ── Diagnostic: validate return-stat vs serve-stat separation ─────────────
    # These values are logged at INFO level so they always appear in Railway logs.
    # Return stats (used in conversion rate):
    _ret_conv_raw = player_stats.get("return_bp_converted")    # avg BPs won per match as returner
    _ret_opps_raw = player_stats.get("return_bp_opportunities") # avg BP opps per match as returner
    # Serve stat (must NEVER be used as conversion-rate denominator):
    _srv_faced    = player_stats.get("bp_faced_count")          # avg BPs faced per match on own serve
    logger.info(
        "BP_STAT_AUDIT | player=%s | surface=%s | "
        "return_bp_converted_per_match=%s | return_bp_opps_per_match=%s | "
        "serve_bp_faced_per_match=%s | ss_conv_rate=%.1f%% | "
        "[VERIFY: conv_rate must equal return_bp_converted/return_bp_opps; "
        "bp_faced_count is a SERVE stat and is NOT the denominator]",
        player_name, surface,
        f"{_ret_conv_raw:.2f}" if _ret_conv_raw is not None else "None",
        f"{_ret_opps_raw:.2f}" if _ret_opps_raw is not None else "None",
        f"{_srv_faced:.2f}" if _srv_faced is not None else "None",
        ss_conv_pct or 0.0,
    )
    if _ret_opps_raw and _ret_conv_raw and _ret_opps_raw > 0:
        _direct_rate = _ret_conv_raw / _ret_opps_raw * 100
        logger.info(
            "BP_STAT_AUDIT_RATE | player=%s | direct_rate_from_raw=%.1f%% | "
            "blended_conv_rate=%.1f%% | delta=%.1f%%",
            player_name, _direct_rate, conv_rate_pct or 0.0,
            abs((_direct_rate) - (conv_rate_pct or 0.0)),
        )

    p_matches = player_stats.get("matches_played", 0) or 0
    o_matches = opponent_stats.get("matches_played", 0) or 0

    if not conv_rate_pct:
        return {
            "projection": None, "lean": None, "confidence": 0,
            "note": "No break point conversion data available for this surface.",
            "ta_used": ta_used, "ta_surface_matches": ta_surface_matches,
            "sanity_failed": False, "used_opp_tour_avg": used_opp_tour_avg,
            "match_format": match_format,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 3 — Serve quality adjustment: opponent hold rate
    # Derived from first/second serve stats via _hold_rate_proxy.
    # Thresholds calibrated to service-game hold-rate equivalents:
    #   proxy > 0.70  ≈ 85 %+ hold  → Elite   → reduce opps by 15 %
    #   0.63–0.70     ≈ 75–85 % hold → Good    → no change
    #   < 0.63        ≈ < 75 % hold  → Weak    → increase opps by 10 %
    # ─────────────────────────────────────────────────────────────────────────
    opp_hold_proxy = _hold_rate_proxy(opponent_stats)   # fraction of srv pts won
    if opp_hold_proxy > 0.70:
        serve_quality_adj = 0.85
        opp_serve_tier    = "Elite"
    elif opp_hold_proxy >= 0.63:
        serve_quality_adj = 1.00
        opp_serve_tier    = "Good"
    else:
        serve_quality_adj = 1.10
        opp_serve_tier    = "Weak"

    logger.info(
        "BP_BIDIR_SRV_QUAL | opp_hold_proxy=%.3f | tier=%s | adj=%.2f",
        opp_hold_proxy, opp_serve_tier, serve_quality_adj,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4 — Break-back momentum (additive bonus model)
    #
    # Logic: every time the opponent breaks the player, the player's urgency
    # to break back immediately increases — this is measurable and surface-
    # dependent.  Instead of a multiplicative factor on the base projection,
    # we compute the opponent's projected BP won and add a surface-calibrated
    # bonus to the player's projection.
    #
    # opponent_projected_bp_won:
    #   = opp_conv_rate_blended × player_bp_faced_blended × bo_scale
    #   (simplified — no recursive full formula; no serve-quality correction)
    #
    # momentum_bonus = opp_proj_bp × surface_mult × bo5_mult   (additive)
    #
    # Surface multipliers: Clay 0.28 | Hard 0.25 | Grass 0.20
    # BO5 amplifier:       × 1.15 (more sets = more momentum swing cycles)
    # ─────────────────────────────────────────────────────────────────────────

    # Opponent conversion rate — surface+overall blend
    opp_surf_conv    = opponent_stats.get("bp_converted") or 0.0
    opp_overall_conv = opponent_stats.get("overall_bp_converted") or opp_surf_conv
    opp_sample       = (opponent_stats.get("surface_matches", 0)
                        or opponent_stats.get("matches_played", 0) or 0)

    if opp_surf_conv and opp_overall_conv:
        if opp_sample >= 10:
            opp_blended_conv = 0.65 * opp_surf_conv + 0.35 * opp_overall_conv
        elif opp_sample >= 5:
            opp_blended_conv = 0.40 * opp_surf_conv + 0.60 * opp_overall_conv
        else:
            opp_blended_conv = opp_overall_conv
    else:
        opp_blended_conv = opp_surf_conv or opp_overall_conv or 0.0

    # Player BP faced on own serve — opponent's opportunity pool (surface+overall blend)
    player_bp_faced_surf    = _safe(player_stats.get("bp_faced_count"), tour_avg_bp)
    player_bp_faced_overall = player_stats.get("overall_bp_faced_count") or player_bp_faced_surf
    player_surf_n           = (player_stats.get("surface_matches", 0)
                               or player_stats.get("matches_played", 0) or 0)

    if player_bp_faced_surf and player_bp_faced_overall:
        if player_surf_n >= 10:
            player_bp_faced_blended = 0.65 * player_bp_faced_surf + 0.35 * player_bp_faced_overall
        elif player_surf_n >= 5:
            player_bp_faced_blended = 0.40 * player_bp_faced_surf + 0.60 * player_bp_faced_overall
        else:
            player_bp_faced_blended = player_bp_faced_overall
    else:
        player_bp_faced_blended = player_bp_faced_surf

    # Opponent projected BP won (simplified — scales with bo_scale for BO5)
    opp_projected_bp_won = (opp_blended_conv / 100.0) * player_bp_faced_blended * bo_scale

    # Break-back momentum bonus (additive, surface + format calibrated)
    surface_momentum_mult = _SURFACE_MOMENTUM_MULT.get(surface, 0.25)
    bo5_momentum_mult     = _BO5_MOMENTUM_MULT if is_bo5 else 1.0
    momentum_bonus        = opp_projected_bp_won * surface_momentum_mult * bo5_momentum_mult

    logger.info(
        "BP_MOMENTUM_BREAKBACK | opp_surf_conv=%.1f%% | opp_overall_conv=%.1f%% | "
        "opp_blended_conv=%.1f%% | opp_n=%d | player_bp_faced_blended=%.2f | "
        "opp_proj_bp=%.2f | surf_mult=%.2f | bo5_mult=%.2f | momentum_bonus=%.3f",
        opp_surf_conv, opp_overall_conv, opp_blended_conv, opp_sample,
        player_bp_faced_blended, opp_projected_bp_won,
        surface_momentum_mult, bo5_momentum_mult, momentum_bonus,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 5 — Best-of-5 scaling (bo_scale already set above: 1.6 or 1.0)
    # bo_scale is applied as a multiplicative term in the final formula.
    # ─────────────────────────────────────────────────────────────────────────

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 6 — Surface calibration + CPR
    #
    # Clay:  longer rallies → servers recover more → fewer converts.  ×0.92
    # Hard:  baseline.  ×1.0
    # Grass: fewer opps but higher conversion when they arise.
    #        Handled by: conv_rate ×1.05, opp_opps ×0.85 → net ×0.8925
    # CPR:   slow courts suppress BP conversion (servers have more recovery time);
    #        fast courts amplify it.  Each 10-point deviation from neutral = ±3 %.
    # ─────────────────────────────────────────────────────────────────────────
    grass_conv_boost = 1.0
    grass_opp_shrink = 1.0
    if surface == "Clay":
        surface_cal = 0.92
    elif surface == "Grass":
        grass_conv_boost = 1.05
        grass_opp_shrink = 0.85
        surface_cal      = 1.0   # both adjustments applied separately below
    else:
        surface_cal = 1.00

    # Court pace (CPR) calibration — affects BP conversion only
    cpr = cpr_override if cpr_override is not None else CPR_NEUTRAL
    cpr_delta = (cpr - CPR_NEUTRAL) / 10.0   # positive = faster
    cpr_factor = 1.0 + cpr_delta * 0.03      # ±3% per 10-pt deviation
    cpr_factor = max(0.90, min(1.10, cpr_factor))   # clip at ±10%

    logger.info(
        "BP_BIDIR_SURFACE | surface=%s | surface_cal=%.2f | "
        "grass_conv=%.2f | grass_opp=%.2f | cpr=%d | cpr_factor=%.3f",
        surface, surface_cal, grass_conv_boost, grass_opp_shrink,
        cpr, cpr_factor,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 7 — Combined formula
    #
    # base_proj = opportunities_created
    #           × conversion_rate
    #           × serve_quality_adj
    #           × bo_scale
    #           × surface_calibration
    #           × cpr_factor
    #
    # proj = base_proj + momentum_bonus   (break-back bonus is ADDITIVE)
    #
    # Grass: separate adjustments to opportunity count (×0.85) and conv rate
    # (×1.05).  CPR: ±3% per 10-point deviation from neutral.
    # ─────────────────────────────────────────────────────────────────────────
    effective_opps     = estimated_bp_opps * grass_opp_shrink
    effective_conv_pct = conv_rate_pct * grass_conv_boost

    base_proj = (
        effective_opps
        * (effective_conv_pct / 100.0)
        * serve_quality_adj
        * bo_scale
        * surface_cal
        * cpr_factor
    )
    proj = base_proj + momentum_bonus

    raw_proj = proj   # before handedness / H2H adjustments — used in diagnostic
    logger.info(
        "BP_BIDIR_FORMULA | opps=%.2f | conv=%.1f%% | srv_qual=%.2f | "
        "bo_scale=%.2f | surf_cal=%.2f | cpr_fac=%.3f | "
        "base_proj=%.2f | momentum_bonus=%.3f | proj=%.2f",
        effective_opps, effective_conv_pct, serve_quality_adj,
        bo_scale, surface_cal, cpr_factor,
        base_proj, momentum_bonus, proj,
    )
    # ── Step 7 GS diagnostic: full Sinner-style matchup breakdown ─────────────
    logger.info(
        "BP_GS_DIAGNOSTIC | %s vs %s | surface=%s | format=%s | "
        "player_surf_conv=%.1f%% | player_overall_conv=%.1f%% | blended_conv=%.1f%% | "
        "opp_surf_bp_faced=%.2f | opp_overall_bp_faced=%.2f | blended_opps=%.2f | "
        "bo_scale=%.2f | surf_cal=%.2f | cpr_fac=%.3f | "
        "opp_proj_bp_won=%.2f | surf_momentum_mult=%.2f | bo5_momentum_mult=%.2f | "
        "momentum_bonus=%.3f | base_proj=%.2f | raw_proj_with_momentum=%.2f",
        player_name, opp_name, surface, match_format,
        ss_surf_conv or 0.0, ss_overall_conv or 0.0, conv_rate_pct or 0.0,
        raw_opp_bp_faced or 0.0, overall_opp_bp_faced or 0.0, estimated_bp_opps,
        bo_scale, surface_cal, cpr_factor,
        opp_projected_bp_won, surface_momentum_mult, bo5_momentum_mult,
        momentum_bonus, base_proj, proj,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 8 — Handedness adjustment (carry-over from prior model)
    # ─────────────────────────────────────────────────────────────────────────
    hand_bp_factor = 1.0
    opp_hand = opponent_ta.get("handedness") if opponent_ta else None
    if opp_hand == "L" and player_ta:
        vs_left_bp = player_ta.get("vs_left", {}).get("bp_converted")
        if vs_left_bp and conv_rate_pct > 0:
            hand_bp_factor = max(0.88, min(1.12, vs_left_bp / conv_rate_pct))
    elif opp_hand == "R" and player_ta:
        vs_right_bp = player_ta.get("vs_right", {}).get("bp_converted")
        if vs_right_bp and conv_rate_pct > 0:
            hand_bp_factor = max(0.88, min(1.12, vs_right_bp / conv_rate_pct))
    proj = proj * hand_bp_factor
    logger.info("BP_BIDIR_HAND | opp_hand=%s | hand_factor=%.3f | after=%.2f",
                opp_hand, hand_bp_factor, proj)

    # Match environment — still computed and returned for UI display
    env = detect_environment(player_stats, opponent_stats, surface=surface)

    # ─────────────────────────────────────────────────────────────────────────
    # H2H blend — 25 % weight if ≥ 3 H2H surface matches
    # ─────────────────────────────────────────────────────────────────────────
    h2h_used = h2h_bp_avg is not None and h2h_bp_avg > 0 and h2h_match_count >= 3
    if h2h_used:
        proj_pre_h2h = proj
        proj = proj * 0.75 + h2h_bp_avg * 0.25
        logger.info("BP_BIDIR_H2H | h2h_avg=%.2f | before=%.2f | after=%.2f",
                    h2h_bp_avg, proj_pre_h2h, proj)

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 9 — All-surface sanity check
    # If surface projection < 60 % of player's all-surface reference, blend
    # 30 % toward the all-surface estimate to prevent outlier projections.
    # ─────────────────────────────────────────────────────────────────────────
    all_surface_blended = False
    all_surface_ref = None
    if player_all_stats:
        all_conv_pct = player_all_stats.get("bp_converted")
        # Use return_bp_opportunities (avg BP opps the player creates as RETURNER per match)
        # as the opportunity denominator.  NEVER use bp_faced_count here — that is a
        # SERVE stat (BPs the player faces on their own serve) and has no role in
        # projecting BPs the player WINS as a returner.
        all_bp_opps = (
            player_all_stats.get("return_bp_opportunities")
            or _tour_avg(tour, "Hard")["bp_faced_per_match"]   # tour avg serves as proxy
        )
        if all_conv_pct and all_conv_pct > 0:
            # All-surface reference: use actual all-surface avg (no BO5/surface adj)
            all_surface_ref = (all_conv_pct / 100.0) * all_bp_opps
            if proj < all_surface_ref * 0.60:
                proj_pre_sanity = proj
                proj = proj * 0.70 + all_surface_ref * 0.30
                all_surface_blended = True
                logger.info(
                    "BP_BIDIR_ALL_SURF | surface_proj=%.2f < 60%% of all_surface_ref=%.2f"
                    " → blending to %.2f",
                    proj_pre_sanity, all_surface_ref, proj,
                )

    # ─────────────────────────────────────────────────────────────────────────
    # Sanity bounds (format-aware)
    # ─────────────────────────────────────────────────────────────────────────
    sanity_ok = sanity_check_projection(
        "Break Points Won", proj, tour, player_name, surface,
        match_format=match_format,
    )
    sanity_failed = not sanity_ok
    if sanity_failed:
        logger.warning("BP_BIDIR_SANITY_FAIL | proj=%.2f", proj)

    conf = _confidence(p_matches, o_matches, h2h_used)
    if ta_used and ta_surface_matches < 20:
        conf = max(0, conf - 8)
    elif ta_used and ta_surface_matches > 50:
        conf = min(95, conf + 5)

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 10 — Verification logging (component breakdown)
    # ─────────────────────────────────────────────────────────────────────────
    logger.info(
        "BP_BIDIR_FINAL | player=%s | surface=%s | format=%s | "
        "opps=%.2f | conv_pct=%.1f | srv_qual=%.2f | "
        "bo_scale=%.2f | surf_cal=%.2f | cpr_fac=%.3f | hand=%.3f | "
        "base_proj=%.2f | momentum_bonus=%.3f | opp_proj_bp=%.2f | "
        "h2h_used=%s | all_blend=%s | PROJECTION=%.2f",
        player_name, surface, match_format,
        effective_opps, effective_conv_pct, serve_quality_adj,
        bo_scale, surface_cal, cpr_factor, hand_bp_factor,
        base_proj, momentum_bonus, opp_projected_bp_won,
        h2h_used, all_surface_blended, proj,
    )
    if is_bo5 and proj < 4.5:
        logger.warning(
            "BP_BIDIR_LOW_GS | player=%s | surface=%s | proj=%.2f — "
            "below 4.5 for Grand Slam; check surface conv rate, opp BP faced, "
            "bo_scale, and momentum_bonus above",
            player_name, surface, proj,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Return — includes all components for frontend display (Step 8)
    # ─────────────────────────────────────────────────────────────────────────
    p1_ret = _return_pts_won(player_stats)
    p2_srv = _safe(opponent_stats.get("first_serve_pts_won"), 72.0)

    return {
        "projection":            round(proj, 1),
        "lean":                  "OVER" if proj > base_proj else "UNDER",
        "confidence":            conf,
        # ── Conversion rate ──────────────────────────────────────────────────
        "conv_rate_pct":         round(conv_rate_pct, 1),   # final blended rate used
        "conv_rate_source":      conv_rate_source,
        "surf_conv_pct":         round(ss_surf_conv, 1) if ss_surf_conv else None,    # surface-only
        "overall_conv_pct":      round(ss_overall_conv, 1) if ss_overall_conv else None,  # overall
        "surf_conv_sample":      surf_sample,      # player surface match count
        "overall_conv_sample":   overall_sample,   # player overall match count
        "surf_only_flag":        surf_only_flag,   # True when < 5 surface matches
        # ── Opportunity pool (opponent BP faced on serve) ────────────────────
        "opp_bp_faced":          round(estimated_bp_opps, 1),   # blended opps used
        "surf_opp_bp_faced":     round(raw_opp_bp_faced, 1) if raw_opp_bp_faced else None,
        "overall_opp_bp_faced":  round(overall_opp_bp_faced, 1) if overall_opp_bp_faced else None,
        "opp_surf_sample":       opp_surf_sample,
        "used_opp_tour_avg":     used_opp_tour_avg,
        # ── Formula components ───────────────────────────────────────────────
        "serve_quality_adj":     round(serve_quality_adj, 3),
        "opp_serve_tier":        opp_serve_tier,
        "opp_hold_proxy":        round(opp_hold_proxy, 3),
        "bo_scale":              bo_scale,
        "match_format":          match_format,
        "surface_calibration":   round(surface_cal, 3),
        "cpr_factor":            round(cpr_factor, 4),
        "cpr":                   cpr,
        "hand_bp_factor":        round(hand_bp_factor, 3),
        # ── Break-back momentum (additive) ───────────────────────────────────
        "base_proj":             round(base_proj, 2),
        "opp_projected_bp_won":  round(opp_projected_bp_won, 2),
        "momentum_bonus":        round(momentum_bonus, 3),
        "surface_momentum_mult": surface_momentum_mult,
        "bo5_momentum_mult":     bo5_momentum_mult,
        # ── Display stats ────────────────────────────────────────────────────
        "player_bp_won_per_match":  round((conv_rate_pct / 100.0) * estimated_bp_opps, 1),
        "player_bp_opps_per_match": round(estimated_bp_opps, 1),
        "opp_hold_rate_pct":        round(opp_hold_proxy * 100, 1),
        # ── Environment ──────────────────────────────────────────────────────
        "environment":           env,
        # ── H2H ──────────────────────────────────────────────────────────────
        "h2h_bp_avg":            round(h2h_bp_avg, 1) if h2h_used else None,
        # ── Sanity / quality flags ────────────────────────────────────────────
        "sanity_failed":         sanity_failed,
        "all_surface_blended":   all_surface_blended,
        "all_surface_ref":       round(all_surface_ref, 2) if all_surface_ref else None,
        # ── TA metadata ──────────────────────────────────────────────────────
        "ta_used":               ta_used,
        "ta_surface_matches":    ta_surface_matches,
        # ── Raw display ──────────────────────────────────────────────────────
        "p1_ret":                round(p1_ret, 1),
        "p2_srv":                round(p2_srv, 1),
    }


def generate_scouting_report(
    player_name: str,
    opponent_name: str,
    player_surface_stats: dict,
    opponent_surface_stats: dict,
    player_all_stats: dict,
    opponent_all_stats: dict,
    surface: str,
    court: str,
    prop_type: str,
    projection: dict,
    player_arch: str,
    opponent_arch: str,
    h2h_summary: dict = None,
    player_hand: str = None,
    opponent_hand: str = None,
    player_recent_results: list = None,
    opponent_recent_results: list = None,
    ta_career_matches: int = 0,
    data_quality: str = "moderate",
) -> str:
    """
    Sharp bettor-voice scouting report. 4 sentences max.

    Voice: write like a sharp tennis bettor posting their take in a Discord or
    on Twitter — direct, opinionated, backed by stats but not showing the math.

    Never output:
    - "profiling" in any form
    - "conditions favor X"
    - "trending toward X" when discussing game totals
    - "that's a standard setup" or "that's a <env> setup"
    - "synthesis of" or "matchup dynamics"
    - Archetype labels as nouns (counterpuncher, all-court player) — use
      tendencies instead ("he lives on his return game")
    - Any sentence ending with a confidence percentage
    - Any inline calculation showing X × Y = Z
    """
    from src.constants import COURT_CPR, CPR_NEUTRAL

    # Archetype → plain-English tendency descriptions
    _ARCH_TENDENCY = {
        "Big Server":          "leans hard on his serve to win points",
        "Serve and Volleyer":  "likes to serve big and close points at the net",
        "Precision Baseliner": "keeps a clean serve and competes well on return",
        "Attacking Baseliner": "looks to take over points from the baseline on both wings",
        "Solid Baseliner":     "is comfortable at the back of the court, leans on his return",
        "Counterpuncher":      "lives on his return game and turns defence into break chances",
        "All-Court Player":    "has no obvious weakness in his game",
    }

    def _s(val, fmt=".0f", default="—"):
        if val is None:
            return default
        try:
            return format(float(val), fmt)
        except Exception:
            return default

    def _last(name: str) -> str:
        parts = name.strip().split()
        return parts[-1] if parts else name

    cpr = COURT_CPR.get(court, CPR_NEUTRAL)

    p_aces    = _s(player_surface_stats.get("aces"), ".1f")
    p_dfs     = _s(player_surface_stats.get("double_faults"), ".1f")
    p_1sw     = _s(player_surface_stats.get("first_serve_pts_won"), ".0f")
    p_ret1    = _s(player_surface_stats.get("return_first_serve_pts_won"), ".0f")
    p_bpc     = _s(player_surface_stats.get("bp_converted"), ".0f")
    p_wr      = _s(player_surface_stats.get("win_rate"))
    p_matches = player_surface_stats.get("matches_played", 0) or 0

    o_ret1    = _s(opponent_surface_stats.get("return_first_serve_pts_won"), ".0f")
    o_1sw     = _s(opponent_surface_stats.get("first_serve_pts_won"), ".0f")
    o_aces    = _s(opponent_surface_stats.get("aces"), ".1f")
    o_wr      = _s(opponent_surface_stats.get("win_rate"))
    o_matches = opponent_surface_stats.get("matches_played", 0) or 0

    lean = projection.get("lean", "NEUTRAL")

    p_last = _last(player_name)
    o_last = _last(opponent_name)

    p_tendency = _ARCH_TENDENCY.get(player_arch, "has a balanced game")
    o_tendency = _ARCH_TENDENCY.get(opponent_arch, "has a balanced game")

    # Court description
    court_desc = court if court and court not in ("", "None") else f"{surface} courts"
    court_is_fast = cpr >= 40
    court_is_slow = cpr <= 28

    # Recent results string (first result only for inline reference)
    p_recent_ref = ""
    if player_recent_results:
        p_recent_ref = player_recent_results[0]  # e.g. "W 6-3 6-4 vs Napolitano (Clay, Apr 13)"

    sentences: list = []

    # ── Low-data uncertainty lead ─────────────────────────────────────────────
    # Use TA career matches as primary indicator; fall back to Sofascore count
    effective_matches = ta_career_matches if ta_career_matches > 0 else p_matches
    thin_data = (data_quality == "thin") or effective_matches < 5
    if thin_data:
        match_note = (
            f"{effective_matches} career surface matches in our data"
            if effective_matches > 0 else
            "very limited surface data"
        )
        sentences.append(
            f"{player_name} has {match_note} on {surface}"
            f" — I don't have a great read here, so take this with a grain of salt."
        )

    # ── Prop-specific opening ─────────────────────────────────────────────────
    if prop_type == "Aces":
        _o_ret1_raw = opponent_surface_stats.get("return_first_serve_pts_won") or 0
        suppress_note = (
            "a strong returner who keeps ace totals honest"
            if _o_ret1_raw > 36 else
            "not someone who really suppresses aces"
        )
        hand_note = ""
        if player_hand and opponent_hand and player_hand != opponent_hand:
            hf = projection.get("hand_factor", 1.0) or 1.0
            hand_note = (
                f" {player_hand}H vs {opponent_hand}H angle works in {p_last}'s favour here."
                if hf >= 1.0 else
                f" The {player_hand}H vs {opponent_hand}H matchup cuts into {p_last}'s ace angles."
            )
            hand_note = f"{hand_note}"
        recent_note = f" Most recently: {p_recent_ref}." if p_recent_ref and not thin_data else ""
        sentences.append(
            f"{player_name} is averaging {p_aces} aces on {surface} and {o_last} is {suppress_note}"
            f" — {o_ret1}% on first-serve return points.{hand_note}{recent_note}"
        )
        speed_note = (
            "Fast conditions here mean free points are easier to come by on serve."
            if court_is_fast else
            "Slower conditions mean he'll have to earn those aces — returns come back more often."
            if court_is_slow else
            f"{court_desc} is a medium-pace surface, nothing extreme either way."
        )
        if not thin_data:
            sentences.append(speed_note)

    elif prop_type == "Double Faults":
        pf = projection.get("pressure_factor", 1.0) or 1.0
        pressure_note = (
            "pushes servers to take more risks on second balls"
            if pf > 1.02 else
            "doesn't put huge pressure on second serves"
            if pf < 0.98 else
            "is roughly neutral on second-serve pressure"
        )
        recent_note = f" Recent form: {p_recent_ref}." if p_recent_ref and not thin_data else ""
        sentences.append(
            f"{player_name} is averaging {p_dfs} double faults per match on {surface}"
            f" and {o_last}'s return game {pressure_note}.{recent_note}"
        )
        speed_note = (
            "Fast courts put extra pressure on the second serve — servers push for more."
            if court_is_fast else
            "Slow clay gives servers slightly more margin on the second ball."
            if court_is_slow and surface == "Clay" else
            f"{court_desc} is a medium-pace surface, nothing extreme."
        )
        if not thin_data:
            sentences.append(speed_note)

    elif prop_type == "Total Games":
        ch    = projection.get("combined_hold", 72) or 72
        gps   = projection.get("games_per_set", 0) or 0
        _p1sw_raw = player_surface_stats.get("first_serve_pts_won") or 0
        _o1sw_raw = opponent_surface_stats.get("first_serve_pts_won") or 0
        _p_ret_raw = player_surface_stats.get("return_first_serve_pts_won") or 0
        _o_ret_raw = opponent_surface_stats.get("return_first_serve_pts_won") or 0

        serve_note = (
            "both players hold at a high clip"
            if ch >= 74 else
            "neither player holds at a rate that makes breaks rare"
            if ch <= 68 else
            "hold rates are average for this surface"
        )
        return_note = (
            "The return games on both sides are competitive, which keeps sets tight."
            if (_p_ret_raw > 36 or _o_ret_raw > 36) else
            "Neither player puts massive return pressure on the other's serve."
        )
        recent_note = f" {player_name} recently: {p_recent_ref}." if p_recent_ref and not thin_data else ""
        sentences.append(
            f"On {surface}, {serve_note} — {p_last} holds at {p_1sw}% on first serve"
            f" and {o_last} at {o_1sw}%.{recent_note}"
        )
        if not thin_data:
            sentences.append(return_note)

    elif prop_type == "Break Points Won":
        conv       = projection.get("conv_rate_pct", 0) or 0
        faced      = projection.get("opp_bp_faced", 0) or 0
        serve_tier = projection.get("opp_serve_tier", "")
        bo_scale   = projection.get("bo_scale", 1.0) or 1.0
        momentum   = projection.get("momentum_factor", 1.0) or 1.0
        fmt_label  = "best-of-5" if bo_scale >= 1.5 else "best-of-3"

        serve_tier_note = {
            "Elite": f"{o_last} is an elite server — opportunities will be limited even for good returners.",
            "Good":  f"{o_last} holds at a solid rate, so each opportunity will count.",
            "Weak":  f"{o_last} struggles to hold serve, which inflates the opportunity pool significantly.",
        }.get(serve_tier, "")

        momentum_note = (
            f" Momentum models add {(momentum - 1) * 100:.0f}% for the break-back effect across {fmt_label}."
            if momentum > 1.02 else ""
        )

        recent_note = f" {player_name} recently: {p_recent_ref}." if p_recent_ref and not thin_data else ""
        sentences.append(
            f"{player_name} converts around {conv:.0f}% of break point chances on {surface}"
            f" and {o_last} gives up roughly {faced:.1f} BP opportunities per {fmt_label} match on serve."
            f"{recent_note}"
        )
        if serve_tier_note and not thin_data:
            sentences.append(serve_tier_note + momentum_note)
        elif momentum_note and not thin_data:
            sentences.append(momentum_note.strip())

        speed_note = (
            "Fast courts shrink break-point volume — serves are harder to get back."
            if court_is_fast else
            "Slow clay gives servers more recovery time per point, which suppresses conversion rates even when opportunities exist."
            if court_is_slow and surface == "Clay" else
            f"{court_desc} is a medium-pace court — nothing extreme either way."
        )
        if not thin_data:
            sentences.append(speed_note)

    # ── Player tendency + H2H (one combined sentence to stay within 4-sentence limit) ─
    h2h_str = ""
    if h2h_summary and h2h_summary.get("total", 0) > 0:
        total = h2h_summary["total"]
        p1w   = h2h_summary.get("p1_wins", 0)
        p2w   = total - p1w
        surf_total = h2h_summary.get("surface_matches", 0)
        meeting_word = "meeting" if total == 1 else "meetings"
        if total <= 2:
            h2h_str = f"H2H is basically noise at {total} {meeting_word}."
        else:
            if p1w > p2w:
                h2h_str = f"{p_last} leads the H2H {p1w}–{p2w}"
            elif p2w > p1w:
                h2h_str = f"{o_last} leads the H2H {p2w}–{p1w}"
            else:
                h2h_str = f"They're even in the H2H at {p1w}–{p2w}"
            if surf_total > 0:
                sp1w = h2h_summary.get("surface_p1_wins", 0)
                h2h_str += f", {sp1w}–{surf_total - sp1w} on {surface}"
            h2h_str += "."
    else:
        h2h_str = "No meaningful H2H to factor in."

    tendency_sentence = (
        f"{p_last} {p_tendency} on this surface — {o_last} {o_tendency}. {h2h_str}"
    )
    sentences.append(tendency_sentence)

    # ── Lean (always last, no confidence percentage) ──────────────────────────
    lean_phrases = {
        "OVER":    f"I'm leaning OVER on the {prop_type.lower()} — the setup points that way.",
        "UNDER":   f"I'm leaning UNDER on the {prop_type.lower()} — the numbers don't support the high side.",
        "NEUTRAL": f"Tough to take a strong side on the {prop_type.lower()} — I'm staying off this one.",
    }
    sentences.append(lean_phrases.get(lean, lean_phrases["NEUTRAL"]))

    return " ".join(sentences[:4])
