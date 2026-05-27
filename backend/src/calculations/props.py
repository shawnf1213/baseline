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
PROJECTION_SANITY_BOUNDS = {
    "Break Points Won": {
        "ATP": {"min": 1.5, "max": 12.0},
        "WTA": {"min": 1.5, "max": 14.0},
    },
    "Aces": {
        "ATP": {"min": 0.5, "max": 18.0},
        "WTA": {"min": 0.2, "max": 8.0},
    },
    "Double Faults": {
        "ATP": {"min": 0.3, "max": 8.0},
        "WTA": {"min": 0.3, "max": 10.0},
    },
    "Total Games": {
        "ATP": {"min": 14.0, "max": 39.0},
        "WTA": {"min": 12.0, "max": 39.0},
    },
}


def sanity_check_projection(prop_type: str, projection: float,
                             tour: str, player_name: str,
                             surface: str) -> bool:
    """
    Return True if projection is within realistic bounds, False if it fails.
    Logs a warning on failure. Caller should apply a tour-average fallback
    or flag the result when this returns False.
    """
    bounds = PROJECTION_SANITY_BOUNDS.get(prop_type, {}).get(tour, {})
    if not bounds:
        return True
    if projection < bounds["min"] or projection > bounds["max"]:
        logger.warning(
            "SANITY_FAIL | player=%s | prop=%s | surface=%s | tour=%s | "
            "projection=%.2f outside [%.1f, %.1f]",
            player_name, prop_type, surface, tour,
            projection, bounds["min"], bounds["max"],
        )
        return False
    return True

# Tour-average first-serve points won % — used for TA opponent suppression
_TOUR_AVG_FIRST_WON = {"ATP": 72.0, "WTA": 65.0}

# Average service points per match by tour
_AVG_SERVICE_PTS = {"ATP": 80, "WTA": 70}

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
) -> dict:
    """
    5-layer ace projection model:
      L1 — base ace rate: TA surface stats (primary) or Sofascore (fallback)
      L2 — opponent suppression: TA first_won_pct (primary) blended with Sofascore
      L3 — handedness matchup adjustment (Tennis Abstract)
      L4 — opponent ace-against (TA ace_pct blended with Sofascore ace-against)
      L5 — surface/court CPR (court pace rating)
    """
    avg_service_pts = _AVG_SERVICE_PTS.get(tour, 80)
    ta_used = False
    ta_surface_matches = 0

    # ── L1: Base ace rate — TA surface stats preferred ────────────────────────
    sofascore_base = _safe(player_stats.get("aces"))
    ta_base = None
    ta_surf = None
    if player_ta:
        ta_surf = player_ta.get("surface_stats", {}).get(surface)
    if ta_surf and ta_surf.get("ace_pct") is not None:
        ace_pct = ta_surf["ace_pct"]
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
) -> dict:
    avg_service_pts = _AVG_SERVICE_PTS.get(tour, 80)
    ta_used = False
    ta_surface_matches = 0

    # ── Base DF rate: TA surface stats preferred ──────────────────────────────
    sofascore_base = _safe(player_stats.get("double_faults"))
    ta_base = None
    ta_surf = None
    if player_ta:
        ta_surf = player_ta.get("surface_stats", {}).get(surface)
    if ta_surf and ta_surf.get("df_pct") is not None:
        df_pct = ta_surf["df_pct"]
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
    Apply a dynamic multiplier to reflect the feedback loop between break frequency
    and total BP opportunities.

    More expected breaks → opponent plays more service games → more BP chances.

    Returns (scaled_projection, opportunity_multiplier).
    """
    expected_breaks = base_proj   # base_proj IS the expected-break estimate

    # Surface adjustment on base service games per match
    if match_format == "best_of_5":
        base_service_games = 22.0
    else:
        base_service_games = 13.0

    surf_game_adj = {"Clay": 1.08, "Hard": 1.0, "Grass": 0.94}
    adjusted_service_games = base_service_games * surf_game_adj.get(surface, 1.0)  # noqa: F841 (for logging)

    # Graduated opportunity multiplier — capped at +5% to avoid over-inflation.
    # The base projection already encodes BP-faced rate through estimated_bp_opps;
    # this small adjustment only reflects the marginal feedback that more breaks
    # = more service games = fractionally more BP chances.
    if expected_breaks < 2.0:
        opp_mult = 1.0
    elif expected_breaks < 4.0:
        opp_mult = 1.0 + (expected_breaks - 2.0) * 0.015   # +1.5% per break above 2
    else:
        opp_mult = min(1.05, 1.03 + (expected_breaks - 4.0) * 0.01)

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
    h2h_bp_avg: float = None,
    cpr_override: int = None,
    h2h_match_count: int = 0,
    player_ta: dict = None,
    opponent_ta: dict = None,
    surface: str = "Hard",
    tour: str = "ATP",
    opp_ss_matches: int = 0,
    match_format: str = "best_of_3",
) -> dict:
    """
    Project break points won by player_stats' player.

    Key inputs
    ----------
    opp_ss_matches : int
        How many Sofascore surface matches back the opponent's bp_faced_count
        was computed over. When < 3, the number is too noisy and we fall back
        to the tour-average BP-faced rate for this surface.
    """
    ta_used = False
    ta_surface_matches = 0
    used_opp_tour_avg  = False

    logger.info(
        "BP_WON_START | player=%s | opp=%s | surface=%s | tour=%s | "
        "opp_ss_matches=%d | raw_opp_bp_faced=%s",
        player_stats.get("player_name", "?"),
        opponent_stats.get("player_name", "?"),
        surface, tour, opp_ss_matches,
        opponent_stats.get("bp_faced_count"),
    )

    # ── Step 1: Opportunity pool — opponent BPs faced per match on serve ──────
    raw_opp_bp_faced = opponent_stats.get("bp_faced_count")
    tour_avg_bp = _tour_avg(tour, surface)["bp_faced_per_match"]

    # Minimum credible floor: 25% of tour average for this surface.
    # bp_faced_count is now blended from all 4 SS tiers in blended_stats, so
    # it's reliable for any player with > 0 stat matches. This floor only catches
    # genuinely implausible values (near-zero) that would indicate a parse error.
    # NOTE: opp_ss_matches (last-5 recent stat count) is intentionally NOT used
    # as a fallback trigger here.
    min_credible_bp = tour_avg_bp * 0.25

    if (
        raw_opp_bp_faced is None
        or raw_opp_bp_faced == 0
        or raw_opp_bp_faced < min_credible_bp
    ):
        # bp_faced genuinely missing or implausibly low — fall back to tour avg
        estimated_bp_opps = tour_avg_bp
        used_opp_tour_avg = True
        logger.info(
            "BP_WON_FALLBACK | reason=missing_or_below_floor | raw=%s | "
            "opp_ss_matches=%d | tour_avg=%.2f | surface=%s",
            raw_opp_bp_faced, opp_ss_matches, tour_avg_bp, surface,
        )
    else:
        estimated_bp_opps = raw_opp_bp_faced
        logger.info("BP_WON_OPP_BP | bp_faced=%.2f (opp_ss_recent=%d career-blended)",
                    estimated_bp_opps, opp_ss_matches)

    # ── Step 2: Player conversion rate — TA surface bp_conv_pct as primary ───
    conv_rate_source = ""
    player_ta_surf = None
    opp_ta_surf    = None

    if player_ta:
        player_ta_surf = player_ta.get("surface_stats", {}).get(surface)
    if opponent_ta:
        surf_stats = opponent_ta.get("surface_stats", {})
        opp_ta_surf = surf_stats.get(surface) or surf_stats.get("All")

    # Primary: player's own TA bp_conv_pct on this surface
    ta_conv_rate = None
    if player_ta_surf and player_ta_surf.get("bp_conv_pct") is not None and player_ta_surf.get("matches", 0) >= 5:
        ta_conv_rate = player_ta_surf["bp_conv_pct"]
        ta_used = True
        ta_surface_matches = player_ta_surf.get("matches", 0) or 0
        conv_rate_source = f"TA player {surface}"

    # Secondary: opponent's TA bp_conv_pct (what returners convert against them)
    opp_ta_conv = None
    if opp_ta_surf and opp_ta_surf.get("bp_conv_pct") is not None and opp_ta_surf.get("matches", 0) >= 5:
        opp_ta_conv = opp_ta_surf["bp_conv_pct"]

    # Sofascore fallback conv rate
    ss_conv_rate = player_stats.get("bp_converted")

    # Determine final conversion rate: blend TA player + TA opponent or fall back
    if ta_conv_rate is not None and opp_ta_conv is not None:
        conv_rate_pct = (ta_conv_rate + opp_ta_conv) / 2
        conv_rate_source = f"TA blend {surface}"
    elif ta_conv_rate is not None:
        conv_rate_pct = ta_conv_rate
    elif opp_ta_surf and opp_ta_surf.get("bp_conv_pct") is not None and opp_ta_surf.get("matches", 0) >= 5:
        conv_rate_pct = opp_ta_surf["bp_conv_pct"]
        conv_rate_source = f"TA opp {surface}"
        ta_used = True
        ta_surface_matches = opp_ta_surf.get("matches", 0) or 0
    else:
        conv_rate_pct = ss_conv_rate
        conv_rate_source = "SS"

    logger.info(
        "BP_WON_CONV | conv_rate_pct=%s | source=%s | ta_conv=%s | opp_ta_conv=%s | ss_conv=%s",
        conv_rate_pct, conv_rate_source, ta_conv_rate, opp_ta_conv, ss_conv_rate,
    )

    if not conv_rate_pct or conv_rate_pct == 0:
        return {"projection": None, "lean": None, "confidence": 0,
                "note": "No break point conversion data available for this surface.",
                "ta_used": ta_used, "ta_surface_matches": ta_surface_matches,
                "sanity_failed": False, "used_opp_tour_avg": used_opp_tour_avg}

    # ── Step 3: Base projection ───────────────────────────────────────────────
    base_proj = (conv_rate_pct / 100) * estimated_bp_opps
    logger.info(
        "BP_WON_BASE | conv_pct=%.1f | bp_opps=%.2f | base=%.2f | "
        "tour_avg_used=%s",
        conv_rate_pct, estimated_bp_opps, base_proj, used_opp_tour_avg,
    )

    # ── Sofascore recency blend: 75% TA, 25% Sofascore ───────────────────────
    p_matches = player_stats.get("matches_played", 0) or 0
    o_matches = opponent_stats.get("matches_played", 0) or 0

    if ta_used and ss_conv_rate and ss_conv_rate > 0 and p_matches >= 3:
        ss_proj = (ss_conv_rate / 100) * estimated_bp_opps
        proj = 0.75 * base_proj + 0.25 * ss_proj
        logger.info("BP_WON_SS_BLEND | ta_proj=%.2f | ss_proj=%.2f | blended=%.2f",
                    base_proj, ss_proj, proj)
    else:
        proj = base_proj

    # ── Step 3b: Dynamic break-opportunity scaling (feedback loop) ───────────
    proj, opp_scaling_factor = _apply_break_opportunity_scaling(
        proj, match_format, surface
    )

    # ── Step 3c: Returner dominance factor ───────────────────────────────────
    ret_factor = _returner_dominance_factor(player_stats, opponent_stats, tour)
    proj = proj * ret_factor
    logger.info("BP_WON_RET_DOM | ret_factor=%.3f | after=%.2f", ret_factor, proj)

    # ── Step 3d: Handedness adjustment ───────────────────────────────────────
    hand_bp_factor = 1.0
    opp_hand = opponent_ta.get("handedness") if opponent_ta else None
    if opp_hand == "L" and player_ta:
        vs_left_bp = player_ta.get("vs_left", {}).get("bp_converted")
        if vs_left_bp and conv_rate_pct > 0:
            ratio = vs_left_bp / conv_rate_pct
            hand_bp_factor = max(0.85, min(1.15, ratio))
    elif opp_hand == "R" and player_ta:
        vs_right_bp = player_ta.get("vs_right", {}).get("bp_converted")
        if vs_right_bp and conv_rate_pct > 0:
            ratio = vs_right_bp / conv_rate_pct
            hand_bp_factor = max(0.85, min(1.15, ratio))

    proj = proj * hand_bp_factor
    logger.info("BP_WON_HAND | opp_hand=%s | hand_factor=%.3f | after=%.2f",
                opp_hand, hand_bp_factor, proj)

    # ── Step 4: H2H blend at 30% if ≥ 3 H2H surface matches ─────────────────
    h2h_used = h2h_bp_avg is not None and h2h_bp_avg > 0 and h2h_match_count >= 3
    if h2h_used:
        proj_before_h2h = proj
        proj = proj * 0.70 + h2h_bp_avg * 0.30
        logger.info("BP_WON_H2H | h2h_avg=%.2f | before=%.2f | after=%.2f",
                    h2h_bp_avg, proj_before_h2h, proj)

    # ── Step 5: CPR surface adjustment ±5% ───────────────────────────────────
    cpr = cpr_override if cpr_override is not None else CPR_NEUTRAL
    if cpr <= 28:
        cpr_adj = -(28 - cpr) / (28 - 20) * 0.05
    elif cpr >= 43:
        cpr_adj = (cpr - 43) / (50 - 43) * 0.05
    else:
        cpr_adj = 0.0
    cpr_factor = 1.0 + cpr_adj
    proj_before_cpr = proj
    proj = proj * cpr_factor
    logger.info("BP_WON_CPR | cpr=%d | cpr_adj=%.3f | before=%.2f | final=%.2f",
                cpr, cpr_adj, proj_before_cpr, proj)

    # ── Sanity check ─────────────────────────────────────────────────────────
    sanity_ok = sanity_check_projection(
        "Break Points Won", proj, tour,
        player_stats.get("player_name", "?"), surface,
    )
    sanity_failed = not sanity_ok
    if sanity_failed:
        logger.warning(
            "BP_WON_SANITY_FAIL | proj=%.2f | clamping to tour floor %.1f",
            proj, tour_avg_bp * 0.30,
        )

    env = detect_environment(player_stats, opponent_stats, surface=surface)

    p1_ret = _return_pts_won(player_stats)
    p2_srv = _safe(opponent_stats.get("first_serve_pts_won"), 72.0)

    conf = _confidence(p_matches, o_matches, h2h_used)

    # ── Confidence adjustment for TA sample size ──────────────────────────────
    if ta_used and ta_surface_matches < 20:
        conf = max(0, conf - 8)
    elif ta_used and ta_surface_matches > 50:
        conf = min(95, conf + 5)

    return {
        "projection":           round(proj, 1),
        "lean":                 "OVER" if proj > base_proj else "UNDER",
        "confidence":           conf,
        "conv_rate_pct":        round(conv_rate_pct, 1),
        "conv_rate_source":     conv_rate_source,
        "opp_bp_faced":         round(estimated_bp_opps, 1),
        "base_proj":            round(base_proj, 2),
        "opp_scaling_factor":   round(opp_scaling_factor, 3),
        "returner_factor":      round(ret_factor, 3),
        "h2h_bp_avg":           round(h2h_bp_avg, 1) if h2h_used else None,
        "hand_bp_factor":       round(hand_bp_factor, 3),
        "cpr_factor":           round(cpr_factor, 4),
        "cpr_adj_pct":          round(cpr_adj * 100, 1),
        "cpr":                  cpr,
        "p1_ret":               round(p1_ret, 1),
        "p2_srv":               round(p2_srv, 1),
        "environment":          env,
        "ta_used":              ta_used,
        "ta_surface_matches":   ta_surface_matches,
        "sanity_failed":        sanity_failed,
        "used_opp_tour_avg":    used_opp_tour_avg,
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
        conv  = projection.get("conv_rate_pct", 0) or 0
        faced = projection.get("opp_bp_faced", 0) or 0
        recent_note = f" {player_name} recently: {p_recent_ref}." if p_recent_ref and not thin_data else ""
        sentences.append(
            f"{player_name} is converting around {conv:.0f}% of break point chances on {surface}"
            f" and {o_last} gives up roughly {faced:.1f} BP opportunities per match on serve.{recent_note}"
        )
        speed_note = (
            "Fast courts shrink break-point volume — serves are harder to get back."
            if court_is_fast else
            "Slow clay inflates break chances, especially in longer rallies."
            if court_is_slow and surface == "Clay" else
            f"{court_desc} is a medium-pace court — nothing extreme on either side."
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
