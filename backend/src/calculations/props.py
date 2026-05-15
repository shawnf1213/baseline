from src.constants import COURT_CPR, CPR_NEUTRAL, ATP_TOUR_AVERAGES

# Tour-average aces faced per match — used to normalise opponent ace-against rate
_TOUR_AVG_ACE_AGAINST = {"ATP": 5.5, "WTA": 3.0}

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
      L1 — base ace rate on surface (Sofascore)
      L2 — opponent ace-against rate vs tour average (Tennis Abstract)
      L3 — handedness matchup adjustment (Tennis Abstract)
      L4 — opponent return aggressiveness (Sofascore return_first_serve_pts_won)
      L5 — surface/court CPR (court pace rating)
    """
    base = _safe(player_stats.get("aces"))
    if base == 0:
        return {"projection": None, "lean": None, "confidence": 0,
                "note": "No ace data available for this surface."}

    cpr = cpr_override if cpr_override is not None else COURT_CPR.get(court, CPR_NEUTRAL)

    # ── L2: Opponent ace-against rate (Tennis Abstract) ───────────────────────
    ace_against_factor = 1.0
    opp_ace_against = None
    if opponent_ta:
        opp_ace_against = opponent_ta.get("ace_against_per_match")
    if opp_ace_against and opp_ace_against > 0:
        tour_avg_ag = _TOUR_AVG_ACE_AGAINST.get(tour, 5.5)
        raw_factor = opp_ace_against / tour_avg_ag
        # Clamp to a sensible range — avoids explosion on outliers
        ace_against_factor = max(0.70, min(1.50, raw_factor))

    # ── L3: Handedness matchup (Tennis Abstract) ──────────────────────────────
    hand_factor = 1.0
    player_hand = player_ta.get("handedness") if player_ta else None
    opp_hand    = opponent_ta.get("handedness") if opponent_ta else None

    if player_hand and opp_hand:
        # Choose surface-specific lookup table
        factor_table = (
            _HAND_ACE_FACTORS_GRASS
            if surface == "Grass"
            else _HAND_ACE_FACTORS_CLAY_HARD
        )
        # If TA has explicit vs-handedness serve data, use the ratio to
        # refine the fixed factor (takes precedence when available)
        vs_key  = "vs_left" if opp_hand == "L" else "vs_right"
        vs_data = (player_ta.get(vs_key) or {}) if player_ta else {}
        vs_spw  = vs_data.get("serve_pts_won")
        overall_spw = player_ta.get("first_serve_pts_won") if player_ta else None

        if vs_spw and overall_spw and overall_spw > 0:
            ratio = vs_spw / overall_spw
            hand_factor = max(0.85, min(1.20, ratio))
        else:
            hand_factor = factor_table.get((player_hand, opp_hand), 1.0)

    # ── L4: Opponent return aggressiveness (Sofascore) ────────────────────────
    opp_ret1 = _safe(opponent_stats.get("return_first_serve_pts_won"))
    tour_avg_ret1 = ATP_TOUR_AVERAGES["return_first_serve_pts_won"]
    if opp_ret1 > 0:
        if opp_ret1 > tour_avg_ret1:
            suppression = 1 - (opp_ret1 - tour_avg_ret1) / 120
        else:
            suppression = 1 + (tour_avg_ret1 - opp_ret1) / 200
    else:
        suppression = 1.0

    # ── L5: Court speed (CPR) ─────────────────────────────────────────────────
    cpr_factor = 1 + (cpr - CPR_NEUTRAL) / 100

    # ── Combine layers ────────────────────────────────────────────────────────
    proj = base * ace_against_factor * hand_factor * suppression * cpr_factor

    if h2h_ace_avg is not None and h2h_ace_avg > 0:
        proj = proj * 0.70 + h2h_ace_avg * 0.30

    p_matches = player_stats.get("matches_played", 0) or 0
    o_matches = opponent_stats.get("matches_played", 0) or 0
    conf = _confidence(p_matches, o_matches, h2h_ace_avg is not None)

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
    }


def project_double_faults(
    player_stats: dict,
    opponent_stats: dict,
    h2h_df_avg: float = None,
) -> dict:
    base = _safe(player_stats.get("double_faults"))
    if base == 0:
        return {"projection": None, "lean": None, "confidence": 0,
                "note": "No double fault data available for this surface."}

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

    proj = base * pressure

    if h2h_df_avg is not None and h2h_df_avg > 0:
        proj = proj * 0.70 + h2h_df_avg * 0.30

    p_matches = player_stats.get("matches_played", 0) or 0
    o_matches = opponent_stats.get("matches_played", 0) or 0
    conf = _confidence(p_matches, o_matches, h2h_df_avg is not None)

    return {
        "projection": round(proj, 1),
        "lean": "OVER" if proj > base * 1.1 else "UNDER" if proj < base * 0.9 else "NEUTRAL",
        "confidence": conf,
        "base_avg": round(base, 1),
        "pressure_factor": round(pressure, 3),
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
) -> dict:
    p1_srv = _safe(player_stats.get("first_serve_pts_won"), 72.0)
    p2_srv = _safe(opponent_stats.get("first_serve_pts_won"), 72.0)
    combined_hold = (p1_srv + p2_srv) / 2

    # Step 2 — games per set from combined hold rate (continuous, no discontinuity at boundaries)
    if combined_hold > 75:
        # 9.5 → 10.5 from 75 → 90
        games_per_set = 9.5 + (combined_hold - 75) / 15
        games_per_set = min(10.5, games_per_set)
    elif combined_hold >= 65:
        # 8.5 → 9.5 from 65 → 75
        games_per_set = 8.5 + (combined_hold - 65) / 10
    else:
        # 7.5 → 8.5 from 50 → 65
        games_per_set = max(7.5, 7.5 + (combined_hold - 50) / 15)

    # Step 3 — expected sets adjusted for match balance
    p1_wr = _safe(player_stats.get("win_rate"), 50.0)
    p2_wr = _safe(opponent_stats.get("win_rate"), 50.0)
    exp_sets = _expected_sets(tour, court, p1_wr, p2_wr)

    # Step 4 — raw total games
    proj = games_per_set * exp_sets

    # Step 5 — H2H blend at 35% if available
    if h2h_games_avg is not None and h2h_games_avg > 0:
        proj = proj * 0.65 + h2h_games_avg * 0.35

    # Step 6 — CPR surface adjustment
    from src.constants import COURT_CPR
    cpr = COURT_CPR.get(court, CPR_NEUTRAL)
    if cpr <= 28:       # slow clay — longer rallies extend service games
        gps_adj = 0.4   # midpoint of +0.3 to +0.5
    elif cpr >= 43:     # fast grass — points end quickly
        gps_adj = -0.3  # midpoint of -0.2 to -0.4
    else:
        gps_adj = 0.0
    proj += gps_adj * exp_sets

    env = detect_environment(player_stats, opponent_stats)

    p_matches = player_stats.get("matches_played", 0) or 0
    o_matches = opponent_stats.get("matches_played", 0) or 0
    conf = _confidence(p_matches, o_matches, h2h_games_avg is not None)

    proj_no_h2h = games_per_set * exp_sets + gps_adj * exp_sets
    lean = "OVER" if proj > proj_no_h2h * 1.02 else "UNDER" if proj < proj_no_h2h * 0.98 else "NEUTRAL"

    return {
        "projection":      round(proj, 1),
        "lean":            lean,
        "confidence":      conf,
        "games_per_set":   round(games_per_set, 1),
        "expected_sets":   exp_sets,
        "combined_hold":   round(combined_hold, 1),
        "p1_srv":          round(p1_srv, 1),
        "p2_srv":          round(p2_srv, 1),
        "format":          f"Best of {'5' if court in GRAND_SLAMS and tour == 'ATP' else '3'}",
        "environment":     env,
        "cpr":             cpr,
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
    # Step 1 — opportunity pool: how many BPs the opponent faces per match on their serve
    opp_bp_faced = opponent_stats.get("bp_faced_count")

    # Step 2 — player conversion rate %
    # Primary: 100 - opponent's BPSvd from Tennis Abstract (surface-specific)
    # Fallback: Sofascore bp_converted aggregate
    conv_rate_source = ""
    opp_ta_surf = None
    if opponent_ta:
        surf_stats = opponent_ta.get("surface_stats", {})
        opp_ta_surf = surf_stats.get(surface) or surf_stats.get("All")

    if opp_ta_surf and opp_ta_surf.get("bp_conv_pct") is not None and opp_ta_surf.get("matches", 0) >= 5:
        # Use 100 - opponent's BPSvd% on this surface (what returners convert against them)
        conv_rate_pct = opp_ta_surf["bp_conv_pct"]
        conv_rate_source = f"TA {surface}"
    else:
        conv_rate_pct = player_stats.get("bp_converted")
        conv_rate_source = ""

    if not opp_bp_faced or opp_bp_faced == 0:
        return {"projection": None, "lean": None, "confidence": 0,
                "note": "Insufficient break-point-faced data for opponent on this surface."}
    if not conv_rate_pct or conv_rate_pct == 0:
        return {"projection": None, "lean": None, "confidence": 0,
                "note": "No break point conversion data available for this surface."}

    # Step 3 — base projection = conversion rate × opponent BPs faced per match
    proj = (conv_rate_pct / 100) * opp_bp_faced

    # Step 3b — Handedness adjustment (Tennis Abstract)
    # If opponent is left-handed and TA has explicit player vs-LH BP conversion, use it
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

    # Step 4 — H2H blend at 30% if ≥ 3 H2H surface matches
    h2h_used = h2h_bp_avg is not None and h2h_bp_avg > 0 and h2h_match_count >= 3
    if h2h_used:
        proj = proj * 0.70 + h2h_bp_avg * 0.30

    # Step 5 — CPR surface adjustment ±5%
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

    p_matches = player_stats.get("matches_played", 0) or 0
    o_matches = opponent_stats.get("matches_played", 0) or 0
    conf = _confidence(p_matches, o_matches, h2h_used)

    return {
        "projection":        round(proj, 1),
        "lean":              "OVER" if proj > (conv_rate_pct / 100) * opp_bp_faced else "UNDER",
        "confidence":        conf,
        "conv_rate_pct":     round(conv_rate_pct, 1),
        "conv_rate_source":  conv_rate_source,
        "opp_bp_faced":      round(opp_bp_faced, 1),
        "h2h_bp_avg":        round(h2h_bp_avg, 1) if h2h_used else None,
        "hand_bp_factor":    round(hand_bp_factor, 3),
        "cpr_factor":        round(cpr_factor, 4),
        "cpr_adj_pct":       round(cpr_adj * 100, 1),
        "cpr":               cpr,
        "p1_ret":            round(p1_ret, 1),
        "p2_srv":            round(p2_srv, 1),
        "environment":       env,
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
) -> str:
    from src.constants import COURT_CPR, CPR_NEUTRAL

    def _s(val, fmt=".0f", default="—"):
        if val is None:
            return default
        try:
            return format(float(val), fmt)
        except Exception:
            return default

    cpr = COURT_CPR.get(court, CPR_NEUTRAL)
    speed = "fast" if cpr >= 40 else "medium-fast" if cpr >= 36 else "medium" if cpr >= 30 else "slow"

    p_wr = _s(player_surface_stats.get("win_rate"))
    p_aces = _s(player_surface_stats.get("aces"), ".1f")
    p_dfs = _s(player_surface_stats.get("double_faults"), ".1f")
    p_fs = _s(player_surface_stats.get("first_serve_pct"), ".0f")
    p_1sw = _s(player_surface_stats.get("first_serve_pts_won"), ".0f")
    p_ret1 = _s(player_surface_stats.get("return_first_serve_pts_won"), ".0f")
    p_bpc = _s(player_surface_stats.get("bp_converted"), ".0f")
    p_matches = player_surface_stats.get("matches_played", 0) or 0

    o_wr = _s(opponent_surface_stats.get("win_rate"))
    o_aces = _s(opponent_surface_stats.get("aces"), ".1f")
    o_ret1 = _s(opponent_surface_stats.get("return_first_serve_pts_won"), ".0f")
    o_matches = opponent_surface_stats.get("matches_played", 0) or 0

    proj_val = projection.get("projection", "N/A")
    lean = projection.get("lean", "NEUTRAL")
    conf = projection.get("confidence", 50)

    sentences = []

    # Form sentence
    form_word = "strong" if (player_surface_stats.get("win_rate") or 0) > 65 else \
                "solid" if (player_surface_stats.get("win_rate") or 0) > 50 else "inconsistent"
    data_note = f"across {p_matches} tracked {surface} matches" if p_matches > 0 else "with limited surface data"
    sentences.append(
        f"{player_name} brings a {form_word} {surface} record ({p_wr}% win rate {data_note}), "
        f"profiling as a {player_arch} with {p_aces} aces and {p_dfs} double faults per match on the surface."
    )

    # Opponent sentence
    opp_word = "formidable" if (opponent_surface_stats.get("win_rate") or 0) > 65 else \
               "dangerous" if (opponent_surface_stats.get("win_rate") or 0) > 50 else "beatable"
    opp_data = f"across {o_matches} {surface} matches" if o_matches > 0 else "with limited data"
    sentences.append(
        f"{opponent_name} is a {opp_word} {opponent_arch} on {surface} ({o_wr}% win rate {opp_data}), "
        f"averaging {o_aces} aces per match and winning {o_ret1}% of points on opponent first serves."
    )

    # Court/surface sentence
    sentences.append(
        f"The {court} plays as a {speed} surface (CPR {cpr}), which "
        + (f"amplifies serve power and favors {player_name}'s {player_arch.lower()} game." if cpr >= 37 else
           f"rewards consistency and return game, conditions where a {player_arch.lower()} can excel." if cpr <= 28 else
           f"offers balanced conditions where both archetypes are competitive.")
    )

    # Prop-specific sentence
    if prop_type == "Aces":
        _o_ret1_raw = opponent_surface_stats.get("return_first_serve_pts_won") or 0
        suppress_note = (
            f"{opponent_name}'s {o_ret1}% return rate on first serves "
            + ("heavily suppresses ace output." if _o_ret1_raw > 36 else
               f"provides minimal suppression of {player_name}'s serve.")
        )
        # Handedness context
        hand_note = ""
        if player_hand and opponent_hand:
            matchup = f"{player_hand}H vs {opponent_hand}H"
            hf = projection.get("hand_factor", 1.0) or 1.0
            if player_hand != opponent_hand:
                direction = "boosts" if hf >= 1.0 else "reduces"
                hand_note = (
                    f" Handedness matchup ({matchup}) {direction} ace output "
                    f"by {abs(hf - 1) * 100:.0f}% (×{hf:.2f})."
                )
            else:
                hand_note = f" Same-handedness matchup ({matchup}) — no handedness adjustment."
        # Ace-against context
        ag_factor = projection.get("ace_against_factor", 1.0) or 1.0
        opp_ag = projection.get("opp_ace_against")
        ag_note = ""
        if opp_ag is not None:
            ag_desc = "concedes more aces than average" if ag_factor > 1.05 else (
                "concedes fewer aces than average" if ag_factor < 0.95 else "concedes an average number of aces"
            )
            ag_note = f" {opponent_name} {ag_desc} ({opp_ag:.1f}/match, ×{ag_factor:.2f})."
        sentences.append(
            f"For aces, {player_name}'s surface baseline of {p_aces}/match is adjusted by court speed "
            f"(×{projection.get('cpr_factor', 1.0):.2f}) and opponent suppression "
            f"(×{projection.get('suppression_factor', 1.0):.2f}). {suppress_note}{ag_note}{hand_note}"
        )
    elif prop_type == "Double Faults":
        pf = projection.get("pressure_factor", 1.0)
        sentences.append(
            f"Double fault projection is driven by {player_name}'s baseline of {p_dfs}/match on {surface}, "
            f"with opponent pressure adding a ×{pf:.2f} factor — {opponent_name}'s return aggression "
            f"{'increases' if pf > 1.0 else 'reduces'} second-serve stress."
        )
    elif prop_type == "Total Games":
        gps  = projection.get("games_per_set", 0)
        sets = projection.get("expected_sets", 3.0)
        ch   = projection.get("combined_hold", 72)
        env  = ENVIRONMENT_LABELS.get(projection.get("environment", "STANDARD"), "Standard")
        sentences.append(
            f"{env} environment — combined hold rate {ch:.0f}% ({player_name} {p_1sw}%, {opponent_name} "
            f"{_s(opponent_surface_stats.get('first_serve_pts_won'))}%). "
            f"Modeling {gps:.1f} games/set over {sets:.1f} expected sets on {surface}."
        )
    elif prop_type == "Break Points Won":
        conv  = projection.get("conv_rate_pct", 0)
        faced = projection.get("opp_bp_faced", 0)
        h2h_bp  = projection.get("h2h_bp_avg")
        cpr_adj = projection.get("cpr_adj_pct", 0)
        env  = ENVIRONMENT_LABELS.get(projection.get("environment", "STANDARD"), "Standard")
        h2h_str = f" H2H average: {h2h_bp:.1f} BPs." if h2h_bp is not None else ""
        adj_str = f" CPR adjustment: {'+' if cpr_adj >= 0 else ''}{cpr_adj:.1f}%." if cpr_adj != 0 else ""
        sentences.append(
            f"{env} environment — {player_name} converts {conv:.0f}% of break points on {surface}. "
            f"{opponent_name} faces {faced:.1f} BPs/match on serve. "
            f"Base projection: {conv:.0f}% × {faced:.1f} = {(conv/100)*faced:.1f}.{h2h_str}{adj_str}"
        )

    # H2H context
    if h2h_summary and h2h_summary.get("total", 0) > 0:
        total = h2h_summary["total"]
        p1w = h2h_summary.get("p1_wins", 0)
        surf_total = h2h_summary.get("surface_matches", 0)
        h2h_str = f"{p1w}-{total - p1w} overall"
        if surf_total > 0:
            sp1w = h2h_summary.get("surface_p1_wins", 0)
            h2h_str += f", {sp1w}-{surf_total - sp1w} on {surface}"
        sentences.append(
            f"Head-to-head: {player_name} leads {h2h_str} — {'this adds high-confidence context' if surf_total >= 3 else 'limited surface H2H data available'}."
        )

    # Closing
    direction_phrase = {
        "OVER": f"lean OVER on {prop_type.lower()}",
        "UNDER": f"lean UNDER on {prop_type.lower()}",
        "NEUTRAL": f"a neutral lean on {prop_type.lower()}",
    }.get(lean, "a neutral lean")
    sentences.append(
        f"Synthesis of surface data, court speed, and matchup dynamics points to a {direction_phrase}, "
        f"projecting {proj_val} with {conf}% model confidence."
    )

    return " ".join(sentences[:6])
