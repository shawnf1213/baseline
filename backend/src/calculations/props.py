from src.constants import COURT_CPR, CPR_NEUTRAL, ATP_TOUR_AVERAGES

# Tour-average aces faced per match — used to normalise opponent ace-against rate
_TOUR_AVG_ACE_AGAINST = {"ATP": 5.5, "WTA": 3.0}

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
        vs_key  = "vs_left" if opp_hand == "L" else "vs_right"
        vs_data = (player_ta.get(vs_key) or {}) if player_ta else {}
        vs_spw  = vs_data.get("serve_pts_won")
        overall_spw = player_ta.get("first_serve_pts_won") if player_ta else None

        if vs_spw and overall_spw and overall_spw > 0:
            ratio = vs_spw / overall_spw
            hand_factor = max(0.85, min(1.20, ratio))
        else:
            hand_factor = factor_table.get((player_hand, opp_hand), 1.0)

    # ── L4: Opponent ace-against (TA blended with Sofascore) ─────────────────
    ace_against_factor = 1.0
    opp_ace_against = None
    # Sofascore ace-against
    ss_opp_ace_against = None
    if opponent_ta:
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


def detect_environment(p1_stats: dict, p2_stats: dict) -> str:
    """Return one of HIGH_BREAK / SERVE_DOM / RET_EDGE / WEAK_SERVE / STANDARD."""
    p1_ret = _return_pts_won(p1_stats)
    p2_ret = _return_pts_won(p2_stats)
    p1_srv = _safe(p1_stats.get("first_serve_pts_won"), 72.0)
    p2_srv = _safe(p2_stats.get("first_serve_pts_won"), 72.0)

    # Serve dominant — both hold comfortably, neither returns well
    if p1_srv > 75 and p2_srv > 75 and p1_ret < 35 and p2_ret < 35:
        return "SERVE_DOM"
    # High break — neither holds reliably, both return well
    if p1_ret > 42 and p2_ret > 42 and p1_srv < 70 and p2_srv < 70:
        return "HIGH_BREAK"
    # Returner edge — p1 returns well but faces a strong server
    if p1_ret > 38 and p2_srv > 73:
        return "RET_EDGE"
    # Weak serve match — p1 is a weak returner vs a weak server
    if p1_ret < 35 and p2_srv < 65:
        return "WEAK_SERVE"
    return "STANDARD"


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

    env = detect_environment(player_stats, opponent_stats)

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
) -> dict:
    ta_used = False
    ta_surface_matches = 0

    # ── Step 1: Opportunity pool — opponent BPs faced per match on serve ──────
    opp_bp_faced = opponent_stats.get("bp_faced_count")

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

    # Determine estimated_bp_opportunities from opponent's bp_saved_pct if available
    if opp_ta_surf and opp_ta_surf.get("bp_saved_pct") is not None and opp_bp_faced:
        # Use opp bp_saved_pct to refine opp_bp_faced estimate
        # No override needed — opp_bp_faced from Sofascore is already per-match
        estimated_bp_opps = opp_bp_faced
    else:
        estimated_bp_opps = opp_bp_faced

    if not estimated_bp_opps or estimated_bp_opps == 0:
        return {"projection": None, "lean": None, "confidence": 0,
                "note": "Insufficient break-point-faced data for opponent on this surface.",
                "ta_used": ta_used, "ta_surface_matches": ta_surface_matches}

    # Determine final conversion rate: blend TA player + TA opponent or fall back
    if ta_conv_rate is not None and opp_ta_conv is not None:
        # Both available: weight equally (both describe this specific matchup)
        conv_rate_pct = (ta_conv_rate + opp_ta_conv) / 2
        conv_rate_source = f"TA blend {surface}"
    elif ta_conv_rate is not None:
        conv_rate_pct = ta_conv_rate
    elif opp_ta_surf and opp_ta_surf.get("bp_conv_pct") is not None and opp_ta_surf.get("matches", 0) >= 5:
        # Legacy path: use opponent's bp_conv_pct as it was before
        conv_rate_pct = opp_ta_surf["bp_conv_pct"]
        conv_rate_source = f"TA opp {surface}"
        ta_used = True
        ta_surface_matches = opp_ta_surf.get("matches", 0) or 0
    else:
        conv_rate_pct = ss_conv_rate
        conv_rate_source = ""

    if not conv_rate_pct or conv_rate_pct == 0:
        return {"projection": None, "lean": None, "confidence": 0,
                "note": "No break point conversion data available for this surface.",
                "ta_used": ta_used, "ta_surface_matches": ta_surface_matches}

    # ── Step 3: Base projection ───────────────────────────────────────────────
    ta_proj = (conv_rate_pct / 100) * estimated_bp_opps

    # ── Sofascore recency blend: 75% TA, 25% Sofascore ───────────────────────
    p_matches = player_stats.get("matches_played", 0) or 0
    o_matches = opponent_stats.get("matches_played", 0) or 0

    if ta_used and ss_conv_rate and ss_conv_rate > 0 and p_matches >= 3:
        ss_proj = (ss_conv_rate / 100) * estimated_bp_opps
        proj = 0.75 * ta_proj + 0.25 * ss_proj
    else:
        proj = ta_proj

    # ── Step 3b: Handedness adjustment ───────────────────────────────────────
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

    # ── Step 4: H2H blend at 30% if ≥ 3 H2H surface matches ─────────────────
    h2h_used = h2h_bp_avg is not None and h2h_bp_avg > 0 and h2h_match_count >= 3
    if h2h_used:
        proj = proj * 0.70 + h2h_bp_avg * 0.30

    # ── Step 5: CPR surface adjustment ±5% ───────────────────────────────────
    cpr = cpr_override if cpr_override is not None else CPR_NEUTRAL
    if cpr <= 28:
        cpr_adj = -(28 - cpr) / (28 - 20) * 0.05
    elif cpr >= 43:
        cpr_adj = (cpr - 43) / (50 - 43) * 0.05
    else:
        cpr_adj = 0.0
    cpr_factor = 1.0 + cpr_adj
    proj = proj * cpr_factor

    env = detect_environment(player_stats, opponent_stats)

    p1_ret = _return_pts_won(player_stats)
    p2_srv = _safe(opponent_stats.get("first_serve_pts_won"), 72.0)

    conf = _confidence(p_matches, o_matches, h2h_used)

    # ── Confidence adjustment for TA sample size ──────────────────────────────
    if ta_used and ta_surface_matches < 20:
        conf = max(0, conf - 8)
    elif ta_used and ta_surface_matches > 50:
        conf = min(95, conf + 5)

    return {
        "projection":        round(proj, 1),
        "lean":              "OVER" if proj > (conv_rate_pct / 100) * estimated_bp_opps else "UNDER",
        "confidence":        conf,
        "conv_rate_pct":     round(conv_rate_pct, 1),
        "conv_rate_source":  conv_rate_source,
        "opp_bp_faced":      round(estimated_bp_opps, 1),
        "h2h_bp_avg":        round(h2h_bp_avg, 1) if h2h_used else None,
        "hand_bp_factor":    round(hand_bp_factor, 3),
        "cpr_factor":        round(cpr_factor, 4),
        "cpr_adj_pct":       round(cpr_adj * 100, 1),
        "cpr":               cpr,
        "p1_ret":            round(p1_ret, 1),
        "p2_srv":            round(p2_srv, 1),
        "environment":       env,
        "ta_used":           ta_used,
        "ta_surface_matches": ta_surface_matches,
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
    thin_data = p_matches < 5
    if thin_data:
        sentences.append(
            f"{player_name} has barely played on {surface} this year ({p_matches} matches in the sample)"
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
