import logging
import math

from src.constants import (
    COURT_CPR, CPR_NEUTRAL, ATP_TOUR_AVERAGES, WTA_TOUR_AVERAGES,
    SERVE_QUALITY_TIERS,
)

logger = logging.getLogger(__name__)


# ── Component trace (permanent admin-only instrumentation) ───────────────────
# Every projection component appends one entry here when a trace list is passed,
# so "is the projection working properly" is a single API call instead of log
# archaeology. Passing trace=None (the default, and what every production caller
# does) makes every _trace call a no-op, so this costs nothing on the hot path.
#
# Contract per entry:
#   step      ordinal position in the chain
#   name      the component's identity (C1, cpr_factor, ...)
#   inputs    the values that FED this component
#   value     the component's OWN value (the multiplier/addend it contributes)
#   running   the chain's running result AFTER this component applies
#   note      how it was sourced / whether a clamp or floor bit
def _trace(trace, name, inputs, value, running, note=""):
    if trace is None:
        return running
    trace.append({
        "step":    len(trace) + 1,
        "name":    name,
        "inputs":  inputs,
        "value":   round(value, 4) if isinstance(value, (int, float)) else value,
        "running": round(running, 4) if isinstance(running, (int, float)) else running,
        "note":    note,
    })
    return running


# ── H2H sample gate ──────────────────────────────────────────────────────────
# Every H2H blend below was ungated: any non-null average entered at FULL weight,
# so ONE historical meeting moved the projection as hard as five. Observed
# (Collignon vs Vacherot, Gstaad): an h2h_ace_avg of 1.0 — a single sparse
# meeting — dragged a 7.0 ace projection to 5.8, a 17% cut, at 20% weight.
#
# The tennis reality: one prior meeting says almost nothing about ace counts.
# Ace output is driven by current serve form and conditions, not by what happened
# once against this opponent. So H2H must EARN its weight with meetings:
#     < 3 meetings  -> contributes NOTHING
#       3 meetings  -> half its full weight
#       4 meetings  -> three-quarters
#     >= 5 meetings -> full weight
# Scaling by fraction (not fixed percentages) keeps each prop's own full weight
# intact: aces 20% -> 10/15/20, DF 30% -> 15/22.5/30, Total Games 35% ->
# 17.5/26.25/35.
H2H_MIN_MEETINGS = 3
_H2H_WEIGHT_SCALE = {3: 0.50, 4: 0.75}     # >=5 -> 1.0


def _h2h_weight(full_weight: float, n) -> float:
    """H2H blend weight scaled by meeting count; 0.0 below the minimum."""
    if not isinstance(n, (int, float)) or n < H2H_MIN_MEETINGS:
        return 0.0
    return full_weight * _H2H_WEIGHT_SCALE.get(int(n), 1.0)


# ── games_per_set: per-tour empirical fit ────────────────────────────────────
# Fitted 2026-07-15 on 1,233 completed matches (ATP 603 / WTA 630), deduped by
# event. Each match supplies BOTH variables from itself:
#     player hold   = service_games_won / service_games
#     opponent hold = 1 - return_games_won / return_games
#     combined_hold = mean of the two          -> x
#     games_per_set = total_match_games / sets_played  -> y
#
# WHY IT WAS REPLACED. The old curve was:
#     >75 : 9.5 + (ch-75)/15   |   >=65 : 8.5 + (ch-65)/10
#     else: max(7.5, 7.5 + (ch-50)/15)
# It was calibrated for ATP hold levels and applied to BOTH tours. ATP sits at a
# mean combined hold of 79.9%, where it was roughly right (+/-0.3). WTA sits at
# 64.5% — where it was wrong by -0.5 to -2.3 games/set across its ENTIRE
# operating range. Measured on the live board: Feistel/Samson projected a combined
# 18.9 against a book total of 20.5 priced -120/-120 both ways. The shortfall was
# ~0.7 games/set * 2.3 sets = the whole 1.6-game gap, and Player Total Games Won
# inherited it because PTGW splits this total between the players.
# The old low end also claimed a 50%-hold set averages 7.5 games, which is close to
# impossible: a set is FIRST TO 6, so 6-1 is already 7.
#
# FITTED SLOPES are much gentler than the old 1/15 = 0.0667: ATP 0.0506, WTA
# 0.0329. The old curve over-reacted to hold. The real data is remarkably flat —
# WTA averages 8.6-9.1 games/set across holds from 40 to 65 — because a set needs
# six games won regardless of who is holding.
#
# HONEST LIMITS OF THIS FIT (read before trusting it):
#   * R^2 = 0.157 (ATP) / 0.093 (WTA). Combined hold explains only ~10-15% of the
#     variance in games/set. It is a WEAK predictor, and this curve should be
#     understood as "the conditional mean", not a precise forecast.
#   * Residual sd = ~1.2 games/set => ~+/-2.8 games on a 2.3-set total. Total Games
#     is intrinsically near-coin-flip, which is exactly why books price it at
#     -120/-120 both ways. Any confidence claim on that prop must respect this.
#   * x here is the IN-MATCH combined hold; the model feeds a SEASON-AVERAGE hold,
#     which is less dispersed. Feeding a less-variable x into a curve fitted on a
#     more-variable one under-disperses the output slightly. Acceptable versus a
#     curve that is simply wrong, but it is a known approximation.
#   * Clamped to the observed support [8.3, 11.0] — the fit is not evidence about
#     holds outside the sampled range.
_GPS_FIT = {           # (intercept, slope) — games_per_set = a + b * combined_hold
    "ATP": (5.8218, 0.05061),
    "WTA": (7.2399, 0.03294),
}
_GPS_MIN, _GPS_MAX = 8.3, 11.0


def _games_per_set(combined_hold: float, tour: str = "ATP") -> float:
    """Expected games per set from the combined hold rate, per tour. See the fit
    note above — especially the R^2: this is a conditional mean over a noisy
    relationship, not a precise prediction."""
    a, b = _GPS_FIT.get(tour, _GPS_FIT["ATP"])
    return max(_GPS_MIN, min(_GPS_MAX, a + b * _safe(combined_hold, 65.0)))


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

    # Surface-aware floor for Break Points Won on grass. Grass is the most
    # serve-dominant surface; against an elite server (e.g. a top-10 server at
    # fast Halle) a legitimate projection can land below the generic 1.5 floor
    # (realistic range ~0.8–1.8). Don't flag those as sanity failures — only the
    # hard/clay floor stays at 1.5, where sub-1.5 usually signals a data issue.
    if prop_type == "Break Points Won" and surface == "Grass" and match_format != "best_of_5":
        bounds = {**bounds, "min": 0.6}
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
    trace: list = None,
    h2h_stat_n: int = 0,          # stat-rich H2H meetings behind h2h_ace_avg
) -> dict:
    """
    5-layer ace projection model with expected-sets scaling:
      L1 — base ace rate per set: TA surface stats (primary) or Sofascore (fallback)
            scaled by expected_sets (driven by matchup competitiveness)
      L2 — opponent suppression: TA first_won_pct (primary) blended with Sofascore
      L3 — handedness matchup adjustment (Tennis Abstract)
      L4 — opponent ace-against (TA ace_pct blended with Sofascore ace-against)
      L5 — surface/court CPR (court pace rating)
    """
    # ── Expected sets — driven by matchup competitiveness, not flat BO5 ──
    # match_format is the source of truth (respects the ATP GS Qualifying toggle).
    is_bo5 = (match_format == "best_of_5")
    _p_form = player_stats.get("form") or player_stats.get("recent_form")
    _o_form = opponent_stats.get("form") or opponent_stats.get("recent_form")
    p_prob, o_prob, win_prob_gap = _estimate_win_prob(
        player_stats, opponent_stats,
        p_rank=player_stats.get("rank") or player_stats.get("currentRank"),
        o_rank=opponent_stats.get("rank") or opponent_stats.get("currentRank"),
        p_form=_p_form, o_form=_o_form,
    )
    expected_sets, comp_label = _expected_sets_from_gap(win_prob_gap, is_bo5)
    # Sets-scaling denominator for aces. The per-match ace average is taken
    # over a player's whole season:
    #   BO3 matches  → ~2.35 sets/match (mostly best-of-3 events)
    #   BO5 matches  → the per-match data ALREADY embeds the player's Grand
    #                  Slam BO5 history, so scaling a 4.1-set match against
    #                  2.35 double-counts the long-match volume. Use a
    #                  BO5-appropriate denominator (~3.4) so a Grand Slam
    #                  projection lands at book-realistic levels (Mensik RG
    #                  BO5 ~12-13, matching the 12.5 line, not 21).
    avg_hist_sets = _ACE_BO5_SETS.get(tour, 2.8) if is_bo5 \
        else _ACE_AVG_HISTORICAL_SETS.get(tour, 2.35)
    per_set_scale = expected_sets / max(avg_hist_sets, 0.01)

    # Per-set service points (~ same across BO3/BO5 because match-format
    # averages are roughly proportional to sets played). Used by TA branch.
    _sp_map = _AVG_SERVICE_PTS.get(tour, {"best_of_3": 80, "best_of_5": 80})
    sp_per_set = _sp_map.get("best_of_3", 80) / avg_hist_sets
    avg_service_pts = sp_per_set * expected_sets

    ta_used = False
    ta_surface_matches = 0

    logger.info(
        "ACE_EXPSETS | player=%s | tour=%s | bo5=%s | "
        "p_wr=%.1f o_wr=%.1f | win_prob_gap=%.1fpp | exp_sets=%.2f (%s) | "
        "avg_hist_sets=%.2f | per_set_scale=%.3f | sp_per_set=%.1f | "
        "avg_service_pts=%.1f",
        player_stats.get("player_name", "?"), tour, is_bo5,
        _safe(player_stats.get("win_rate"), 50.0),
        _safe(opponent_stats.get("win_rate"), 50.0),
        win_prob_gap, expected_sets, comp_label,
        avg_hist_sets, per_set_scale, sp_per_set, avg_service_pts,
    )
    _trace(trace, "sets_scaling",
           {"win_prob_gap": round(win_prob_gap, 2), "competitiveness": comp_label,
            "expected_sets": expected_sets, "avg_historical_sets": avg_hist_sets,
            "is_bo5": is_bo5, "tour": tour},
           per_set_scale, per_set_scale,
           "per_set_scale = expected_sets/avg_historical_sets; applied to the "
           "per-match ace average BEFORE the base blend, so it is already inside "
           "every running value below")

    # ── L1: Base ace rate — blend surface form with an overall anchor ─────────
    # Three signals feed the base, and NONE of them is the whole story:
    #   • recency-weighted last-N surface matches — how the player is serving on
    #     this surface NOW (half-life 120d), when match-level ace logs exist;
    #   • a surface ace rate — TA surface ace_pct (deep, smoothed) preferred,
    #     else the Sofascore surface average;
    #   • the player's OVERALL all-surface ace rate — a persistent anchor.
    # The last-N window and the surface sample are each ONE PART: a thin or
    # unrepresentative sample (e.g. five low-ace early-round matches, or no
    # recent grass at all) must not define an elite server's projection, and a
    # single big-ace match must not inflate a grinder's. The surface signal
    # leads in proportion to its sample size; the overall rate always anchors.
    sofascore_base_raw = _safe(player_stats.get("aces"))
    _rw_aces = player_stats.get("recency_weighted_aces")
    if isinstance(_rw_aces, (int, float)) and _rw_aces > 0:
        logger.info("ACE_RECENCY | equal=%.2f -> recency_weighted=%.2f",
                    sofascore_base_raw, _rw_aces)
        sofascore_base_raw = _rw_aces
    # Per-set scaling: divide per-match by historical avg sets, then multiply
    # by THIS match's expected sets.
    sofascore_base = sofascore_base_raw * per_set_scale

    ta_base = None
    ta_surf = None
    if player_ta:
        ta_surf = player_ta.get("surface_stats", {}).get(surface)
    if ta_surf and ta_surf.get("ace_pct") is not None:
        ace_pct = ta_surf["ace_pct"]
        # avg_service_pts now reflects expected sets for THIS match
        ta_base = (ace_pct / 100) * avg_service_pts
        ta_used = True
        ta_surface_matches = ta_surf.get("matches", 0) or 0

    # Primary SURFACE signal (TA preferred — deeper, smoothed — else Sofascore)
    # and its effective sample size.
    surface_base = ta_base if ta_used else sofascore_base
    surf_n = ta_surface_matches if ta_used \
        else int(_safe(player_stats.get("ace_surface_n"), 0))

    # Overall all-surface anchor, put on the SAME per-set scale as the surface
    # signal so the two are blendable.
    _overall_aces = _safe(player_stats.get("overall_aces"), 0.0)
    overall_base = _overall_aces * per_set_scale if _overall_aces > 0 else 0.0

    # Blend — the surface signal is one part, weighted by its sample size, with
    # the surface weight CAPPED so the overall rate always contributes (~>=35%).
    # Thin/empty surface sample → leans on the overall anchor; deep surface
    # history → leads, but is still shrunk toward the player's broader rate.
    if surface_base and surface_base > 0 and overall_base > 0:
        w_surf = min(0.65, surf_n / (surf_n + 8.0))
        base = w_surf * surface_base + (1.0 - w_surf) * overall_base
        logger.info(
            "ACE_BASE_BLEND | surface=%.2f (src=%s n=%d w=%.2f) overall=%.2f -> base=%.2f",
            surface_base, "TA" if ta_used else "SS", surf_n, w_surf, overall_base, base,
        )
        _trace(trace, "L1_base_blend",
               {"surface_base": round(surface_base, 3),
                "surface_src": "TA" if ta_used else "Sofascore",
                "surface_n": surf_n,
                "overall_anchor": round(overall_base, 3),
                "per_set_scale_already_applied": round(per_set_scale, 3)},
               w_surf, base,
               "w_surf = min(0.65, n/(n+8)) — THIS is the 65/35: surface capped at "
               "65%%, overall anchor always >=35%%%s"
               % (" [CAPPED at 0.65]" if surf_n / (surf_n + 8.0) > 0.65 else ""))
    elif surface_base and surface_base > 0:
        base = surface_base
    elif overall_base > 0:
        base = overall_base
        logger.info("ACE_BASE | no surface signal -> overall anchor %.2f", base)
    else:
        base = 0.0

    # Fallback cascade — never fail outright. A player with no ace data at all
    # falls back to the tour-average ace rate for this surface so a projection
    # is still produced (flagged via aces_fallback).
    aces_fallback = False
    if not base or base <= 0:
        base = _tour_avg(tour, surface).get("aces_per_match", 3.0)
        aces_fallback = True
        logger.info("ACE_FALLBACK | tour-avg ace rate %.2f used (no surface data)", base)

    cpr = cpr_override if cpr_override is not None else COURT_CPR.get(court, CPR_NEUTRAL)

    # ── Opponent return context (sets the BLEND WEIGHT, not a multiplier) ────
    # Return points won is NO LONGER a suppression multiplier. It only shifts
    # how much weight the blend gives the opponent's ace-against rate vs the
    # player's own baseline (see the blend below). This removes the old
    # compounding double-suppression that crushed big-server projections.
    opp_ta_surf = None
    if opponent_ta:
        opp_ta_surf = opponent_ta.get("surface_stats", {}).get(surface)
    opp_ret1 = _safe(opponent_stats.get("return_first_serve_pts_won"))

    # ── L3: Handedness matchup (Tennis Abstract) ──────────────────────────────
    hand_factor = 1.0
    # WHY the factor ended up where it did — the trace must report the reason,
    # not infer it from the value. hand_factor==1.0 is reachable three ways
    # (handedness unknown, TA splits absent so the table returned neutral, or a
    # real split that genuinely computed 1.0) and they are not the same fact.
    hand_reason = "handedness unknown for one/both players — no adjustment"
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
                hand_reason = ("TA legacy serve-pts split %s: %.3f ratio, clamped "
                               "[0.85,1.20]" % (vs_key, ratio))
            else:
                hand_factor = factor_table.get((player_hand, opp_hand), 1.0)
                hand_reason = ("no TA handedness split — %s-vs-%s table value "
                               "(%s surface)" % (player_hand, opp_hand,
                                                 "Grass" if surface == "Grass" else "Clay/Hard"))
        else:
            # Use TA handedness ace_pct vs tour-average ace_pct on this surface
            ta_surf_stats = (player_ta.get("surface_stats") or {}).get(surface) or {}
            overall_ace_pct = ta_surf_stats.get("ace_pct") or (player_ta.get("surface_stats") or {}).get("All", {}).get("ace_pct")
            if overall_ace_pct and overall_ace_pct > 0:
                ratio = vs_ace_pct / overall_ace_pct
                hand_factor = max(0.80, min(1.25, ratio))
                hand_reason = ("TA ace_pct %s (%.2f%%) vs own surface ace_pct "
                               "(%.2f%%): %.3f ratio, clamped [0.80,1.25]"
                               % (vs_key, vs_ace_pct, overall_ace_pct, ratio))
            else:
                hand_factor = factor_table.get((player_hand, opp_hand), 1.0)
                hand_reason = ("TA split present but no own-surface ace_pct to "
                               "compare — %s-vs-%s table value" % (player_hand, opp_hand))

    # ── Opponent ace-against rate (aces they concede per match on surface) ────
    # Direct measure: how many aces this opponent gives up as a returner.
    opp_ace_against = None
    ss_opp_ace_against = opponent_stats.get("ace_against_per_match")
    if ss_opp_ace_against is None and opponent_ta:
        ss_opp_ace_against = opponent_ta.get("ace_against_per_match")
    # TA opponent ace_pct is the opponent's OWN serve ace rate, a proxy for how
    # dominant a server they are — NOT how many aces they face. We only use it
    # as a last-resort fallback scaled to the tour-average ace-against level.
    ta_opp_ace_against = None
    if opp_ta_surf and opp_ta_surf.get("ace_pct") is not None:
        ta_opp_ace_against = (opp_ta_surf["ace_pct"] / 100) * avg_service_pts

    if ss_opp_ace_against is not None and ss_opp_ace_against > 0:
        opp_ace_against = ss_opp_ace_against
    elif ta_opp_ace_against is not None and ta_opp_ace_against > 0:
        opp_ace_against = ta_opp_ace_against

    have_real_ace_against = opp_ace_against is not None and opp_ace_against > 0

    # ── CORE: opponent effect is a RELATIVE factor, not an absolute blend ─────
    # A previous version blended the player's ace base toward the opponent's
    # ace-against COUNT. That is mathematically biased: a 12-ace server blended
    # toward an opponent who concedes 6 always gets dragged down — even when
    # that opponent is a BELOW-average returner who should BOOST aces. The
    # opponent's ace-against only tells us whether they concede MORE or FEWER
    # aces than a league-average returner; we apply that as a multiplier on the
    # player's own baseline.
    #
    #   opp_factor = opp_ace_against / tour_avg_ace_against
    #     > 1.0  opponent concedes more aces than average  → boost
    #     < 1.0  opponent concedes fewer (good returner)   → suppress
    #
    # The factor's STRENGTH is governed by return quality (weak returners let
    # the matchup move the number more; elite returners are already captured by
    # their low ace-against). We damp the raw factor toward 1.0 so it nudges
    # rather than dominates, then clamp.
    tour_avg_ag = _TOUR_AVG_ACE_AGAINST.get(tour, 5.5)
    if have_real_ace_against:
        raw_opp_factor = opp_ace_against / tour_avg_ag
        # The opponent's return ability moves aces, but a player only serves
        # aces on their OWN serve — so the opponent effect has a modest
        # ceiling. Damp 70% toward neutral and clamp to ±22%: a 2x
        # ace-conceder yields ~1.22x, a stingy returner ~0.80x.
        _undamped = 1.0 + (raw_opp_factor - 1.0) * 0.30
        opp_factor = max(0.78, min(1.22, _undamped))
        blended = base * opp_factor
        _trace(trace, "L4_opponent_ace_against",
               {"opp_ace_against_per_match": round(opp_ace_against, 3),
                "tour_avg_ace_against": tour_avg_ag,
                "raw_ratio": round(raw_opp_factor, 3),
                "base_in": round(base, 3)},
               opp_factor, blended,
               "RELATIVE MULTIPLIER, not a blend: 1.0+(ratio-1)*0.30 (70%% damped), "
               "clamped [0.78,1.22]%s. NOTE: this REPLACED an older 65/35 blend "
               "toward the opponent's ace COUNT, which was biased against big "
               "servers; w_player/w_opp in the return dict are back-compat aliases."
               % (" [CLAMP BIT]" if abs(_undamped - opp_factor) > 1e-9 else ""))
    else:
        opp_factor = 1.0
        blended = base   # no opponent data → player baseline alone
        _trace(trace, "L4_opponent_ace_against",
               {"opp_ace_against_per_match": None, "base_in": round(base, 3)},
               1.0, blended, "no opponent ace-against data — player baseline alone")
    # Diagnostic-compat aliases
    w_player = 1.0
    w_opp = round(opp_factor, 3)
    opp_ace_against_scaled = (opp_ace_against or 0.0) * (expected_sets / max(avg_hist_sets, 0.01))

    # ── L5: Court speed (ST Pace Index) — surface-relative multiplier ─────────
    from src.constants import GENERIC_SURFACE_CPR as _GEN_SURF_CPR
    surface_baseline = _GEN_SURF_CPR.get(surface, CPR_NEUTRAL)
    _cpr_undamped = 1.0 + (cpr - surface_baseline) * 0.018
    cpr_factor = max(0.65, min(1.35, _cpr_undamped))
    _after_cpr = blended * cpr_factor
    _trace(trace, "L5_cpr_pace_index",
           {"court": court or "(none)", "court_pace_index": cpr,
            "surface_baseline_cpr": surface_baseline,
            "delta_vs_surface": round(cpr - surface_baseline, 2),
            "blended_in": round(blended, 3)},
           cpr_factor, _after_cpr,
           "1.0+(cpr-surface_baseline)*0.018, clamped [0.65,1.35]%s%s"
           % (" [CLAMP BIT]" if abs(_cpr_undamped - cpr_factor) > 1e-9 else "",
              " — court not in COURT_CPR, using CPR_NEUTRAL" if court and court not in COURT_CPR else ""))

    _after_hand = _after_cpr * hand_factor
    _trace(trace, "L3_handedness",
           {"player_hand": player_hand, "opponent_hand": opp_hand,
            "in": round(_after_cpr, 3)},
           hand_factor, _after_hand, hand_reason)

    # ── Surface ace factor — grass boosts, clay suppresses (see constant) ─────
    surface_ace_factor = _SURFACE_ACE_FACTOR.get(surface, 1.0)

    # ── Final projection — blend × CPR × handedness × surface ─────────────────
    proj = blended * cpr_factor * hand_factor * surface_ace_factor
    _trace(trace, "surface_ace_factor",
           {"surface": surface, "in": round(_after_hand, 3)},
           surface_ace_factor, proj, "grass boosts / clay suppresses")

    # ── H2H blend — SAMPLE-GATED (see _h2h_weight) ───────────────────────────
    _h2h_w = _h2h_weight(0.20, h2h_stat_n)
    if h2h_ace_avg is not None and h2h_ace_avg > 0 and _h2h_w > 0:
        _pre_h2h = proj
        proj = proj * (1.0 - _h2h_w) + h2h_ace_avg * _h2h_w
        _trace(trace, "h2h_blend",
               {"model_proj": round(_pre_h2h, 3), "h2h_ace_avg": h2h_ace_avg,
                "h2h_stat_rich_meetings": h2h_stat_n},
               _h2h_w, proj,
               "weight scaled by meetings: %d -> %.0f%% (full weight 20%% needs "
               ">=5)" % (h2h_stat_n, _h2h_w * 100))
    elif h2h_ace_avg is not None and h2h_ace_avg > 0:
        _trace(trace, "h2h_SKIPPED",
               {"h2h_ace_avg": h2h_ace_avg, "h2h_stat_rich_meetings": h2h_stat_n,
                "minimum_required": H2H_MIN_MEETINGS},
               0.0, proj,
               "H2H contributes NOTHING — only %s stat-rich meeting(s), below the "
               "%d minimum. One meeting says almost nothing about ace counts."
               % (h2h_stat_n, H2H_MIN_MEETINGS))
    _trace(trace, "projector_output", {"chain_result": round(proj, 3)},
           proj, round(proj, 1),
           "END OF THE PROJECTOR — NOT the final number. main.py may still apply "
           "indoor / altitude / H2H-psych modifiers after this; see the "
           "post-projector steps and the real FINAL below.")

    p_matches = player_stats.get("matches_played", 0) or 0
    o_matches = opponent_stats.get("matches_played", 0) or 0

    # ── Single comprehensive diagnostic ───────────────────────────────────────
    logger.info(
        "ACE_DIAG | player=%s vs %s | surface=%s court_cpr=%.1f | "
        "base=%.2f | opp_ace_against(raw=%.2f scaled=%.2f have=%s) | "
        "opp_ret1=%.1f%% -> w_player=%.2f/w_opp=%.2f | blended=%.2f | "
        "cpr_factor=%.3f hand_factor=%.3f surf_factor=%.2f | exp_sets=%.2f | FINAL=%.2f",
        player_stats.get("player_name", "?"),
        opponent_stats.get("player_name", "?"),
        surface, cpr,
        base, opp_ace_against or 0.0, opp_ace_against_scaled, have_real_ace_against,
        opp_ret1, w_player, w_opp, blended,
        cpr_factor, hand_factor, surface_ace_factor, expected_sets, proj,
    )

    # Kept for return-dict back-compat (no longer multiplied into proj)
    ace_against_factor = round(opp_ace_against_scaled / max(base, 0.01), 3)
    suppression = round(w_opp, 3)

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
        "surface_ace_factor":  round(surface_ace_factor, 3),
        "cpr":                 cpr,
        "player_hand":         player_hand,
        "opp_hand":            opp_hand,
        "opp_ace_against":     round(opp_ace_against, 1) if opp_ace_against else None,
        "ta_used":             ta_used,
        "ta_surface_matches":  ta_surface_matches,
        # Expected-sets exposure
        "expected_sets":       round(expected_sets, 2),
        "competitiveness":     comp_label,
        "win_prob_gap":        round(win_prob_gap, 1),
        "p1_win_prob":         round(p_prob, 1),
        "p2_win_prob":         round(o_prob, 1),
        "avg_historical_sets": round(avg_hist_sets, 2),
        "per_set_scale":       round(per_set_scale, 3),
        "is_bo5":              is_bo5,
        "aces_per_set":        round(sofascore_base_raw / max(avg_hist_sets, 0.01), 2)
                                if sofascore_base_raw else None,
    }


def project_double_faults(
    player_stats: dict,
    opponent_stats: dict,
    h2h_df_avg: float = None,
    h2h_stat_n: int = 0,          # stat-rich H2H meetings behind h2h_df_avg
    player_ta: dict = None,
    opponent_ta: dict = None,
    tour: str = "ATP",
    surface: str = "Hard",
    match_format: str = "best_of_3",
    court: str = "",
) -> dict:
    """
    Double-fault projection with per-set scaling driven by expected sets.
    A 5-set match has more service games and therefore more DF opportunities
    than a 3-set match — but a 3-set blowout has fewer than a 3-set thriller.
    Scale by (expected_sets / avg_historical_sets).
    """
    # ── Expected sets — driven by matchup competitiveness ──
    is_bo5 = (match_format == "best_of_5")   # respects ATP GS Qualifying toggle
    p_prob, o_prob, win_prob_gap = _estimate_win_prob(
        player_stats, opponent_stats,
        p_rank=player_stats.get("rank") or player_stats.get("currentRank"),
        o_rank=opponent_stats.get("rank") or opponent_stats.get("currentRank"),
        p_form=player_stats.get("form") or player_stats.get("recent_form"),
        o_form=opponent_stats.get("form") or opponent_stats.get("recent_form"),
    )
    expected_sets, comp_label = _expected_sets_from_gap(win_prob_gap, is_bo5)
    # DFs are a per-serve volume stat like aces — use the same BO5-aware
    # denominator: ~2.35 for BO3, ~3.4 for BO5 (the per-match average already
    # embeds Grand Slam BO5 history, so scaling against 2.35 would double-count).
    avg_hist_sets = _ACE_BO5_HISTORICAL_SETS.get(tour, 3.4) if is_bo5 \
        else _ACE_AVG_HISTORICAL_SETS.get(tour, 2.35)
    per_set_scale = expected_sets / max(avg_hist_sets, 0.01)

    # Per-set service pts → total service pts for this expected match length
    _sp_map = _AVG_SERVICE_PTS.get(tour, {"best_of_3": 80, "best_of_5": 80})
    sp_per_set = _sp_map.get("best_of_3", 80) / avg_hist_sets
    avg_service_pts = sp_per_set * expected_sets

    ta_used = False
    ta_surface_matches = 0

    logger.info(
        "DF_EXPSETS | player=%s | tour=%s | bo5=%s | "
        "win_prob_gap=%.1fpp | exp_sets=%.2f (%s) | per_set_scale=%.3f",
        player_stats.get("player_name", "?"), tour, is_bo5,
        win_prob_gap, expected_sets, comp_label, per_set_scale,
    )

    # ── Base DF rate: TA surface stats preferred ──────────────────────────────
    sofascore_base_raw = _safe(player_stats.get("double_faults"))
    # Per-set scaling: per_match / avg_hist_sets * expected_sets
    sofascore_base = sofascore_base_raw * per_set_scale

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
    # Fallback cascade — never fail outright. No surface DF data falls back
    # to the tour-average DF rate for this surface (flagged via df_fallback).
    df_fallback = False
    if base == 0 or base is None:
        base = _tour_avg(tour, surface).get("df_per_match", 2.5)
        df_fallback = True
        logger.info("DF_FALLBACK | tour-avg DF rate %.2f used (no surface data)", base)

    # ── Opponent pressure factor (2nd-serve return pts won only) ─────────────
    # 2nd serve is where DF pressure lives — use 2nd-serve return pct as the
    # signal, falling back to 1st-serve if 2nd unavailable.
    _opp_ret2_raw = opponent_stats.get("return_second_serve_pts_won")
    _opp_ret1_raw = opponent_stats.get("return_first_serve_pts_won")
    if _opp_ret2_raw is not None:
        opp_pressure_ret = float(_opp_ret2_raw)
        # ATP tour avg 2nd-serve return pts won ~52%; WTA ~55%
        tour_avg_ret = 55.0 if tour.upper() == "WTA" else 52.0
    elif _opp_ret1_raw is not None:
        opp_pressure_ret = float(_opp_ret1_raw)
        tour_avg_ret = 40.0  # 1st-serve baseline unchanged
    else:
        opp_pressure_ret = 0.0
        tour_avg_ret = 52.0

    if opp_pressure_ret > 0:
        if opp_pressure_ret > tour_avg_ret:
            pressure = 1 + (opp_pressure_ret - tour_avg_ret) / 200
        else:
            pressure = 1 - (tour_avg_ret - opp_pressure_ret) / 300
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

    # ── H2H blend — SAMPLE-GATED (see _h2h_weight) ───────────────────────────
    # Was ungated at a 30% weight: one sparse meeting moved a DF projection by
    # nearly a third. Same defect as the aces chain, larger blast radius.
    _h2h_w_df = _h2h_weight(0.30, h2h_stat_n)
    if h2h_df_avg is not None and h2h_df_avg > 0 and _h2h_w_df > 0:
        proj = proj * (1.0 - _h2h_w_df) + h2h_df_avg * _h2h_w_df
        logger.info("DF_H2H | avg=%.2f | %d stat-rich meetings -> weight %.0f%%",
                    h2h_df_avg, h2h_stat_n, _h2h_w_df * 100)
    elif h2h_df_avg is not None and h2h_df_avg > 0:
        logger.info("DF_H2H_SKIPPED | avg=%.2f but only %s stat-rich meeting(s) "
                    "(<%d) — H2H contributes nothing",
                    h2h_df_avg, h2h_stat_n, H2H_MIN_MEETINGS)

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
        # Expected-sets exposure
        "expected_sets":       round(expected_sets, 2),
        "competitiveness":     comp_label,
        "win_prob_gap":        round(win_prob_gap, 1),
        "p1_win_prob":         round(p_prob, 1),
        "p2_win_prob":         round(o_prob, 1),
        "avg_historical_sets": round(avg_hist_sets, 2),
        "per_set_scale":       round(per_set_scale, 3),
        "is_bo5":              is_bo5,
        "df_per_set":          round(sofascore_base_raw / max(avg_hist_sets, 0.01), 2)
                                if sofascore_base_raw else None,
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
_SURFACE_MOMENTUM_MULT = {"Clay": 0.10, "Hard": 0.20, "Grass": 0.16}
# Clay reduced 0.28 → 0.15 → 0.12 → 0.10. Diagnosis (Cerundolo vs Landaluce RG):
# the base projection (C1×C2×C3×C8) already lands at ~5.5 — exactly the book
# line. The momentum bonus was adding the entire ~0.7 overage. opp_proj_bp is
# large when the opponent is a weak/young server, so even a small multiplier
# compounds with C8. 0.10 keeps a modest break-back signal without it adding
# a near-full phantom break for high-break matchups.
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


def _game_win_prob(p: float) -> float:
    """Probability of winning a (service) game given per-point win probability p,
    under the standard iid-point model. Maps e.g. 0.66 points → ~0.85 games."""
    p = max(0.01, min(0.99, p))
    q = 1.0 - p
    win_by_4 = p ** 4 * (1 + 4 * q + 10 * q * q)          # to love / 15 / 30
    deuce = 20 * p ** 3 * q ** 3 * (p * p / (p * p + q * q))  # from deuce
    return max(0.0, min(1.0, win_by_4 + deuce))


def _serve_tier_and_adj(sgw_pct, tour: str) -> tuple:
    """Classify a server by SERVICE GAMES WON %, TOUR-RELATIVE (Steps 2/3), and
    return the matching C4 break-point multiplier. ATP serves far bigger than
    WTA, so each tour is judged against its OWN SERVE_QUALITY_TIERS cutoffs —
    never an ATP yardstick on a WTA player. Elite servers concede fewer break
    chances (0.85), weak servers more (1.10).
        ATP: Elite >82 · Strong 74-82 · Average 64-74 · Weak <64
        WTA: Elite >72 · Strong 63-72 · Average 53-63 · Weak <53
    """
    t = SERVE_QUALITY_TIERS.get((tour or "ATP").upper(), SERVE_QUALITY_TIERS["ATP"])
    if sgw_pct is None:
        return "Average", 1.00
    if sgw_pct > t["elite"]:
        return "Elite", 0.85
    if sgw_pct >= t["strong"]:
        return "Strong", 0.93
    if sgw_pct >= t["average"]:
        return "Average", 1.00
    return "Weak", 1.10


def _server_quality_tier_sgw(sgw_pct, tour: str = "ATP", tiebreak_rate=None) -> str:
    """Server-quality badge from SERVICE GAMES WON %, tour-relative (Step 2).
    Same tour cutoffs as the C4 tier above; returns the '<Tier> Server' label.
    NEW SIGNAL 3 — tiebreak supplement: a player who holds >80% of service games
    AND reaches a tiebreak in >30% of sets consistently holds even under
    pressure, so they are definitively Elite regardless of ace rate."""
    if sgw_pct is None:
        return None
    if tiebreak_rate is not None and sgw_pct > 80.0 and tiebreak_rate > 30.0:
        return "Elite Server"
    t = SERVE_QUALITY_TIERS.get((tour or "ATP").upper(), SERVE_QUALITY_TIERS["ATP"])
    if sgw_pct > t["elite"]:
        return "Elite Server"
    if sgw_pct >= t["strong"]:
        return "Strong Server"
    if sgw_pct >= t["average"]:
        return "Average Server"
    return "Weak Server"


def detect_environment(p1_stats: dict, p2_stats: dict,
                       surface: str = "Hard", tour: str = "ATP") -> str:
    """
    Classify match environment from the surface-specific combined hold rate.

    Combined hold = average of both players' first_serve_pts_won on the
    selected surface. The blended-stats pipeline upstream already supplies
    surface-specific stats, so we read first_serve_pts_won directly (no
    all-surface proxy — that was the bug that mislabeled grass).

    Thresholds per (surface, tour) reflect actual tennis dynamics:
      Grass is the most serve-dominant surface → HIGH_BREAK requires an
        extreme breakdown of both servers' hold rates.
      Clay favors returners → easier to qualify as HIGH_BREAK.
      WTA serve stats run ~7-8pp lower than ATP across every surface, so
        WTA thresholds are uniformly lower.

    Returns one of: SERVE_DOM / STANDARD / RET_EDGE / HIGH_BREAK.
    """
    p1_h = _safe(p1_stats.get("first_serve_pts_won"), 70.0)
    p2_h = _safe(p2_stats.get("first_serve_pts_won"), 70.0)
    combined = (p1_h + p2_h) / 2.0

    s = (surface or "Hard").title()
    t = (tour or "ATP").upper()

    # (high_break_below, ret_edge_below, standard_below, serve_dom_at_or_above)
    THRESHOLDS = {
        ("Grass", "ATP"): (58.0, 65.0, 72.0, 72.0),
        ("Grass", "WTA"): (50.0, 58.0, 65.0, 65.0),
        ("Hard",  "ATP"): (60.0, 68.0, 75.0, 75.0),
        ("Hard",  "WTA"): (52.0, 60.0, 68.0, 68.0),
        ("Clay",  "ATP"): (62.0, 70.0, 78.0, 78.0),
        ("Clay",  "WTA"): (54.0, 62.0, 70.0, 70.0),
    }
    high, ret, std, _ = THRESHOLDS.get((s, t), THRESHOLDS[("Hard", "ATP")])

    if combined < high:
        env = "HIGH_BREAK"
    elif combined < ret:
        env = "RET_EDGE"
    elif combined < std:
        env = "STANDARD"
    else:
        env = "SERVE_DOM"

    logger.info(
        "ENV | surface=%s tour=%s | p1_1st=%.1f p2_1st=%.1f combined=%.1f -> %s",
        s, t, p1_h, p2_h, combined, env,
    )
    return env


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
# ─────────────────────────────────────────────────────────────────────────────
# Expected-sets model
#
# Replaces the previous flat BO5 multipliers (1.6, 1.1, etc.) and the
# simplistic _expected_sets helper. The match length is now driven by
# competitiveness: the bigger the win-probability gap, the shorter the match.
#
# BO5 tiers (ATP Grand Slams):
#   gap > 40%   →  3.3 sets  (heavy favorite, most matches 3-0 / 3-1)
#   25% – 40%   →  3.7 sets  (clear favorite)
#   10% – 25%   →  4.1 sets  (slight favorite)
#   gap < 10%   →  4.4 sets  (even matchup, many 4- and 5-setters)
#
# BO3 tiers (everything else):
#   gap > 40%   →  2.1 sets
#   25% – 40%   →  2.3 sets
#   10% – 25%   →  2.5 sets
#   gap < 10%   →  2.6 sets
#
# Average sets in the historical Sofascore data — used to convert per-match
# averages into per-set rates so we can re-scale by expected_sets:
#   WTA  always BO3      →  ~2.30 sets/match
#   ATP  mix of BO3+BO5  →  ~2.60 sets/match
#        Top-20 ATP players average ~15-20 GS matches per season (3.7 sets each)
#        on top of ~25-30 BO3 events (2.2 sets). Weighted avg ≈ 2.60.
#        Using 2.45 underestimated C8 baseline, making the BO5 scaler too
#        aggressive for players whose per-match Sofascore data already embeds
#        significant BO5 volume.
# ─────────────────────────────────────────────────────────────────────────────
_AVG_HISTORICAL_SETS = {"WTA": 2.30, "ATP": 2.60}

# Aces/DFs use their OWN historical-sets denominator. _AVG_HISTORICAL_SETS was
# raised to 2.60 for ATP specifically to tame Break-Points-Won BO5 overcounting
# — but the BP per-match data over-weights top players' heavy Grand Slam (BO5)
# schedules. A volume serve stat like aces is averaged across a player's ENTIRE
# season, which is dominated by BO3 events; the true mean is ~2.35 sets/match.
# Borrowing 2.60 here divided every per-match ace average by too large a number,
# systematically suppressing ace projections (worst for heavy favorites whose
# expected_sets is low — exactly the big-server over-bet case).
_ACE_AVG_HISTORICAL_SETS = {"WTA": 2.20, "ATP": 2.35}

# BO5 (ATP Grand Slam) ace/DF denominator. The per-match ace average already
# contains the player's Grand Slam BO5 matches, so for a BO5 projection the
# expected-sets scaling must be measured against a BO5-appropriate baseline,
# not the BO3-dominated 2.35 — otherwise a 4.1-set projection scales a
# per-match figure that already includes long matches and roughly doubles it.
# 3.4 lands Grand Slam big-server projections at book-realistic levels
# (e.g. Mensik RG BO5 ~12-13 vs the 12.5 line). WTA never plays BO5 but the
# key is kept for symmetry. (Used by the DF model.)
_ACE_BO5_HISTORICAL_SETS = {"WTA": 2.20, "ATP": 3.40}

# Aces-specific BO5 denominator. 3.4 (above) over-suppressed grass big servers:
# a best-of-5 plays ~40% more service games than the BO3-dominated per-match
# average, so ace VOLUME should scale UP, not stay flat. With 3.4, Sinner's
# grass Wimbledon BO5 projected ~9-10 while the market sat ~14 (10+ @ -300,
# 15+ @ +125). 2.8 restores the volume lift; the surface ace factor below keeps
# clay BO5 (e.g. Mensik) controlled so both ends match the book.
_ACE_BO5_SETS = {"WTA": 2.20, "ATP": 2.80}

# Surface ace multiplier — grass yields the most aces per service game, clay the
# fewest, hard in between (neutral baseline). Applied on top of the within-
# surface court-pace (CPR) factor, it corrects the common case where a player's
# surface-specific sample understates their true grass ace output (or a clay
# match's low-ace environment is under-credited). Hard-court BO3 — the bulk of
# the slate — is unaffected (factor 1.0, BO3 denominator unchanged).
_SURFACE_ACE_FACTOR = {"Grass": 1.13, "Clay": 0.82, "Hard": 1.0}


def _is_bo5_match(tour: str, court: str) -> bool:
    """ATP Grand Slams are best-of-5. Everything else is best-of-3."""
    return tour == "ATP" and court in GRAND_SLAMS


# ── Surface affinity ─────────────────────────────────────────────────────────
# The model compares players' raw surface stats and their overall level, but it
# never asks which player the surface RELATIVELY favours. Those are different
# questions: Jones can be the better player overall while clay is her worst
# surface, and Urgesi the lower-level player while clay is her best — a matchup
# far tighter than the level gap implies.
#
# Affinity measures a player against THEIR OWN all-surface baseline, so absolute
# level cancels out. A player whose clay win rate runs 12pp above their overall
# win rate has strong positive clay affinity whether they're ranked 20 or 200.
#
# Win rate carries the most weight (it's the outcome), with service and return
# games won as the mechanism behind it — a surface that suits a player shows up
# as holding and breaking more than they usually do.
_AFFINITY_WEIGHTS = {"win_rate": 0.50, "service_games_won_pct": 0.25,
                     "return_games_won_pct": 0.25}
_AFFINITY_FULL_SAMPLE = 8      # stat-rich surface matches for full weight
SURFACE_AFFINITY_MIN_GAP   = 3.0    # affinity gap below this = not meaningful
SURFACE_AFFINITY_K         = 1.0    # win-prob gap narrowed per point of affinity gap
SURFACE_AFFINITY_MAX_SHIFT = 15.0   # hard cap on the narrowing (pp of gap)
SURFACE_AFFINITY_MIN_KEEP  = 2.0    # favourite must stay favourite by >= this


AFFINITY_MIN_SURFACE_N = 5    # stat-rich matches ON the measured surface
AFFINITY_MIN_OTHER_N   = 8    # stat-rich matches across the OTHER surfaces


def _affinity_raw(stats: dict, base_suffix: str):
    """Weighted mean of (surface stat - baseline stat) for the three affinity
    inputs. ``base_suffix`` selects the reference: 'heldout_' (the player's OTHER
    surfaces) or 'overall_' (all surfaces, INCLUDING this one)."""
    num = den = 0.0
    for surf_key in ("win_rate", "service_games_won_pct", "return_games_won_pct"):
        sv, bv = stats.get(surf_key), stats.get(base_suffix + surf_key)
        if isinstance(sv, (int, float)) and isinstance(bv, (int, float)):
            w = _AFFINITY_WEIGHTS[surf_key]
            num += (sv - bv) * w
            den += w
    return (num / den) if den > 0 else None


def surface_affinity(stats: dict, held_out: bool = True):
    """How much this surface suits the player RELATIVE TO THEIR OWN baseline, in
    percentage points. Positive = a strong surface for them, negative = weak.
    None when it cannot be measured honestly.

    HELD-OUT baseline (default): the reference is the player's OTHER surfaces.
    Measuring against overall_* is circular — 'overall' includes the surface being
    measured, so a clay specialist's clay results inflate the baseline they're
    compared against and every affinity is pulled toward zero. The dilution is
    worst exactly where the signal matters most: a player with mostly clay matches
    has a nearly-all-clay 'overall', so their true clay affinity all but vanishes.
    Pass held_out=False for the legacy diluted method (kept only for the
    side-by-side comparison log).

    MINIMUM SAMPLE: needs >= AFFINITY_MIN_SURFACE_N stat-rich matches on the
    surface AND >= AFFINITY_MIN_OTHER_N across the others. Both sides of a
    difference need enough support — a baseline built from 2 matches is not a
    baseline. Below either threshold the affinity is None and the differential
    does not fire for that player: no adjustment from unmeasurable affinity.

    Scaled by surface sample size beyond the minimum, so a just-qualifying 5-match
    record still can't assert a full-strength affinity."""
    if held_out:
        # SINGLE SOURCE: when the caller has already computed this player's
        # affinity for this surface from raw match records (the per-surface
        # ranking in main.py), use THAT number. Re-deriving it here from `stats`
        # would compare quality-weighted surface figures against a raw held-out
        # reference and disagree with the ranking for the same player+surface.
        # One affinity per player per surface, raw-record basis, quality weighting
        # excluded — the ranking is the only place it's computed.
        if "surface_affinity_precomputed" in stats:
            return stats["surface_affinity_precomputed"]
        n_s = stats.get("surface_stat_n") or 0
        n_o = stats.get("heldout_stat_n") or 0
        if n_s < AFFINITY_MIN_SURFACE_N or n_o < AFFINITY_MIN_OTHER_N:
            logger.info(
                "SURFACE_AFFINITY | %s | INSUFFICIENT SAMPLE — surface stat-rich=%d "
                "(need %d), other-surface stat-rich=%d (need %d) — affinity=None, "
                "differential will not fire for this player",
                stats.get("player_name", "?"), n_s, AFFINITY_MIN_SURFACE_N,
                n_o, AFFINITY_MIN_OTHER_N,
            )
            return None
        raw = _affinity_raw(stats, "heldout_")
        if raw is None:
            return None
        return raw * min(1.0, n_s / _AFFINITY_FULL_SAMPLE)

    raw = _affinity_raw(stats, "overall_")
    if raw is None:
        return None
    n = stats.get("surface_matches") or stats.get("matches_played") or 0
    return raw * min(1.0, n / _AFFINITY_FULL_SAMPLE)


def _estimate_win_prob(p_stats: dict, o_stats: dict,
                       p_rank: int = None, o_rank: int = None,
                       p_form: list = None, o_form: list = None,
                       detail: dict = None) -> tuple:
    """
    Estimate (p_win_prob_pct, o_win_prob_pct, gap_pct) using surface win rate,
    ranking (if available), and recent form (last-10 W/L).

    Key design choice: comparing win rates against *all* opponents understates
    the head-to-head gap (a top-10 player who only plays top-20 opponents has
    a similar overall win rate to a #50 player who plays #100s). To compensate
    we use steep difference-based shares rather than pure ratio shares:
        share = 50 + (p_metric - o_metric) × k  (clamped)

    Component weights when both rankings present:
        surface win rate  : 35%   (k = 1.5)
        ranking advantage : 55%   (k = 18 on log10 scale)
        recent form       : 10%   (k = 0.6)
    Falls back to higher win-rate weight when rank is missing.
    """
    import math

    def _surf_n(stats: dict) -> float:
        return stats.get("surface_matches") or stats.get("matches_played") or 0

    # ── Win rate: all-surface base + a CAPPED surface adjustment ──
    # Overall win rate is the reliable signal. A surface specialist (Golubic on
    # grass) gets a bonus, but it's capped (±8pp) and scaled by sample size so a
    # 90%-on-9-matches surface record can't dominate the estimate.
    def _eff_wr(stats: dict) -> float:
        ov = stats.get("overall_win_rate")
        sf = stats.get("win_rate")
        base = ov if ov is not None else (sf if sf is not None else 50.0)
        if sf is not None and ov is not None:
            w = min(1.0, _surf_n(stats) / 15.0)
            base += max(-8.0, min(8.0, (sf - ov) * w))
        return base

    # ── Serve + return dominance (all-surface — less noisy than thin surface) ──
    def _dominance(s: dict):
        s1 = _safe(s.get("overall_first_serve_pts_won"),  _safe(s.get("first_serve_pts_won"),  68.0))
        s2 = _safe(s.get("overall_second_serve_pts_won"), _safe(s.get("second_serve_pts_won"), 50.0))
        r1 = _safe(s.get("overall_return_first_serve_pts_won"),  _safe(s.get("return_first_serve_pts_won"),  32.0))
        r2 = _safe(s.get("overall_return_second_serve_pts_won"), _safe(s.get("return_second_serve_pts_won"), 52.0))
        return (s1 + s2) / 2.0 + (r1 + r2) / 2.0

    # ── Strength-of-schedule: tier gap (ATP 3 / Challenger 2 / ITF 1) ──
    # Stats earned vs a weak field are worth less. Adds directly to the win-rate
    # and dominance gaps. Zero effect when both players play the same tier
    # (Sinner/Sonego, Golubic/Navarro) — only bites cross-tier matchups.
    sos = (p_stats.get("competition_level") or 2.5) - (o_stats.get("competition_level") or 2.5)

    wr_diff  = (_eff_wr(p_stats) - _eff_wr(o_stats)) + sos * 26.0
    wr_share = max(5.0, min(95.0, 50.0 + wr_diff * 1.2))

    dom_diff  = (_dominance(p_stats) - _dominance(o_stats)) + sos * 18.0
    dom_share = max(5.0, min(95.0, 50.0 + dom_diff * 1.4))

    have_rank = p_rank and o_rank and p_rank > 0 and o_rank > 0
    components = []

    if have_rank:
        rank_diff = math.log10(o_rank) - math.log10(p_rank)
        rank_share = max(5.0, min(95.0, 50.0 + rank_diff * 18.0))
        components.append((rank_share, 0.40))
        components.append((wr_share,   0.25))
        components.append((dom_share,  0.25))
    else:
        # No ranking — split between win rate and serve/return dominance so a
        # single thin-sample surface win rate can't dominate the estimate.
        components.append((wr_share,   0.50))
        components.append((dom_share,  0.50))

    # ── Component 3: recent form (last-10 W/L list) ──
    def _form_pct(form):
        if not form:
            return None
        wins = sum(1 for m in form if (m.get("won") if isinstance(m, dict) else m))
        n = len(form)
        return (wins / n) * 100.0 if n > 0 else None

    p_form_pct = _form_pct(p_form)
    o_form_pct = _form_pct(o_form)
    if p_form_pct is not None and o_form_pct is not None:
        form_share = 50.0 + (p_form_pct - o_form_pct) * 0.6
        form_share = max(20.0, min(80.0, form_share))
        components.append((form_share, 0.10))

    total_w = sum(w for _, w in components)
    p_prob = sum(share * w for share, w in components) / total_w if total_w > 0 else 50.0
    p_prob = max(5.0, min(95.0, p_prob))

    # ── Surface-affinity differential ────────────────────────────────────────
    # Everything above measures LEVEL. This asks who the surface favours, and
    # only ever makes the matchup TIGHTER: when the underdog is on their best
    # surface against a favourite on their worst, the level gap overstates the
    # real gap. The favourite always stays the favourite (SURFACE_AFFINITY_MIN_KEEP),
    # and an affinity edge for the FAVOURITE is deliberately ignored — widening a
    # gap on this signal would compound the level estimate rather than correct it.
    p_aff, o_aff = surface_affinity(p_stats), surface_affinity(o_stats)
    aff_gap = shift = 0.0
    if p_aff is not None and o_aff is not None:
        aff_gap = p_aff - o_aff
        gap0 = abs(p_prob - (100.0 - p_prob))
        # The underdog is whoever sits below 50. Only narrow when the affinity
        # gap points THEIR way.
        under_is_p = p_prob < 50.0
        under_aff_gap = aff_gap if under_is_p else -aff_gap
        if under_aff_gap >= SURFACE_AFFINITY_MIN_GAP and gap0 > SURFACE_AFFINITY_MIN_KEEP:
            shift = min(SURFACE_AFFINITY_MAX_SHIFT, under_aff_gap * SURFACE_AFFINITY_K)
            shift = min(shift, gap0 - SURFACE_AFFINITY_MIN_KEEP)  # keep the favourite ahead
            # Narrowing the GAP by `shift` moves each side by half of it.
            p_prob += (shift / 2.0) if under_is_p else -(shift / 2.0)
            p_prob = max(5.0, min(95.0, p_prob))

    o_prob = 100.0 - p_prob
    gap = abs(p_prob - o_prob)
    if detail is not None:
        detail.update({"p_affinity": p_aff, "o_affinity": o_aff,
                       "affinity_gap": aff_gap, "affinity_shift": shift})
    # OLD-vs-NEW comparison (temporary — remove after 2026-07-22). The held-out
    # baseline replaced the diluted overall_* one; logging both for a week shows
    # how often the correction changes the picture, and whether the 3.0 trigger
    # threshold still makes sense once affinities are measured honestly rather
    # than shrunk toward zero by their own surface.
    p_old, o_old = surface_affinity(p_stats, held_out=False), surface_affinity(o_stats, held_out=False)
    _fmt = lambda v: ("%+.1f" % v) if isinstance(v, (int, float)) else "n/a"
    _gap_old = (p_old - o_old) if (p_old is not None and o_old is not None) else None
    logger.info(
        "SURFACE_AFFINITY | NEW(held-out) p=%s o=%s gap=%s | OLD(diluted) p=%s o=%s "
        "gap=%s | fires=%s shift=%.1fpp -> win_prob %.1f/%.1f (gap %.1f)%s",
        _fmt(p_aff), _fmt(o_aff), _fmt(aff_gap if (p_aff is not None and o_aff is not None) else None),
        _fmt(p_old), _fmt(o_old), _fmt(_gap_old),
        bool(shift), shift, p_prob, o_prob, gap,
        "" if shift else " — no shift (affinity unmeasurable, gap favours the "
                         "favourite, gap below the %.0f threshold, or match already even)"
                         % SURFACE_AFFINITY_MIN_GAP,
    )
    return p_prob, o_prob, gap


def _expected_sets_from_gap(win_prob_gap: float, is_bo5: bool) -> tuple:
    """
    Map a win-probability gap to (expected_sets, competitiveness_label).
    """
    # ── FORMAT CEILINGS ARE MATHEMATICAL, NOT TUNABLE ────────────────────────
    # BO3: exp_sets = 2 + P(3 sets), and P(3 sets) = 2q(1-q) which MAXES at 0.5
    #      (q = per-set win prob = 0.5). So exp_sets <= 2.5. ALWAYS.
    # BO5: at q=0.5, P(3-0)=0.250 -> 3 sets, P(3-1)=0.375 -> 4, P(3-2)=0.375 -> 5,
    #      giving E[sets] = 4.125. So exp_sets <= 4.125. ALWAYS.
    #
    # The even-matchup values were 2.6 and 4.4 — both ABOVE their format's
    # mathematical ceiling, i.e. claiming more sets than the format can produce
    # even between two coin-flip players. 2.6 implies P(3 sets)=60%; the maximum
    # is 50% and the measured rate across 409 WTA matches is 32.8%.
    #
    # Surfaced by a 24.1 total-games projection: 9.25 gps x 2.60 = 24.05. The real
    # expectation for an even WTA match is 0.5*18.0 + 0.5*28.25 = 23.1 — which is
    # exactly 9.25 x 2.50. The error predates the games_per_set fit; raising gps
    # to its correct level simply made it visible.
    if is_bo5:
        if win_prob_gap > 40:
            return 3.3, "Heavy favorite"
        if win_prob_gap > 25:
            return 3.7, "Clear favorite"
        if win_prob_gap > 10:
            return 4.0, "Slight favorite"
        return 4.1, "Even matchup"        # was 4.4 — above the 4.125 ceiling
    else:
        if win_prob_gap > 40:
            return 2.1, "Heavy favorite"
        if win_prob_gap > 25:
            return 2.3, "Clear favorite"
        if win_prob_gap > 10:
            return 2.45, "Slight favorite"
        return 2.5, "Even matchup"        # was 2.6 — above the 2.5 ceiling


def _per_set_scale(tour: str, expected_sets: float) -> float:
    """
    Conversion factor that turns a per-match stat (from historical Sofascore
    data, which mixes BO3 and BO5 matches) into the projected per-match stat
    for *this* match given its expected length.

        per_set      = per_match_stat / avg_historical_sets[tour]
        projected    = per_set * expected_sets
                     = per_match_stat * (expected_sets / avg_historical_sets[tour])
    """
    avg = _AVG_HISTORICAL_SETS.get(tour, 2.45)
    if avg <= 0:
        return 1.0
    return expected_sets / avg


def _expected_sets(tour: str, court: str, p1_wr: float = 50.0, p2_wr: float = 50.0,
                   p1_stats: dict = None, p2_stats: dict = None,
                   p1_rank: int = None, p2_rank: int = None,
                   p1_form: list = None, p2_form: list = None) -> float:
    """
    Backwards-compatible wrapper. Returns just the expected sets value so
    existing callers (notably project_total_games) keep working. When stats
    dicts are provided the win-prob estimator is used; otherwise we fall back
    to comparing raw win rates directly.
    """
    is_bo5 = _is_bo5_match(tour, court)

    if p1_stats is not None and p2_stats is not None:
        _, _, gap = _estimate_win_prob(
            p1_stats, p2_stats, p1_rank, p2_rank, p1_form, p2_form,
        )
    else:
        # Fallback: treat win-rate share as a proxy for win prob
        total = p1_wr + p2_wr
        share = (p1_wr / total * 100.0) if total > 0 else 50.0
        gap = abs(share - (100.0 - share))

    exp_sets, _ = _expected_sets_from_gap(gap, is_bo5)
    return exp_sets


# ══════════════════════════════════════════════════════════════════════════════
# PTGW SCENARIO-MIXTURE MODEL  (FREEZE exception — see FREEZE_LOG.md)
# ──────────────────────────────────────────────────────────────────────────────
# The old PTGW chain projected a single MEAN and graded it vs the line with EVR.
# PTGW is BIMODAL — a straight-set loss lands ~6-9 games, ANY other outcome lands
# ~12-17 — so the mean sits in a valley the distribution rarely occupies, and a
# "fat edge on the mean" is a disguised moneyline bet. We now model the four match
# scenarios explicitly and compute P(over the line) as a probability mixture.
#
# Constants below are EMPIRICAL, fit from 2,028 real Sofascore matches (ATP n=995
# BO3, WTA n=941 BO3) — the same per-match source the games_per_set fit used, and
# the fit method is recorded in scratchpad/fit_ptgw_scenarios.py. Sackmann (the
# spec's stated source) is dead (repos 404 / loader disabled) so this is the
# forced substitute, approved by Shawn. Each entry: per-tour BO3 3-set base rates
# and per-scenario (player games-won) mean & sd.
#   S1 win-in-straights · S2 win-in-decider · S3 lose-in-decider · S4 lose-straights
_PTGW_SCEN_FIT = {
    "ATP": {"p3_win": 0.429, "p3_lose": 0.379,
            "scen": {"S1": (12.36, 1.03), "S2": (16.88, 2.10),
                     "S3": (13.24, 2.80), "S4": (7.37, 2.24)}},
    "WTA": {"p3_win": 0.316, "p3_lose": 0.393,
            "scen": {"S1": (12.28, 0.69), "S2": (15.85, 1.71),
                     "S3": (13.01, 2.31), "S4": (6.29, 2.26)}},
}
# BO5 SANE FALLBACK (spec-sanctioned): tour-level slates are ~all BO3, and the GS
# BO5 sample (ATP n=92, WTA n=0) is too thin to fit. Winner minimum is 18 games
# (3 sets × 6), not 12 — the whole distribution shifts up. Values are the BO3
# shape scaled to the BO5 set structure; flagged as fallback, not fit.
_PTGW_SCEN_BO5 = {
    "p3_win": 0.55, "p3_lose": 0.45,   # "3 sets" here means "went past the minimum"
    "scen": {"S1": (18.6, 1.4), "S2": (26.0, 3.2),
             "S3": (20.5, 3.6), "S4": (11.2, 3.0)},
}
# Win-prob modulation of the 3-set split: a more dominant favourite wins in
# straights more often (lower P(3|win)) and, on the rare loss, loses closer
# (higher P(3|lose)). Light linear overlay on the empirical base, clamped. This is
# the only MODELLED (non-fit) layer; the base rates and games distributions are data.
_PTGW_GAP_K = 0.60
_PTGW_P3_MIN, _PTGW_P3_MAX = 0.12, 0.62


def _norm_sf(x, mu, sd):
    """P(value > x) for a Normal(mu, sd), via the erf survival function. Games are
    integers and the line is always X.5, so no continuity correction is needed."""
    if sd is None or sd <= 0:
        return 1.0 if mu > x else 0.0
    return 0.5 * math.erfc((x - mu) / (sd * _SQRT2))


_SQRT2 = 2.0 ** 0.5


def ptgw_scenario_mixture(p_sel, prop_line, tour="ATP", match_format="best_of_3"):
    """Return the PTGW scenario mixture for the SELECTED player.

    p_sel      the selected player's match-win probability (0-1)
    prop_line  the PTGW line (e.g. 11.5)
    Returns dict: p_over, mixture_mean, scenario probabilities, and the per-scenario
    contribution to P(over) — everything the confidence step and trace need.
    """
    p_sel = max(0.02, min(0.98, float(p_sel)))
    is_bo5 = match_format == "best_of_5"
    fit = _PTGW_SCEN_BO5 if is_bo5 else _PTGW_SCEN_FIT.get(tour, _PTGW_SCEN_FIT["ATP"])
    gap = p_sel - 0.5
    p3_win = max(_PTGW_P3_MIN, min(_PTGW_P3_MAX, fit["p3_win"] - _PTGW_GAP_K * gap))
    p3_lose = max(_PTGW_P3_MIN, min(_PTGW_P3_MAX, fit["p3_lose"] + _PTGW_GAP_K * gap))

    # Scenario probabilities from the selected player's perspective.
    p = {
        "S1": p_sel * (1.0 - p3_win),        # win in straights
        "S2": p_sel * p3_win,                # win in a decider
        "S3": (1.0 - p_sel) * p3_lose,       # lose in a decider
        "S4": (1.0 - p_sel) * (1.0 - p3_lose),  # lose in straights
    }
    scen = fit["scen"]
    # HARD STRUCTURAL FLOOR: a match WINNER always wins at least (sets-to-win × 6)
    # games — 18 in BO5, 12 in BO3 — because every set won needs ≥6 games. So for
    # the two win scenarios, P(games > line) is EXACTLY 1.0 whenever the line sits
    # below that floor. This is the identity the audit rests on: P(over 11.5) can
    # never be less than P(win). The normal tail would wrongly shave it to ~0.87;
    # this enforces the physics instead.
    winner_floor = 18 if is_bo5 else 12
    p_over = 0.0
    mix_mean = 0.0
    contrib = {}
    for s in ("S1", "S2", "S3", "S4"):
        mu, sd = scen[s]
        if s in ("S1", "S2") and prop_line < winner_floor:
            po_s = 1.0                       # winner always clears a sub-floor line
        else:
            po_s = _norm_sf(prop_line, mu, sd)   # P(games > line | scenario)
        contrib[s] = round(p[s] * po_s, 4)
        p_over += p[s] * po_s
        mix_mean += p[s] * mu
    p_over = max(0.0, min(1.0, p_over))
    return {
        "p_over": p_over,
        "p_under": 1.0 - p_over,
        "mixture_mean": mix_mean,
        "scenario_probs": {k: round(v, 4) for k, v in p.items()},
        "over_contrib": contrib,
        "p3_win": round(p3_win, 3),
        "p3_lose": round(p3_lose, 3),
        "p_win_match": round(p_sel, 4),
    }


# ══════════════════════════════════════════════════════════════════════════════
# FANTASY SCORE  (PrizePicks tennis)  — scenario-mixture, built on the PTGW machinery
# ──────────────────────────────────────────────────────────────────────────────
# FS = 10 (match played)
#    + 1·games_won − 1·games_lost
#    + 3·sets_won  − 3·sets_lost
#    + 0.5·aces    − 0.5·double_faults
# FS is MORE bimodal than PTGW (a straight-set win is ~20+, a straight-set loss can
# go negative — the match outcome swings 15-20 points), so a point estimate would
# repeat the exact PTGW error. We reuse the four-scenario machinery:
#   • sets won/lost are EXACT per scenario (definitional).
#   • games won come from the same per-tour/format Sofascore fit (_PTGW_SCEN_FIT);
#     games LOST need no separate fit — by match symmetry the player's games-lost
#     in scenario S equals the OPPONENT's games-won in the mirror scenario (a
#     straight-set WIN pairs with the opponent's straight-set LOSS, etc.), so
#     games_lost | S = games_won | mirror(S). Mirror: S1<->S4, S2<->S3.
#   • aces / DF per scenario scale the match ace/DF projection by that scenario's
#     set count relative to the match's expected sets (reuse, don't rebuild).
#   • FS distribution per scenario = 10 + games_margin + 3·set_margin
#       + 0.5·aces − 0.5·DF, variance = games-margin variance + 0.25·(ace+DF var).
#     INDEPENDENCE of games / aces / DF is assumed (acceptable — they are weakly
#     correlated and the mixture's between-scenario spread dominates).
#   • P(over) = Σ P(scenario)·P(FS > line | scenario); confidence maps from P(over)
#     exactly like the rebuilt PTGW.
_FS_MIRROR = {"S1": "S4", "S2": "S3", "S3": "S2", "S4": "S1"}
# Set margin (won − lost) per scenario. BO3: 2-0/2-1/1-2/0-2. BO5: 3-0/3-1.5/
# 1.5-3/0-3 (win/lose-in-4-or-5 averaged).
_FS_SET_MARGIN = {
    "best_of_3": {"S1": 2.0, "S2": 1.0, "S3": -1.0, "S4": -2.0},
    "best_of_5": {"S1": 3.0, "S2": 1.5, "S3": -1.5, "S4": -3.0},
}
# Sets played per scenario — used to scale the match ace/DF projection into a
# per-scenario expectation (more sets → more serves → more aces/DF).
_FS_SCEN_SETS = {
    "best_of_3": {"S1": 2.0, "S2": 3.0, "S3": 3.0, "S4": 2.0},
    "best_of_5": {"S1": 3.0, "S2": 4.5, "S3": 4.5, "S4": 3.0},
}
FS_CONF_CEILING = 80   # composite high-variance prop — ceiling until the ledger says more
FS_DIVERGENCE_CONF_CAP = 70   # cap when model & book disagree on the OUTCOME (point 4)

# Scenario labels, ordered HIGH FS -> LOW FS. FS rises monotonically with outcome
# quality (comfortable win > tight win > three-set win > three-set loss > straight
# loss), so a line partitions the ordered bands at one boundary.
_FS_ORDER = ["S1", "S2", "S3", "S4"]
_FS_LABEL = {"S1": "a straight-sets win", "S2": "a three-set win",
             "S3": "a three-set loss", "S4": "a straight-sets loss"}


def _fs_band_of(value, breakdown):
    """The scenario band whose mean is nearest to `value` (the outcome the value
    implies)."""
    return min(_FS_ORDER, key=lambda s: abs(value - breakdown[s]["fs_mu"]))


def _fs_describe(lean, line, projection, breakdown, who):
    """Derive the implied claim + line-position classification from WHICH scenarios
    actually clear the line — never a static over/under->win/lose mapping. Returns
    (claim, line_position, proj_band, line_band, divergent)."""
    # Classify each scenario vs the line by its own P(FS > line). A scenario the
    # line cuts THROUGH (meaningful mass on both sides) is a "split" — the upper
    # (comfortable) part clears the line, the lower part doesn't.
    over, under, split = [], [], []
    for s in _FS_ORDER:
        po = breakdown[s]["p_over"]
        (over if po >= 0.7 else under if po <= 0.3 else split).append(s)

    def _join(items):
        items = list(items)
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        return ", ".join(items[:-1]) + " or " + items[-1]

    line_band = _fs_band_of(line, breakdown)
    proj_band = _fs_band_of(projection, breakdown)
    line_position = "book line sits in the %s band" % _FS_LABEL[line_band]

    if lean == "OVER":
        # The OVER wins on the scenarios above the line: the `over` set, plus the
        # UPPER (comfortable) part of any straddled band.
        need = [_FS_LABEL[s] for s in over] + ["a comfortable %s" % _FS_LABEL[s][2:] for s in split]
        claim = ("%s O%.1f — needs %s" % (who, line, _join(need))) if need else \
                ("%s O%.1f — needs the very top outcome" % (who, line))
    else:
        # The UNDER wins UNLESS one of the scenarios above the line lands (the
        # `over` set + the comfortable part of any straddle). If everything is
        # below the line, it hits on any outcome.
        beats = [_FS_LABEL[s] for s in over] + ["a comfortable %s" % _FS_LABEL[s][2:] for s in split]
        claim = ("%s U%.1f — hits unless %s" % (who, line, _join(beats))) if beats else \
                ("%s U%.1f — hits on essentially any outcome" % (who, line))

    # Divergence: model and book point at DIFFERENT outcome bands (not just a
    # margin disagreement). E.g. proj in the three-set band vs a line in the
    # straight-sets-win band.
    divergent = proj_band != line_band
    return claim, line_position, proj_band, line_band, divergent


def fantasy_score_mixture(p_sel, ace_proj, df_proj, expected_sets, prop_line,
                          tour="ATP", match_format="best_of_3"):
    """Scenario mixture for a player's Fantasy Score. Returns P(over line), the
    mixture mean, and the scenario breakdown. p_sel = the player's match-win
    probability (0-1); ace_proj / df_proj = the player's MATCH ace / double-fault
    projections (scaled per scenario by set count)."""
    p_sel = max(0.02, min(0.98, float(p_sel)))
    is_bo5 = match_format == "best_of_5"
    fit = _PTGW_SCEN_BO5 if is_bo5 else _PTGW_SCEN_FIT.get(tour, _PTGW_SCEN_FIT["ATP"])
    scen = fit["scen"]
    set_margin = _FS_SET_MARGIN["best_of_5" if is_bo5 else "best_of_3"]
    scen_sets = _FS_SCEN_SETS["best_of_5" if is_bo5 else "best_of_3"]
    need = 3 if is_bo5 else 2
    baseline_sets = max(_safe(expected_sets, need + 0.3), need + 0.15)

    # Scenario probabilities — identical construction to the PTGW mixture.
    gap = p_sel - 0.5
    p3_win = max(_PTGW_P3_MIN, min(_PTGW_P3_MAX, fit["p3_win"] - _PTGW_GAP_K * gap))
    p3_lose = max(_PTGW_P3_MIN, min(_PTGW_P3_MAX, fit["p3_lose"] + _PTGW_GAP_K * gap))
    p = {"S1": p_sel * (1.0 - p3_win), "S2": p_sel * p3_win,
         "S3": (1.0 - p_sel) * p3_lose, "S4": (1.0 - p_sel) * (1.0 - p3_lose)}

    ace_proj = max(0.0, _safe(ace_proj, 0.0))
    df_proj = max(0.0, _safe(df_proj, 0.0))
    p_over = 0.0
    fs_mean = 0.0
    breakdown = {}
    for s in ("S1", "S2", "S3", "S4"):
        gw_mu, gw_sd = scen[s]
        gl_mu, gl_sd = scen[_FS_MIRROR[s]]        # games lost = opp games won, mirror scenario
        games_margin_mu = gw_mu - gl_mu
        games_margin_var = gw_sd ** 2 + gl_sd ** 2
        scale = (scen_sets[s] / baseline_sets) if baseline_sets > 0 else 1.0
        a_mu, d_mu = ace_proj * scale, df_proj * scale
        # Poisson-style variance approximation for counts (var ≈ mean, floored).
        a_var, d_var = max(a_mu, 0.5), max(d_mu, 0.5)
        fs_mu = (10.0 + games_margin_mu + 3.0 * set_margin[s] + 0.5 * (a_mu - d_mu))
        fs_var = games_margin_var + 0.25 * (a_var + d_var)
        fs_sd = fs_var ** 0.5
        po_s = _norm_sf(prop_line, fs_mu, fs_sd)
        p_over += p[s] * po_s
        fs_mean += p[s] * fs_mu
        breakdown[s] = {"p": round(p[s], 4), "fs_mu": round(fs_mu, 2),
                        "fs_sd": round(fs_sd, 2), "p_over": round(po_s, 4)}
    p_over = max(0.0, min(1.0, p_over))
    return {
        "p_over": p_over, "p_under": 1.0 - p_over,
        "mixture_mean": fs_mean,
        "p_win_match": round(p_sel, 4),
        "scenario_probs": {k: round(v, 4) for k, v in p.items()},
        "scenario_breakdown": breakdown,
    }


def project_fantasy_score(p_sel, ace_proj, df_proj, expected_sets, prop_line,
                          tour="ATP", match_format="best_of_3", player_name="",
                          trace: list = None) -> dict:
    """Project a player's Fantasy Score via the scenario mixture. Returns the
    displayed projection (mixture mean) plus p_over / p_under and the implied
    match claim, mirroring the PTGW contract so main.py can grade it identically."""
    mix = fantasy_score_mixture(p_sel, ace_proj, df_proj, expected_sets, prop_line,
                                tour=tour, match_format=match_format)
    projection = mix["mixture_mean"]
    who = player_name or "player"
    lean = "OVER" if mix["p_over"] >= 0.5 else "UNDER"
    line = prop_line or 0
    # Claim + line-position are DERIVED from which scenarios clear the line — never
    # a static over/under -> win/lose mapping (that mapping is invalid for FS: the
    # line can sit INSIDE the win bands).
    claim, line_position, proj_band, line_band, divergent = _fs_describe(
        lean, line, projection, mix["scenario_breakdown"], who)
    _trace(trace, "FS_scenario_mixture",
           {"line": prop_line, "p_win_match": mix["p_win_match"],
            "ace_proj": round(ace_proj, 2) if isinstance(ace_proj, (int, float)) else ace_proj,
            "df_proj": round(df_proj, 2) if isinstance(df_proj, (int, float)) else df_proj,
            "scenario_probs": mix["scenario_probs"],
            "scenario_breakdown": mix["scenario_breakdown"],
            "proj_band": proj_band, "line_band": line_band, "divergent": divergent},
           round(mix["p_over"], 4), round(projection, 2),
           "FS = 10 + games_margin + 3·set_margin + 0.5(aces−DF); P(over %.1f)=%.3f "
           "= Σ P(scenario)·P(FS>line|scenario); %s; %s"
           % (prop_line or 0, mix["p_over"], line_position,
              "MODEL/BOOK OUTCOME DISAGREEMENT (proj band %s vs line band %s)"
              % (proj_band, line_band) if divergent else "proj & line agree on the outcome band"))
    _trace(trace, "projector_output", {"chain_result": round(projection, 3)},
           projection, round(projection, 1),
           "END OF THE PROJECTOR — main.py maps confidence from p_over")
    return {
        "projection": round(projection, 1),
        "fs_p_over": round(mix["p_over"], 4),
        "fs_p_under": round(mix["p_under"], 4),
        "fs_p_win_match": mix["p_win_match"],
        "fs_scenario_probs": mix["scenario_probs"],
        "fs_scenario_breakdown": mix["scenario_breakdown"],
        "fs_mixture_mean": round(mix["mixture_mean"], 2),
        "fs_implied_claim": claim,
        "fs_line_position": line_position,
        "fs_proj_band": proj_band,
        "fs_line_band": line_band,
        "fs_divergent": divergent,
        "lean": "",
    }


def project_player_games_won(
    player_stats: dict,
    opponent_stats: dict,
    surface: str,
    cpr: float,
    games_combined: float,
    bp_won: float,
    p1_win_prob: float,
    p2_win_prob: float,
    expected_sets: float,
    tour: str = "ATP",
    match_format: str = "best_of_3",
    prop_line: float = None,
    trace: list = None,
) -> dict:
    """Project how many INDIVIDUAL games the SELECTED player wins in the match
    (distinct from the combined Total Games prop).

    Built from the match's SET STRUCTURE so the two players' projections are
    physically self-consistent — they imply the SAME number of sets and always
    reconcile to the combined Total Games (player + opponent == combined).

    The key constraint a naive hold/break split misses: the match winner ALWAYS
    wins at least (sets-to-win × 6) games — 18 in a best-of-5, 12 in a best-of-3
    — because every set won needs ≥6 games. So a favourite expected to win must
    clear that floor; the loser gets the rest. A simple per-game share put the
    favourite BELOW the floor (e.g. 17 in a BO5) while giving the loser too many
    (14) — numbers that imply different set counts and can't both be true.

    Method:
      • avg games/set      = combined / expected_sets
      • set winner/loser   = ~6.x and the remainder (a 6-4 / 7-5 style set)
      • favourite's sets   = (sets-to-win) when they win the match, a fraction of
                             (sets-to-win − 1) when they lose it (win-prob driven)
      • favourite_games    = (sets they win)·winner_gps + (sets they lose)·loser_gps
      • opponent_games     = combined − favourite_games   (exact reconciliation)
    Surface/court speed and win probability are already embedded in the combined
    total, the expected sets and the win prob. Returns the projection plus a
    held/broken breakdown (kept proportional to the players' hold/break profile)
    and the supporting hold/break rates for display.
    """
    games_combined = _safe(games_combined, 0.0)
    service_games = games_combined / 2.0                # player serves ~half the games

    # Surface GAME-hold rates: convert service-POINTS-won (the proxy) into the
    # probability of holding a service GAME (iid-point model). e.g. 66% of points
    # won ≈ 85% of games held — used for the held/broken display breakdown.
    game_hold = max(0.40, min(0.97, _game_win_prob(_hold_rate_proxy(player_stats))))
    opp_hold_rate = max(0.40, min(0.97, _game_win_prob(_hold_rate_proxy(opponent_stats or {}))))

    # ── Set-structure split ──────────────────────────────────────────────────
    need = 3 if match_format == "best_of_5" else 2      # sets to win the match
    es = max(_safe(expected_sets, need + 0.3), need + 0.15)
    avg_gps = (games_combined / es) if es > 0 else 9.5  # games per set
    winner_gps = min(7.0, max(6.0, 0.55 * avg_gps + 0.80))   # set winner's games
    loser_gps = max(2.0, avg_gps - winner_gps)               # set loser's games

    p_sel = _safe(p1_win_prob, 50.0) / 100.0
    p_sel = max(0.02, min(0.98, p_sel))
    p_fav = max(p_sel, 1.0 - p_sel)                     # the favourite's win prob
    sel_is_fav = p_sel >= 0.5

    # Sets the FAVOURITE wins over the whole match: exactly `need` when they win
    # it (prob p_fav); a fraction of (need-1) on the occasions they lose it.
    fav_set_wins = p_fav * need + (1.0 - p_fav) * (need - 1) * 0.55
    fav_set_losses = max(0.0, es - fav_set_wins)
    fav_games = fav_set_wins * winner_gps + fav_set_losses * loser_gps
    fav_games = max(0.0, min(fav_games, games_combined))    # safety bound
    opp_games = games_combined - fav_games

    projection = fav_games if sel_is_fav else opp_games
    _pre_floor = projection
    projection = max(4.5, projection)                   # floor — even a swept loser wins a few

    # ── Scenario-mixture P(over) — the STRUCTURAL replacement for mean-vs-line ──
    # The point estimate above (`projection`) is retained only as a display value
    # and for backward compatibility. Grading no longer compares it to the line;
    # instead we compute P(games > line) from the four-scenario mixture, which is
    # the correct instrument for a bimodal distribution. Confidence (confidence.py)
    # and the lean (main.py) now read `p_over` from here — NOT projection vs line.
    mix = None
    if isinstance(prop_line, (int, float)) and prop_line > 0:
        mix = ptgw_scenario_mixture(p_sel, prop_line, tour=tour, match_format=match_format)
        # The mixture mean is the physically-honest central tendency (it integrates
        # the same set structure); use it as the displayed projection so the number
        # the user sees is consistent with the probability that grades it.
        projection = max(4.5, mix["mixture_mean"])
        _trace(trace, "PTGW_scenario_mixture",
               {"line": prop_line,
                "p_win_match": mix["p_win_match"],
                "scenario_probs": mix["scenario_probs"],
                "p3_win": mix["p3_win"], "p3_lose": mix["p3_lose"],
                "over_contrib": mix["over_contrib"]},
               round(mix["p_over"], 4), round(projection, 2),
               "P(over %.1f) = Σ P(scenario)·P(games>line|scenario) = %.3f; "
               "mixture mean %.2f is the displayed projection (NOT graded vs line)"
               % (prop_line, mix["p_over"], mix["mixture_mean"]))

    # ── Component trace ──────────────────────────────────────────────────────
    # PTGW had NO trace coverage: its projection comes from here, but only aces
    # and break points were instrumented. The FINAL assertion in main.py caught it
    # honestly — debug=true 500'd on this prop because the last traced value never
    # matched the served projection. That is the guard working; this is the fix.
    # PTGW consumes the Total Games projection's win prob / expected sets, so those
    # inputs are shown rather than recomputed.
    _trace(trace, "PTGW_inputs",
           {"games_combined": round(games_combined, 2),
            "expected_sets": round(es, 2),
            "player_win_prob": round(p_sel * 100, 1),
            "player_is_favourite": sel_is_fav,
            "bp_won_component": bp_won},
           round(games_combined, 2), round(games_combined, 2),
           "from the Total Games projection — PTGW splits that combined total "
           "between the two players; it does not re-derive it")
    _trace(trace, "PTGW_games_per_set",
           {"avg_games_per_set": round(avg_gps, 2),
            "set_winner_games": round(winner_gps, 2),
            "set_loser_games": round(loser_gps, 2)},
           round(avg_gps, 2), round(avg_gps, 2),
           "combined / expected_sets, split into a winner's and loser's share")
    _trace(trace, "PTGW_set_split",
           {"favourite_set_wins": round(fav_set_wins, 2),
            "favourite_set_losses": round(fav_set_losses, 2),
            "favourite_games": round(fav_games, 2),
            "opponent_games": round(opp_games, 2)},
           round(fav_games, 2), round(_pre_floor, 2),
           "favourite_games + opponent_games == games_combined exactly; the "
           "SELECTED player takes whichever side they are on")
    _trace(trace, "PTGW_floor",
           {"pre_floor": round(_pre_floor, 2)},
           4.5, round(projection, 2),
           "floor 4.5 — even a swept loser wins a few games%s"
           % (" [FLOOR BIT]" if projection > _pre_floor + 1e-9 else ""))
    _trace(trace, "projector_output", {"chain_result": round(projection, 3)},
           projection, round(projection, 1),
           "END OF THE PROJECTOR — NOT the final number; main.py may still apply "
           "post-projector modifiers (see the steps below and the real FINAL)")

    # Held / broken breakdown for display — split the projection in proportion to
    # the player's actual hold vs break tendency, so it sums to the projection
    # and never goes negative.
    raw_holds = service_games * game_hold
    raw_breaks = service_games * (1.0 - opp_hold_rate)
    raw_total = raw_holds + raw_breaks
    if raw_total > 0:
        games_held = projection * (raw_holds / raw_total)
        games_broken = projection - games_held
    else:
        games_held = projection
        games_broken = 0.0

    hold_rate = game_hold
    break_rate = (1.0 - opp_hold_rate) * 100.0

    logger.info(
        "PLAYER_GAMES_WON | combined=%.1f es=%.2f gps=%.1f(W%.1f/L%.1f) p_sel=%.0f%% "
        "fav_sets=%.2f sel_is_fav=%s -> proj=%.1f | held=%.1f broken=%.1f fmt=%s",
        games_combined, es, avg_gps, winner_gps, loser_gps, p_sel * 100,
        fav_set_wins, sel_is_fav, projection, games_held, games_broken, match_format,
    )

    return {
        "projection":     round(projection, 1),
        "games_held":     round(games_held, 1),
        "games_broken":   round(games_broken, 1),
        "hold_rate":      round(hold_rate * 100, 1),
        "opp_hold_rate":  round(opp_hold_rate * 100, 1),
        "break_rate":     round(break_rate, 1),
        "games_combined": round(games_combined, 1),
        "expected_sets":  expected_sets,
        "p1_win_prob":    p1_win_prob,
        "p2_win_prob":    p2_win_prob,
        "is_bo5":         match_format == "best_of_5",
        "lean":           "",
        # Scenario-mixture outputs (None when no line was supplied — e.g. a bare
        # projection request). p_over is the sole grading input for PTGW.
        "ptgw_p_over":       (round(mix["p_over"], 4) if mix else None),
        "ptgw_p_under":      (round(mix["p_under"], 4) if mix else None),
        "ptgw_p_win_match":  (mix["p_win_match"] if mix else None),
        "ptgw_scenario_probs": (mix["scenario_probs"] if mix else None),
        "ptgw_mixture_mean": (round(mix["mixture_mean"], 2) if mix else None),
    }


def project_total_games(
    player_stats: dict,
    opponent_stats: dict,
    surface: str,
    h2h_games_avg: float = None,
    h2h_games_n: int = 0,         # H2H meetings with a total-games count
    tour: str = "ATP",
    court: str = "",
    player_ta: dict = None,
    opponent_ta: dict = None,
    match_format: str = "best_of_3",
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

    # ── Games per set from combined hold rate — PER-TOUR EMPIRICAL FIT ───────
    games_per_set = _games_per_set(combined_hold, tour)

    # ── Expected sets — driven by win-probability gap, not flat tour avg ──
    p1_wr = _safe(player_stats.get("win_rate"), 50.0)
    p2_wr = _safe(opponent_stats.get("win_rate"), 50.0)
    # match_format is the source of truth (set by the caller; respects the ATP
    # Grand Slam Qualifying toggle), NOT the court alone.
    is_bo5 = (match_format == "best_of_5")
    _aff_detail = {}
    p_prob, o_prob, win_prob_gap = _estimate_win_prob(
        player_stats, opponent_stats,
        p_rank=player_stats.get("rank") or player_stats.get("currentRank"),
        o_rank=opponent_stats.get("rank") or opponent_stats.get("currentRank"),
        p_form=player_stats.get("form") or player_stats.get("recent_form"),
        o_form=opponent_stats.get("form") or opponent_stats.get("recent_form"),
        detail=_aff_detail,
    )
    exp_sets, comp_label = _expected_sets_from_gap(win_prob_gap, is_bo5)
    avg_hist_sets = _AVG_HISTORICAL_SETS.get(tour, 2.45)

    logger.info(
        "GAMES_EXPSETS | p1=%s p2=%s | bo5=%s | p_wr=%.1f o_wr=%.1f | "
        "win_prob_gap=%.1fpp | exp_sets=%.2f (%s) | gps=%.2f",
        player_stats.get("player_name", "?"), opponent_stats.get("player_name", "?"),
        is_bo5, p1_wr, p2_wr, win_prob_gap, exp_sets, comp_label, games_per_set,
    )

    # ── Raw total games ───────────────────────────────────────────────────────
    proj = games_per_set * exp_sets

    # ── H2H blend at 35% if available ────────────────────────────────────────
    # ── H2H blend — SAMPLE-GATED (see _h2h_weight) ───────────────────────────
    # The heaviest H2H weight in the codebase (35%) and it was ungated: a single
    # prior meeting could move a total-games projection by more than a third.
    # NOTE the gate reads games_n, not stat_n — total games is derived from the
    # SCORE, so a meeting needs no parsed statistics to inform it. This is a
    # genuinely different (and usually larger) sample than the ace/DF basis, so
    # gating it on stat_n would discard meetings that are perfectly good here.
    _h2h_w_tg = _h2h_weight(0.35, h2h_games_n)
    if h2h_games_avg is not None and h2h_games_avg > 0 and _h2h_w_tg > 0:
        proj = proj * (1.0 - _h2h_w_tg) + h2h_games_avg * _h2h_w_tg
        logger.info("TG_H2H | avg=%.1f | %d meetings with games -> weight %.0f%%",
                    h2h_games_avg, h2h_games_n, _h2h_w_tg * 100)
    elif h2h_games_avg is not None and h2h_games_avg > 0:
        logger.info("TG_H2H_SKIPPED | avg=%.1f but only %s meeting(s) with games "
                    "(<%d) — H2H contributes nothing",
                    h2h_games_avg, h2h_games_n, H2H_MIN_MEETINGS)

    # ── ST Pace Index surface adjustment for total games per set ─────────────
    # Thresholds updated for the ST Pace Index scale (not the old CPR scale):
    #   ST ≤ 28  (Very Slow / low-Slow clay)  → +0.4 gps (longer rallies, more deuce)
    #            Examples: Barcelona 27.2, Hamburg 28.4, generic clay 26
    #   ST ≥ 42  (Fast — US Open 42.8, top hard courts) → −0.3 gps
    #   All others (Average incl. Roland Garros 37.7, Wimbledon 36.1) → neutral
    from src.constants import COURT_CPR
    cpr = COURT_CPR.get(court, CPR_NEUTRAL)
    if cpr <= 28:
        gps_adj = 0.4
    elif cpr >= 42:
        gps_adj = -0.3
    else:
        gps_adj = 0.0
    proj += gps_adj * exp_sets

    env = detect_environment(player_stats, opponent_stats, surface=surface, tour=tour)

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
        "expected_sets":       round(exp_sets, 2),
        "combined_hold":       round(combined_hold, 1),
        "p1_srv":              round(p1_srv, 1),
        "p2_srv":              round(p2_srv, 1),
        "format":              f"Best of {'5' if is_bo5 else '3'}",
        "environment":         env,
        "cpr":                 cpr,
        "ta_used":             ta_used,
        "ta_surface_matches":  ta_surface_matches,
        # Expected-sets exposure
        "competitiveness":     comp_label,
        "win_prob_gap":        round(win_prob_gap, 1),
        "p1_win_prob":         round(p_prob, 1),
        "p2_win_prob":         round(o_prob, 1),
        "avg_historical_sets": round(avg_hist_sets, 2),
        "is_bo5":              is_bo5,
        # Surface-affinity differential — carried out so the caller can apply the
        # underdog games-won confidence penalty and display the scores. PTGW takes
        # its win prob / expected sets from THIS projection, so the affinity shift
        # already flows into the underdog's games-won number.
        "p_affinity":          _aff_detail.get("p_affinity"),
        "o_affinity":          _aff_detail.get("o_affinity"),
        "affinity_gap":        _aff_detail.get("affinity_gap"),
        "affinity_shift":      _aff_detail.get("affinity_shift"),
    }


# ---------------------------------------------------------------------------
# Break Points Won  — 8-component formula
# ---------------------------------------------------------------------------
def project_break_points(
    player_stats: dict,
    opponent_stats: dict,
    player_all_stats: dict = None,   # all-surface stats (sanity check + C2 baseline)
    opponent_all_stats: dict = None, # all-surface opponent stats (C2 opp baseline)
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
    bp_prop_mode: bool = False,                 # enhancements gated to the BP prop
    bp_generated_quality_adj: float = None,      # Part 2: quality-adjusted BP gen
    bp_generated_raw: float = None,              # raw BP gen per match (display/conf)
    bp_forward_server_factor: float = 1.0,       # Part 2: current-opp serve-quality
    trace: list = None,                          # component trace (admin diagnostic)
) -> dict:
    """
    8-component break points won projection:

      base = C1(opp_bp_faced_surface)
           × C2(returner_creation_mult)
           × C3(player_conv_rate_60/40)
           × C4(serve_quality_adj)
           × C5(player_specific_surface_adj)
           × C6(cpr_mod)

      proj = (base + C7_momentum_bonus) × C8(format_mult)

    C8 = 1.0 for ALL matches — Sofascore per-match bp_faced already embeds
    the BO5 effect for GS players.  Momentum is additive BEFORE C8.
    """
    ta_used = False
    ta_surface_matches = 0
    used_opp_tour_avg  = False

    is_bo5 = (match_format == "best_of_5" and tour == "ATP")

    player_name = player_stats.get("player_name", "?")
    opp_name    = opponent_stats.get("player_name", "?")

    _p_all = player_all_stats   or {}
    _o_all = opponent_all_stats or {}

    tour_avg_bp = _tour_avg(tour, surface)["bp_faced_per_match"]
    cpr = cpr_override if cpr_override is not None else CPR_NEUTRAL

    logger.info(
        "BP_START | player=%s | opp=%s | surface=%s | tour=%s | "
        "format=%s | is_bo5=%s | court=%s | cpr=%d",
        player_name, opp_name, surface, tour,
        match_format, is_bo5, court or "generic", cpr,
    )

    # ═════════════════════════════════════════════════════════════════════════
    # COMPONENT 1 — Opponent BP faced per match on the selected surface
    #
    # Use the raw surface stat.  Per-match average already reflects however
    # many sets those historical matches lasted — never multiply by sets.
    # BO3/BO5 scaling is handled by C8 (match format multiplier), not here.
    #
    # Double-fault exception: if opp DF > 4/match AND bp_faced < 5, the stat
    # likely understates vulnerability — add 0.4 to compensate.
    # ═════════════════════════════════════════════════════════════════════════
    raw_opp_bp_faced     = opponent_stats.get("bp_faced_count")
    overall_opp_bp_faced = opponent_stats.get("overall_bp_faced_count")
    opp_surf_sample      = (opponent_stats.get("surface_matches", 0)
                            or opponent_stats.get("matches_played", 0) or 0)
    min_credible_bp      = tour_avg_bp * 0.25

    if raw_opp_bp_faced is not None and raw_opp_bp_faced >= min_credible_bp:
        # Credibility-weight small surface samples toward tour average.
        # A player with 2-3 clay matches can have a wildly unrepresentative
        # bp_faced stat (e.g. 17/match from three bad outings) that inflates C1.
        # Shrink toward tour_avg_bp proportional to sample size.
        surf_n = opp_surf_sample or 0
        if surf_n >= 10:
            raw_weight = 1.00
        elif surf_n >= 5:
            raw_weight = 0.60   # was 0.75 — more shrinkage for thin samples
        elif surf_n >= 3:
            raw_weight = 0.40   # was 0.55
        else:
            raw_weight = 0.20   # was 0.35 — maximum shrinkage for ≤ 2 matches
        c1_opp_bp_faced = raw_weight * raw_opp_bp_faced + (1.0 - raw_weight) * tour_avg_bp
        c1_source       = f"surface_blended(n={surf_n},w={raw_weight:.0%})"
    elif overall_opp_bp_faced is not None and overall_opp_bp_faced >= min_credible_bp:
        c1_opp_bp_faced = overall_opp_bp_faced
        c1_source       = "overall_fallback"
    else:
        c1_opp_bp_faced = tour_avg_bp
        c1_source       = "tour_avg"
        used_opp_tour_avg = True

    # Double-fault exception
    opp_df         = _safe(opponent_stats.get("double_faults"), 0.0)
    df_bonus_added = 0.0
    if opp_df > 4.0 and c1_opp_bp_faced < 5.0:
        df_bonus_added   = 0.4
        c1_opp_bp_faced += df_bonus_added

    # ── Surface-aware floor on the opportunity pool (C1) ──────────────────────
    # C1 is the opponent's BP faced per match — the pool of break chances this
    # player can convert. On clay/hard a reading far below tour average is
    # usually a data artifact (stat-rich-only subset, per-set mislabel, tiny
    # sample) — we floor it. But on GRASS a low BP-faced count is REAL signal:
    # grass is serve-dominant and produces genuinely few break chances, so a
    # 3-4/match reading is accurate, not noise. Flooring it there manufactures
    # a fake OVER edge by inflating the opportunity pool. So the floor is now
    # surface-relative:
    #   Clay  → 0.55 × tour avg  (clay breaks most; low reading = likely noise)
    #   Hard  → 0.50 × tour avg
    #   Grass → 0.25 × tour avg  (low readings are legitimate — barely floors)
    #   None/challenger default → 0.50 (hard)
    _c1_floor_mult = {"Clay": 0.55, "Hard": 0.50, "Grass": 0.25}.get(surface, 0.50)
    c1_floor = tour_avg_bp * _c1_floor_mult
    c1_floored = False
    if c1_opp_bp_faced < c1_floor:
        c1_floored = True
        c1_opp_bp_faced = c1_floor
        c1_source = f"{c1_source}+floor({c1_floor:.1f}@{_c1_floor_mult:.2f})"

    # ── Part 2/4: opportunity pool reflects BOTH sides + forward server quality
    # (Break Points prop only — gated so Player Total Games Won is unchanged).
    # The opponent's BP-faced says how many chances they concede; the player's
    # quality-adjusted BP-generated says how many they create. Blending the two
    # makes a high-volume opportunity creator project higher than a low-sample
    # one even off the same conversion rate. The forward factor then nudges for
    # the CURRENT opponent's serve quality (strong server → fewer chances).
    c1_pre_blend = c1_opp_bp_faced
    if bp_prop_mode and bp_generated_quality_adj is not None and bp_generated_quality_adj > 0:
        c1_opp_bp_faced = (0.5 * c1_opp_bp_faced + 0.5 * bp_generated_quality_adj) \
            * bp_forward_server_factor
        c1_source = (f"{c1_source}+gen_qadj({bp_generated_quality_adj:.1f})"
                     f"xfwd({bp_forward_server_factor:.2f})")
    elif bp_prop_mode and bp_forward_server_factor != 1.0:
        c1_opp_bp_faced *= bp_forward_server_factor
        c1_source = f"{c1_source}+fwd({bp_forward_server_factor:.2f})"

    logger.info(
        "BP_C1 | opp=%s | surf_raw=%s | overall=%s | opp_surf_n=%d | "
        "source=%s | c1=%.2f | floored=%s | opp_df=%.1f | df_bonus=%.1f | tour_avg=%.2f",
        opp_name, raw_opp_bp_faced, overall_opp_bp_faced, opp_surf_sample,
        c1_source, c1_opp_bp_faced, c1_floored, opp_df, df_bonus_added, tour_avg_bp,
    )

    # ═════════════════════════════════════════════════════════════════════════
    # COMPONENT 2 — Returner creation multiplier
    #
    # Compares the selected player's surface return pts won vs their career
    # overall return pts won average.  Good returners on a surface actively
    # create more BPs than the server's history alone suggests.
    #
    # > 5% above career → 1.08–1.15 (elite on this surface)
    # Within ±5%        → 1.00      (average)
    # > 5% below career → 0.88–0.95 (weaker on this surface)
    # ═════════════════════════════════════════════════════════════════════════
    p_ret1_surf = player_stats.get("return_first_serve_pts_won")
    p_ret2_surf = player_stats.get("return_second_serve_pts_won")
    p_ret1_all  = _p_all.get("return_first_serve_pts_won")
    p_ret2_all  = _p_all.get("return_second_serve_pts_won")

    c2_returner_mult = 1.0
    c2_delta_pct     = 0.0
    c2_source        = "default=1.0(no_career_data)"

    # Part 4: in BP-prop mode, drive C2 off RETURN GAMES WON % (surface vs career)
    # — a more direct measure of breaking than return points won. Fall back to the
    # return-points-won comparison when games-won is unavailable or non-BP mode.
    _rgw_surf = player_stats.get("return_games_won_pct") if bp_prop_mode else None
    _rgw_car  = _p_all.get("return_games_won_pct") if bp_prop_mode else None
    if bp_prop_mode and _rgw_surf is not None and _rgw_car and _rgw_car > 0:
        surf_ret_avg, career_ret_avg, c2_basis = _rgw_surf, _rgw_car, "rgw"
    elif p_ret1_surf is not None and p_ret1_all is not None and p_ret1_all > 0:
        surf_ret_avg   = (p_ret1_surf + p_ret2_surf) / 2 if p_ret2_surf else p_ret1_surf
        career_ret_avg = (p_ret1_all  + p_ret2_all)  / 2 if p_ret2_all  else p_ret1_all
        c2_basis = "retpts"
    else:
        surf_ret_avg = career_ret_avg = None
        c2_basis = "none"

    # Step 4: gate the boost/penalty on the TOUR average too, so a returner who
    # is above their own career but still below tour-average (or vice-versa)
    # isn't over/under-credited. A WTA returner judged only vs their career could
    # look "elite" while sitting at WTA-average; requiring both prevents that.
    _avgs = ATP_TOUR_AVERAGES if (tour or "ATP").upper() == "ATP" else WTA_TOUR_AVERAGES
    _tour_ret_avg = _avgs.get("return_games_won") if c2_basis == "rgw" else _avgs.get("return_pts_won")
    if surf_ret_avg is not None and career_ret_avg and career_ret_avg > 0:
        c2_delta_pct = (surf_ret_avg - career_ret_avg) / career_ret_avg
        _above_tour = (_tour_ret_avg is None) or (surf_ret_avg > _tour_ret_avg)
        _below_tour = (_tour_ret_avg is None) or (surf_ret_avg < _tour_ret_avg)
        if c2_delta_pct > 0.05 and _above_tour:
            # Upper bound trimmed 1.15 → 1.10 → 1.05. A 5% returner boost
            # on a specialist's best surface is realistic; larger values
            # compound too aggressively with C8 for heavy-favorite BO5 matches.
            excess           = c2_delta_pct - 0.05
            c2_returner_mult = min(1.05, 1.02 + excess * 0.6)
            c2_source        = f"above_career+tour({c2_delta_pct:+.1%},{c2_basis})"
        elif c2_delta_pct < -0.05 and _below_tour:
            deficit          = abs(c2_delta_pct) - 0.05
            c2_returner_mult = max(0.88, 0.95 - deficit * 1.4)
            c2_source        = f"below_career+tour({c2_delta_pct:+.1%},{c2_basis})"
        else:
            c2_source        = f"tour_gated({c2_delta_pct:+.1%},{c2_basis})"

    logger.info(
        "BP_C2 | player=%s | surf_ret=%.1f%% | career_ret=%.1f%% | "
        "delta=%.1f%% | c2=%.3f | source=%s",
        player_name,
        p_ret1_surf or 0.0, p_ret1_all or 0.0,
        c2_delta_pct * 100, c2_returner_mult, c2_source,
    )

    # ═════════════════════════════════════════════════════════════════════════
    # COMPONENT 3 — Player BP conversion rate (returner stats only)
    #
    # NEVER use bp_faced_count (that is a SERVE stat).
    # Fixed 60/40 blend: 60% surface-specific + 40% overall career rate.
    # Optionally enriched with Tennis Abstract surface data (35% TA + 65% SS).
    # ═════════════════════════════════════════════════════════════════════════
    conv_rate_source = ""
    player_ta_surf   = None
    if player_ta:
        player_ta_surf = player_ta.get("surface_stats", {}).get(surface)

    ta_conv_pct = None
    if (player_ta_surf
            and player_ta_surf.get("bp_conv_pct") is not None
            and player_ta_surf.get("matches", 0) >= 5):
        ta_conv_pct        = player_ta_surf["bp_conv_pct"]
        ta_used            = True
        ta_surface_matches = player_ta_surf.get("matches", 0) or 0

    ss_surf_conv    = player_stats.get("bp_converted")
    ss_overall_conv = player_stats.get("overall_bp_converted")
    surf_sample     = (player_stats.get("surface_matches", 0)
                       or player_stats.get("matches_played", 0) or 0)
    overall_sample  = player_stats.get("overall_matches_played", 0) or 0
    surf_only_flag  = False

    if ss_surf_conv and ss_overall_conv:
        ss_conv_pct    = 0.60 * ss_surf_conv + 0.40 * ss_overall_conv
        conv_surf_tier = f"SS:60/40 surf_n={surf_sample}"
        if surf_sample < 5:
            surf_only_flag = True
    elif ss_surf_conv:
        ss_conv_pct    = ss_surf_conv
        conv_surf_tier = "SS:surface_only"
    elif ss_overall_conv:
        ss_conv_pct    = ss_overall_conv
        conv_surf_tier = "SS:overall_only"
        surf_only_flag = True
    else:
        ss_conv_pct    = None
        conv_surf_tier = "SS:none"

    if ta_conv_pct is not None and ss_conv_pct and ss_conv_pct > 0:
        conv_rate_pct    = 0.35 * ta_conv_pct + 0.65 * ss_conv_pct
        conv_rate_source = f"TA(35%)+{conv_surf_tier}"
    elif ta_conv_pct is not None:
        conv_rate_pct    = ta_conv_pct
        conv_rate_source = f"TA_{surface}"
    elif ss_conv_pct and ss_conv_pct > 0:
        conv_rate_pct    = ss_conv_pct
        conv_rate_source = conv_surf_tier
    else:
        conv_rate_pct    = None
        conv_rate_source = "none"

    # ── Conversion rate cap ───────────────────────────────────────────────────
    # Prevent TA enrichment from inflating conv_rate_pct beyond what any player
    # sustains in practice.  Elite WTA clay returner (Swiatek): ~55%.
    # Elite ATP clay returner (Alcaraz, Sinner): ~50%.
    _CONV_RATE_CAP = {"WTA": 52.0, "ATP": 40.0}
    # ATP cap reduced 50→45→40. Working backwards from known market prices:
    # the book's implied effective conversion rate for top ATP clay returners
    # vs weak servers is ~38-42%, not 45-50%. The TA enrichment pipeline can
    # push computed C3 above these realistic ceilings, which compounds with
    # C2 and C8 to inflate projections.
    if conv_rate_pct is not None:
        _cap = _CONV_RATE_CAP.get(tour, 52.0)
        if conv_rate_pct > _cap:
            logger.info(
                "BP_C3_CAP | player=%s | raw=%.1f%% → cap=%.1f%% (tour=%s)",
                player_name, conv_rate_pct, _cap, tour,
            )
            conv_rate_pct = _cap

    # Stat audit — verify return vs serve stat separation
    _ret_conv_raw = player_stats.get("return_bp_converted")
    _ret_opps_raw = player_stats.get("return_bp_opportunities")
    _srv_faced    = player_stats.get("bp_faced_count")
    logger.info(
        "BP_C3 | player=%s | surface=%s | ss_surf=%.1f%% | ss_overall=%.1f%% | "
        "surf_n=%d | ta=%.1f%% | c3=%.1f%% | source=%s | "
        "return_conv_pm=%s | return_opps_pm=%s | serve_faced_pm=%s",
        player_name, surface,
        ss_surf_conv or 0.0, ss_overall_conv or 0.0, surf_sample,
        ta_conv_pct or 0.0, conv_rate_pct or 0.0, conv_rate_source,
        f"{_ret_conv_raw:.2f}" if _ret_conv_raw is not None else "None",
        f"{_ret_opps_raw:.2f}" if _ret_opps_raw is not None else "None",
        f"{_srv_faced:.2f}"    if _srv_faced    is not None else "None",
    )

    p_matches = player_stats.get("matches_played", 0) or 0
    o_matches = opponent_stats.get("matches_played", 0) or 0

    # ── Conversion-rate fallback — never fail outright on missing data ────────
    # A player with few recent matches on the selected surface (e.g. a
    # hard-court specialist on clay) can have no surface conversion rate in
    # the recency-focused window. Instead of returning "Insufficient Data",
    # fall back to all-surface career conversion, then the tour average, and
    # flag it so the UI can disclose the limitation.
    conv_rate_fallback = False
    if not conv_rate_pct:
        _all_conv = _p_all.get("bp_converted") if isinstance(_p_all, dict) else None
        if _all_conv and _all_conv > 0:
            conv_rate_pct    = _all_conv
            conv_rate_source = "fallback_all_surface"
        else:
            conv_rate_pct    = 45.0 if tour == "ATP" else 42.0
            conv_rate_source = "fallback_tour_avg"
        conv_rate_fallback = True
        logger.info(
            "BP_CONV_FALLBACK | player=%s | source=%s | conv=%.1f",
            player_name, conv_rate_source, conv_rate_pct,
        )

    # ═════════════════════════════════════════════════════════════════════════
    # COMPONENT 4 — Serve quality adjustment (opponent hold rate)
    #
    # Only applied when C1 is the tour-average fallback (no player-specific
    # bp_faced data).  When C1 IS player-specific, the opponent's serve
    # weakness is already reflected in their actual bp_faced count — applying
    # C4 on top double-counts it.
    #
    # hold_proxy > 0.70  ≈ >85% service game hold  → Elite  → 0.85
    # 0.63 – 0.70        ≈ 65–85%                  → Good   → 1.00
    # < 0.63             ≈ <65%                     → Weak   → 1.10
    # ═════════════════════════════════════════════════════════════════════════
    opp_hold_proxy = _hold_rate_proxy(opponent_stats)

    # Serve-quality TIER + C4 multiplier from the opponent's SERVICE GAMES WON %,
    # judged against TOUR-SPECIFIC cutoffs (Steps 2/3) so a WTA opponent isn't
    # measured on an ATP yardstick. Falls back to the hold proxy (×100) when SGW
    # is unavailable. Tiers: Elite / Strong / Average / Weak.
    _opp_sgw = opponent_stats.get("service_games_won_pct")
    if _opp_sgw is None:
        # hold proxy is service POINTS won — convert to a GAMES-won % scale.
        _opp_sgw = _game_win_prob(opp_hold_proxy) * 100.0
    opp_serve_tier, c4_full = _serve_tier_and_adj(_opp_sgw, tour)

    # The C4 MULTIPLIER is only applied when C1 came from the tour-average
    # fallback. When C1 is the opponent's actual bp_faced, their serve weakness
    # is already embedded in C1, so applying C4 on top would double-count it.
    c4_serve_qual = c4_full if used_opp_tour_avg else 1.00

    logger.info(
        "BP_C4 | hold_proxy=%.3f | tier=%s | c4=%.2f",
        opp_hold_proxy, opp_serve_tier, c4_serve_qual,
    )

    # ═════════════════════════════════════════════════════════════════════════
    # COMPONENT 5 — Player-specific surface adjustment
    #
    # DISABLED: C3 already applies a 60/40 surface/overall blend, which
    # captures surface specialization in the conversion rate itself.  Adding a
    # multiplicative delta factor from the same two fields (surf_conv, overall_conv)
    # double-counts the clay/grass advantage for specialists and pushes the
    # effective conversion rate above the player's actual surface-specific rate.
    #
    # C5 is kept in the return dict for logging and backward compatibility but
    # is pinned to 1.0 — it applies no adjustment to the projection.
    # ═════════════════════════════════════════════════════════════════════════
    c5_surf_adj       = 1.0   # disabled — see note above
    player_surf_delta = 0.0

    if ss_surf_conv and ss_overall_conv and ss_overall_conv > 0:
        player_surf_delta = max(-20.0, min(20.0, ss_surf_conv - ss_overall_conv))
        # c5_surf_adj would be max(0.85, min(1.20, 1.0 + player_surf_delta / 100.0))
        # but is intentionally left at 1.0 to avoid double-counting C3's blend.

    # Opponent surface tendency for logging (already embedded in C1)
    opp_surf_delta_log = 0.0
    if raw_opp_bp_faced and overall_opp_bp_faced:
        opp_surf_delta_log = raw_opp_bp_faced - overall_opp_bp_faced

    logger.info(
        "BP_C5 | player=%s | surf_conv=%.1f%% | overall_conv=%.1f%% | "
        "delta=%.1f_pp | c5=%.3f | "
        "opp_surf_bp=%.2f | opp_overall_bp=%.2f | opp_delta=%.2f(in_C1)",
        player_name,
        ss_surf_conv or 0.0, ss_overall_conv or 0.0, player_surf_delta, c5_surf_adj,
        raw_opp_bp_faced or 0.0, overall_opp_bp_faced or 0.0, opp_surf_delta_log,
    )

    # ═════════════════════════════════════════════════════════════════════════
    # COMPONENT 6 — ST Pace Index modifier (within-surface speed variation)
    #
    # Thresholds updated for the String Tension ST Pace Index scale:
    #
    # Clay (ST Pace Index scale, Very Slow / Slow / Average tiers):
    #   ST ≤ 28  (Very Slow / low-Slow)  → +2.5%  returner advantage
    #            Examples: Barcelona 27.2, Hamburg 28.4, generic clay 26
    #   ST ≤ 33  (Slow)                  → +1.0%
    #            Examples: Munich 29.1, Rome 29.6, Monte Carlo 30.4, Madrid 31.9
    #   ST > 33  (Average or faster)     →  0%  neutral
    #            Example: Roland Garros 2026 at 37.7 → neutral, NOT slow-clay bonus
    #
    # Grass: driven by opponent's 1st-serve pts won, NOT CPR value.
    # Hard:  0% — the aces/DFs formula already handles hard-court speed via
    #         cpr_factor = 1 + (cpr - CPR_NEUTRAL) / 100.
    # ═════════════════════════════════════════════════════════════════════════
    c6_cpr_mod  = 1.0
    c6_note     = "hard_zero"

    if surface == "Clay":
        if cpr <= 28:
            c6_cpr_mod = 1.025
            c6_note    = f"clay_slow(ST={cpr:.1f})+2.5%"
        elif cpr <= 33:
            c6_cpr_mod = 1.010
            c6_note    = f"clay_medium(ST={cpr:.1f})+1%"
        else:
            c6_cpr_mod = 1.000
            c6_note    = f"clay_avg(ST={cpr:.1f})0%"
    elif surface == "Grass":
        opp_1st_won = _safe(opponent_stats.get("first_serve_pts_won"), 72.0)
        if opp_1st_won > 75:
            c6_cpr_mod = 0.90
            c6_note    = f"grass_dom(1stWon={opp_1st_won:.1f}%)-10%"
        elif opp_1st_won >= 65:
            c6_cpr_mod = 0.95
            c6_note    = f"grass_solid(1stWon={opp_1st_won:.1f}%)-5%"
        else:
            c6_cpr_mod = 1.00
            c6_note    = f"grass_weak(1stWon={opp_1st_won:.1f}%)0%"

    logger.info(
        "BP_C6 | surface=%s | cpr=%d | opp_1stWon=%.1f%% | c6=%.3f | note=%s",
        surface, cpr,
        _safe(opponent_stats.get("first_serve_pts_won"), 0.0),
        c6_cpr_mod, c6_note,
    )

    # ═════════════════════════════════════════════════════════════════════════
    # BASE PROJECTION  (Components 1–6)
    #
    # base = C1 × C2 × (C3/100) × C4 × C5 × C6
    # ═════════════════════════════════════════════════════════════════════════
    base_proj = (
        c1_opp_bp_faced
        * c2_returner_mult
        * (conv_rate_pct / 100.0)
        * c4_serve_qual
        * c5_surf_adj
        * c6_cpr_mod
    )

    logger.info(
        "BP_BASE | C1=%.2f | C2=%.3f | C3=%.1f%% | C4=%.2f | C5=%.3f | C6=%.3f | base=%.3f",
        c1_opp_bp_faced, c2_returner_mult, conv_rate_pct,
        c4_serve_qual, c5_surf_adj, c6_cpr_mod, base_proj,
    )

    # ── Component trace: C1..C6, each with its inputs and the running result ──
    _r = _trace(trace, "C1_opp_bp_faced",
                {"raw_opp_bp_faced_surface": raw_opp_bp_faced,
                 "opp_surface_stat_n": opp_surf_sample,
                 "tour_avg_bp_faced": round(tour_avg_bp, 3),
                 "min_credible_bp": round(min_credible_bp, 3),
                 "bp_generated_quality_adj": bp_generated_quality_adj,
                 "bp_forward_server_factor": bp_forward_server_factor},
                c1_opp_bp_faced, c1_opp_bp_faced,
                "opponent BPs faced per match on this surface, from stat-rich "
                "data. source=%s" % c1_source)
    _r = _trace(trace, "C2_returner_creation_mult",
                {"basis": c2_basis, "delta_pct": round(c2_delta_pct, 3),
                 "in": round(_r, 3)},
                c2_returner_mult, _r * c2_returner_mult,
                "how much MORE/LESS this returner creates vs their own baseline. "
                "source=%s" % c2_source)
    _r = _r * c2_returner_mult
    _prev = _r
    _r = _r * (conv_rate_pct / 100.0)
    _trace(trace, "C3_conversion_rate",
           {"conv_rate_pct": round(conv_rate_pct, 2), "in": round(_prev, 3),
            "blend": "0.60*surface + 0.40*overall (Sofascore side)"},
           conv_rate_pct / 100.0, _r,
           "BP conversion — the 60/40 surface/overall blend lives here")
    _prev = _r
    _r = _r * c4_serve_qual
    _trace(trace, "C4_serve_quality",
           {"opponent_hold_context": True, "tour": tour, "in": round(_prev, 3)},
           c4_serve_qual, _r,
           "opponent serve-quality adjustment using %s tour thresholds" % tour)
    _prev = _r
    _r = _r * c5_surf_adj
    _trace(trace, "C5_surface_adj",
           {"surface": surface, "in": round(_prev, 3)},
           c5_surf_adj, _r, "player-specific surface adjustment")
    _prev = _r
    _r = _r * c6_cpr_mod
    _trace(trace, "C6_cpr_modifier",
           {"court": court or "(none)", "surface": surface, "in": round(_prev, 3)},
           c6_cpr_mod, _r, "within-surface pace variation; base_proj complete")

    # ═════════════════════════════════════════════════════════════════════════
    # COMPONENT 7 — Break-back momentum bonus (additive)
    #
    # Compute opponent's projected BP won using C1–C6 from OPPONENT perspective,
    # then multiply by surface momentum factor (Clay 0.28, Hard 0.25, Grass 0.20).
    # Added to base BEFORE applying the format multiplier (C8).
    # ═════════════════════════════════════════════════════════════════════════

    # C1_opp: player's bp_faced on their OWN serve (opponent's opportunity pool)
    c1_opp_persp = _safe(player_stats.get("bp_faced_count"), tour_avg_bp)

    # C2_opp: opponent's returner creation multiplier
    opp_ret1_surf = opponent_stats.get("return_first_serve_pts_won")
    opp_ret2_surf = opponent_stats.get("return_second_serve_pts_won")
    opp_ret1_all  = _o_all.get("return_first_serve_pts_won")
    opp_ret2_all  = _o_all.get("return_second_serve_pts_won")
    c2_opp = 1.0
    if opp_ret1_surf is not None and opp_ret1_all is not None and opp_ret1_all > 0:
        opp_surf_ret   = (opp_ret1_surf + opp_ret2_surf) / 2 if opp_ret2_surf else opp_ret1_surf
        opp_career_ret = (opp_ret1_all  + opp_ret2_all)  / 2 if opp_ret2_all  else opp_ret1_all
        if opp_career_ret > 0:
            opp_c2_delta = (opp_surf_ret - opp_career_ret) / opp_career_ret
            if opp_c2_delta > 0.05:
                # Upper bound trimmed 1.15 → 1.10 → 1.05 (matches the player-side C2).
                c2_opp = min(1.05, 1.02 + (opp_c2_delta - 0.05) * 0.6)
            elif opp_c2_delta < -0.05:
                c2_opp = max(0.88, 0.95 - (abs(opp_c2_delta) - 0.05) * 1.4)

    # C3_opp: opponent's conversion rate (60/40 blend)
    opp_surf_conv_raw    = opponent_stats.get("bp_converted") or 0.0
    opp_overall_conv_raw = opponent_stats.get("overall_bp_converted") or opp_surf_conv_raw
    if opp_surf_conv_raw and opp_overall_conv_raw:
        c3_opp = 0.60 * opp_surf_conv_raw + 0.40 * opp_overall_conv_raw
    else:
        c3_opp = opp_surf_conv_raw or opp_overall_conv_raw or 40.0  # tour avg fallback

    # C4_opp: player's serve quality — conditional on same rule as main C4.
    # Only adjust when c1_opp_persp came from the tour-average fallback.
    player_hold_proxy   = _hold_rate_proxy(player_stats)
    player_bp_is_actual = player_stats.get("bp_faced_count") is not None
    if player_bp_is_actual:
        c4_opp = 1.00   # serve weakness already embedded in c1_opp_persp
    else:
        c4_opp = 0.85 if player_hold_proxy > 0.70 else (1.10 if player_hold_proxy < 0.63 else 1.00)

    # C5_opp: opponent's player-specific surface adjustment — disabled (same
    # rationale as player C5: C3_opp 60/40 blend already captures surface specialization)
    c5_opp = 1.0

    # C6_opp: CPR modifier from opponent's perspective (on Grass: use PLAYER's serve quality)
    c6_opp = 1.0
    if surface == "Clay":
        c6_opp = c6_cpr_mod   # same CPR applies to both directions
    elif surface == "Grass":
        player_1st_won = _safe(player_stats.get("first_serve_pts_won"), 72.0)
        if player_1st_won > 75:
            c6_opp = 0.90
        elif player_1st_won >= 65:
            c6_opp = 0.95
        # else: c6_opp stays 1.0

    opp_proj_bp = (
        c1_opp_persp
        * c2_opp
        * (c3_opp / 100.0)
        * c4_opp
        * c5_opp
        * c6_opp
    )

    surface_momentum_mult = _SURFACE_MOMENTUM_MULT.get(surface, 0.25)
    momentum_bonus_raw    = opp_proj_bp * surface_momentum_mult

    # ── Hard cap on momentum bonus ───────────────────────────────────────────
    # The break-back-urgency effect is real but has a natural ceiling — a
    # player doesn't get unlimited momentum from being broken repeatedly. Cap
    # at 0.6 in BO3 / 1.0 in BO5 so extreme opp_proj_bp values (Paul-style
    # weak servers) can't add 1.5+ phantom breaks to the projection.
    _is_bo5_for_cap = (match_format == "best_of_5" and tour == "ATP")
    momentum_cap    = 1.0 if _is_bo5_for_cap else 0.6
    momentum_bonus  = min(momentum_bonus_raw, momentum_cap)
    momentum_capped = momentum_bonus_raw > momentum_cap

    logger.info(
        "BP_C7_MOMENTUM | opp_persp: C1_player_bp_faced=%.2f | C2_opp=%.3f | "
        "C3_opp=%.1f%% | C4_opp=%.2f | C5_opp=%.3f | C6_opp=%.3f | "
        "opp_proj_bp=%.3f | surf_factor=%.2f | momentum_raw=%.3f | "
        "momentum_capped_at=%.2f | momentum_used=%.3f | hit_cap=%s",
        c1_opp_persp, c2_opp, c3_opp, c4_opp, c5_opp, c6_opp,
        opp_proj_bp, surface_momentum_mult, momentum_bonus_raw,
        momentum_cap, momentum_bonus, momentum_capped,
    )

    # ═════════════════════════════════════════════════════════════════════════
    # COMPONENT 8 — Expected-sets scaling
    #
    # Replaces every previous flat multiplier (1.6 → 1.1 → 1.0). The match
    # length is now derived from the win-probability gap:
    #
    #   BO5 gap > 40%  → 3.3 sets   (heavy fav, blowout)
    #       25–40%    → 3.7 sets   (clear fav)
    #       10–25%    → 4.1 sets   (slight fav)
    #       < 10%     → 4.4 sets   (even — many 4- and 5-setters)
    #
    #   BO3 gap > 40%  → 2.1 sets
    #       25–40%    → 2.3 sets
    #       10–25%    → 2.5 sets
    #       < 10%     → 2.6 sets
    #
    # The C8 multiplier becomes  expected_sets / avg_historical_sets, which
    # converts the per-match base (built from Sofascore data averaging ~2.45
    # sets for ATP, 2.30 for WTA) into the projected per-match value for
    # THIS match's actual expected length. The momentum bonus is similarly
    # rescaled per-set, so longer matches produce proportionally more
    # break-back windows.
    # ═════════════════════════════════════════════════════════════════════════
    _p_form_bp = player_stats.get("form") or player_stats.get("recent_form")
    _o_form_bp = opponent_stats.get("form") or opponent_stats.get("recent_form")
    p_prob, o_prob, win_prob_gap = _estimate_win_prob(
        player_stats, opponent_stats,
        p_rank=player_stats.get("rank") or player_stats.get("currentRank"),
        o_rank=opponent_stats.get("rank") or opponent_stats.get("currentRank"),
        p_form=_p_form_bp, o_form=_o_form_bp,
    )
    expected_sets, comp_label = _expected_sets_from_gap(win_prob_gap, is_bo5)
    avg_hist_sets             = _AVG_HISTORICAL_SETS.get(tour, 2.45)
    c8_format_mult            = expected_sets / max(avg_hist_sets, 0.01)
    # Per the documented C8 invariant ("Sofascore per-match bp_faced already
    # embeds match length"), C8 must not scale a per-match break count ABOVE
    # its own baseline — doing so double-counts match length. Down-scaling for
    # an expected blowout (fewer service games → fewer breaks) is still valid.
    # This only bites non-BO5: WTA's avg_historical_sets (2.30) sits below a
    # competitive match's 2.6 sets, so every close WTA match was being inflated
    # ~13%. ATP non-GS is unaffected (avg_hist 2.60 ≈ competitive 2.6 → ~1.0).
    # BO5 Grand Slams keep the upward scaling (genuinely more sets/breaks).
    if not is_bo5:
        c8_format_mult = min(1.0, c8_format_mult)
    bo_scale                  = c8_format_mult   # alias for return-dict compat

    logger.info(
        "BP_C8_EXPSETS | tour=%s | bo5=%s | court=%s | "
        "win_prob_gap=%.1fpp | exp_sets=%.2f (%s) | avg_hist=%.2f | c8=%.3f",
        tour, is_bo5, court or "generic",
        win_prob_gap, expected_sets, comp_label, avg_hist_sets, c8_format_mult,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # COMBINED PROJECTION:  proj = (base_per_set + momentum_per_set) × exp_sets
    #                            = (base + momentum) × C8
    # The momentum bonus is recomputed in per-set terms first so that a
    # longer match accumulates proportionally more break-back windows.
    # ─────────────────────────────────────────────────────────────────────────
    proj_before_format = base_proj + momentum_bonus
    proj               = proj_before_format * c8_format_mult
    _trace(trace, "C7_momentum_bonus",
           {"opp_projected_bp": round(opp_proj_bp, 3),
            "surface_momentum_mult": surface_momentum_mult,
            "momentum_raw": round(momentum_bonus_raw, 3),
            "cap": momentum_cap, "base_proj_in": round(base_proj, 3)},
           momentum_bonus, proj_before_format,
           "ADDITIVE (absolute breaks), applied BEFORE C8. cap=%.2f (%s)%s"
           % (momentum_cap, "BO5 ATP" if _is_bo5_for_cap else "BO3",
              " [CAP BIT: raw %.3f -> %.3f]" % (momentum_bonus_raw, momentum_bonus)
              if momentum_capped else ""))
    _trace(trace, "C8_expected_sets_scaling",
           {"expected_sets": expected_sets, "avg_historical_sets": avg_hist_sets,
            "win_prob_gap": round(win_prob_gap, 2), "is_bo5": is_bo5,
            "raw_ratio": round(expected_sets / max(avg_hist_sets, 0.01), 4),
            "in": round(proj_before_format, 3)},
           c8_format_mult, proj,
           "C8 = exp_sets/avg_hist%s. INVARIANT: non-BO5 capped at 1.0 — per-match "
           "bp_faced already embeds match length, so scaling ABOVE baseline would "
           "double-count it. CAP HELD: %s"
           % (", then min(1.0, ...) for non-BO5" if not is_bo5 else " (BO5 keeps upward scaling)",
              "yes (<=1.0)" if c8_format_mult <= 1.0 else "NO — c8=%.3f EXCEEDS 1.0" % c8_format_mult))

    # ── Set momentum (Improvement 6): a player who takes the pivotal middle set
    # carries momentum into the decider. When the match projects to a deciding
    # set (expected_sets above the threshold), add a small BP bonus for the
    # favourite (likeliest to win the 2nd set). Gated + format-aware.
    _set_mom_thresh = 4.0 if is_bo5 else 2.4
    set_momentum_bonus = 0.0
    if expected_sets > _set_mom_thresh and p_prob >= 50.0:
        set_momentum_bonus = round(min(0.4, 0.2 + (expected_sets - _set_mom_thresh) * 0.30), 3)
        proj += set_momentum_bonus
        logger.info("BP_SET_MOMENTUM | exp_sets=%.2f thresh=%.1f p_prob=%.0f%% -> +%.2f BP",
                    expected_sets, _set_mom_thresh, p_prob, set_momentum_bonus)
    _trace(trace, "set_momentum_bonus",
           {"expected_sets": expected_sets, "threshold": _set_mom_thresh,
            "player_win_prob": round(p_prob, 1),
            "in": round(proj - set_momentum_bonus, 3)},
           set_momentum_bonus, proj,
           "deciding-set bonus, favourite only (additive, after C8)"
           if set_momentum_bonus else
           "not applied (needs exp_sets>%.1f AND player win prob>=50%%)" % _set_mom_thresh)
    _trace(trace, "projector_output", {"chain_result": round(proj, 3)},
           proj, round(proj, 1),
           "END OF THE PROJECTOR — NOT the final number; main.py may still apply "
           "indoor / H2H-psych modifiers after this. hand_bp_factor and the H2H "
           "blend are DIAGNOSTIC ONLY and deliberately do NOT touch proj — one "
           "calculation, one number")

    logger.info(
        "BP_COMBINED | base=%.3f | momentum=%.3f | before_format=%.3f | "
        "c8=%.1f | proj=%.3f",
        base_proj, momentum_bonus, proj_before_format, c8_format_mult, proj,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # BP_DIAG — every component in human-readable form, one log line per
    # projection. Use this when a number looks wrong (e.g. Ruud vs Paul RG
    # projecting 8 against a book-implied 5-6) to identify which factor or
    # which compounding combination is doing the inflating.
    # ─────────────────────────────────────────────────────────────────────────
    _running_base    = c1_opp_bp_faced
    _after_c2        = _running_base * c2_returner_mult
    _after_c3        = _after_c2 * (conv_rate_pct / 100.0)
    _after_c4        = _after_c3 * c4_serve_qual
    _after_c5        = _after_c4 * c5_surf_adj
    _after_c6        = _after_c5 * c6_cpr_mod
    _final_momentum  = momentum_bonus * c8_format_mult
    logger.info(
        "BP_DIAG | %s vs %s (%s, %s, BO%s) |\n"
        "  C1 opp_bp_faced/match  = %6.2f  (%s, raw_surf=%s, overall=%s)\n"
        "  × C2 returner_mult     = %6.3f  (%s)                  → %6.3f\n"
        "  × C3 conv_rate_blended = %5.1f%%  (surf %.1f%%×0.60 + overall %.1f%%×0.40) → %6.3f\n"
        "  × C4 serve_quality_adj = %6.2f  (%s, hold_proxy=%.2f) → %6.3f\n"
        "  × C5 player_surf_adj   = %6.3f  (delta=%.1fpp, disabled→1.0) → %6.3f\n"
        "  × C6 cpr_modifier      = %6.3f  (%s) → BASE = %6.3f\n"
        "  + C7 momentum_raw      = %6.3f  (opp_proj_bp %.2f × surf_mult %.2f)\n"
        "    momentum_capped@%.1f = %6.3f  (capped=%s)\n"
        "  pre-C8 (base+mom)      = %6.3f\n"
        "  × C8 exp_sets_scale    = %6.3f  (exp_sets %.2f / avg_hist %.2f, %s, gap=%.1fpp)\n"
        "  × hand_bp_factor       = %6.3f  (opp_hand=%s)\n"
        "  FINAL                  = %6.2f  [base_after_c8=%.2f + mom_after_c8=%.2f]",
        player_name, opp_name, surface, court or "generic",
        "5" if is_bo5 else "3",
        c1_opp_bp_faced, c1_source,
        f"{raw_opp_bp_faced:.2f}" if raw_opp_bp_faced else "None",
        f"{overall_opp_bp_faced:.2f}" if overall_opp_bp_faced else "None",
        c2_returner_mult, c2_source, _after_c2,
        conv_rate_pct, ss_surf_conv or 0.0, ss_overall_conv or 0.0, _after_c3,
        c4_serve_qual, opp_serve_tier, opp_hold_proxy, _after_c4,
        c5_surf_adj, player_surf_delta, _after_c5,
        c6_cpr_mod, c6_note, _after_c6,
        momentum_bonus_raw, opp_proj_bp, surface_momentum_mult,
        momentum_cap, momentum_bonus, momentum_capped,
        proj_before_format,
        c8_format_mult, expected_sets, avg_hist_sets, comp_label, win_prob_gap,
        # hand_bp_factor is applied below — log with placeholder for now (real
        # value appended via BP_HAND).
        1.0, "see BP_HAND below",
        proj, base_proj * c8_format_mult, _final_momentum,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Reality check — high-projection flag (warning only, not a cap).
    # If the projection blows past realistic ranges flag it so the UI can
    # surface "verify data quality" without suppressing the value. The user
    # decides whether to trust it.
    # ─────────────────────────────────────────────────────────────────────────
    _high_threshold = 9.0 if is_bo5 else 7.0
    bp_high_projection = proj > _high_threshold
    if bp_high_projection:
        logger.warning(
            "BP_HIGH | %s | proj=%.2f exceeds %.1f (BO%s) — review components",
            player_name, proj, _high_threshold, "5" if is_bo5 else "3",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # GS DIAGNOSTIC — individual component log for Sinner-style matchups
    # TARGET: 5.0–7.0 for Sinner vs Cerundolo Roland Garros BO5 Clay
    # ─────────────────────────────────────────────────────────────────────────
    logger.info(
        "BP_GS_DIAGNOSTIC | %s vs %s | surface=%s | format=%s | tour=%s | "
        "C1=%.2f(%s) | C2_ret_mult=%.3f(%s) | C3_conv=%.1f%%(surf=%.1f%%,n=%d,overall=%.1f%%) | "
        "C4_srv_qual=%.2f(%s) | C5_surf_adj=%.3f(delta=%.1fpp) | C6_cpr=%.3f(%s) | "
        "base=%.3f | C7_opp_proj=%.3f | surf_mom=%.2f | mom_bonus=%.3f | "
        "C8_fmt=%.1f | before_fmt=%.3f | FINAL=%.2f | %s",
        player_name, opp_name, surface, match_format, tour,
        c1_opp_bp_faced, c1_source,
        c2_returner_mult, c2_source,
        conv_rate_pct, ss_surf_conv or 0.0, surf_sample, ss_overall_conv or 0.0,
        c4_serve_qual, opp_serve_tier,
        c5_surf_adj, player_surf_delta,
        c6_cpr_mod, c6_note,
        base_proj, opp_proj_bp, surface_momentum_mult, momentum_bonus,
        c8_format_mult, proj_before_format, proj,
        "TARGET:5.0-7.0" if is_bo5 and surface == "Clay" else "OK",
    )

    if is_bo5 and proj < 5.0:
        logger.warning(
            "BP_LOW_GS | player=%s | surface=%s | proj=%.2f | "
            "C1=%.2f C2=%.3f C3=%.1f%% C4=%.2f C5=%.3f C6=%.3f C8=%.1f",
            player_name, surface, proj,
            c1_opp_bp_faced, c2_returner_mult, conv_rate_pct,
            c4_serve_qual, c5_surf_adj, c6_cpr_mod, c8_format_mult,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Handedness factor — DIAGNOSTIC ONLY (no longer mutates proj).
    # Per spec: a single calculation produces a single number. The user-
    # specified BP formula is C1×C2×C3×C4×C5×C6×C8 + momentum_bonus — no
    # post-formula multipliers. Handedness data is retained in the response
    # for transparency, but is not applied to the projection.
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
    logger.info(
        "BP_HAND | opp_hand=%s | factor=%.3f | DIAGNOSTIC_ONLY (proj unchanged)",
        opp_hand, hand_bp_factor,
    )

    env = detect_environment(player_stats, opponent_stats, surface=surface, tour=tour)

    # ─────────────────────────────────────────────────────────────────────────
    # H2H availability — DIAGNOSTIC ONLY (no longer mutates proj).
    # The h2h_used flag still feeds the confidence calculation below, but the
    # 75/25 blend that previously inflated/deflated proj after the formula has
    # been removed per spec: one calculation, one number.
    # ─────────────────────────────────────────────────────────────────────────
    h2h_used = h2h_bp_avg is not None and h2h_bp_avg > 0 and h2h_match_count >= 3
    if h2h_used:
        logger.info(
            "BP_H2H | avg=%.2f | available_for_confidence_only (proj unchanged)",
            h2h_bp_avg,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # All-surface reference — DIAGNOSTIC ONLY (no longer mutates proj).
    # Per spec the formula's output is final; we still compute the reference
    # so the UI can disclose it and the confidence logic can detect outliers,
    # but the 70/30 blend that previously deflated proj toward this reference
    # has been removed.
    # ─────────────────────────────────────────────────────────────────────────
    all_surface_blended = False
    all_surface_ref     = None
    if _p_all:
        all_conv_pct = _p_all.get("bp_converted")
        all_bp_opps  = (_p_all.get("return_bp_opportunities")
                        or _tour_avg(tour, "Hard")["bp_faced_per_match"])
        if all_conv_pct and all_conv_pct > 0:
            all_surface_ref = (all_conv_pct / 100.0) * all_bp_opps
            if proj < all_surface_ref * 0.60:
                logger.info(
                    "BP_ALL_SURF | proj=%.2f < 60%% of ref=%.2f | "
                    "diagnostic_only (proj unchanged)",
                    proj, all_surface_ref,
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
        logger.warning("BP_SANITY_FAIL | proj=%.2f", proj)

    # ─────────────────────────────────────────────────────────────────────────
    # Confidence — base + TA adjustment + BP surface-sample adjustment
    # ─────────────────────────────────────────────────────────────────────────
    conf = _confidence(p_matches, o_matches, h2h_used)
    if ta_used and ta_surface_matches < 20:
        conf = max(0, conf - 8)
    elif ta_used and ta_surface_matches > 50:
        conf = min(95, conf + 5)
    # Surface sample size: weights stay fixed, confidence adjusts
    if surf_sample < 5:
        conf = max(0, conf - 20)
    elif surf_sample < 10:
        conf = max(0, conf - 10)
    elif surf_sample >= 20:
        conf = min(95, conf + 5)

    logger.info(
        "BP_FINAL | player=%s | PROJECTION=%.2f | conf=%d | "
        "surf_sample=%d | ta_surf=%d | h2h=%s | all_blend=%s",
        player_name, proj, conf, surf_sample, ta_surface_matches,
        h2h_used, all_surface_blended,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Return dict — keys kept backward-compatible with main.py response
    # ─────────────────────────────────────────────────────────────────────────
    p1_ret = _return_pts_won(player_stats)
    p2_srv = _safe(opponent_stats.get("first_serve_pts_won"), 72.0)

    return {
        "projection":            round(proj, 1),
        "lean":                  "OVER" if proj > base_proj else "UNDER",
        "confidence":            conf,
        # ── C3 Conversion rate ────────────────────────────────────────────────
        "conv_rate_pct":         round(conv_rate_pct, 1),
        "conv_rate_source":      conv_rate_source,
        "surf_conv_pct":         round(ss_surf_conv, 1) if ss_surf_conv else None,
        "overall_conv_pct":      round(ss_overall_conv, 1) if ss_overall_conv else None,
        "surf_conv_sample":      surf_sample,
        "overall_conv_sample":   overall_sample,
        "surf_only_flag":        surf_only_flag,
        # ── C1 Opportunity pool ───────────────────────────────────────────────
        "opp_bp_faced":          round(c1_opp_bp_faced, 1),
        "surf_opp_bp_faced":     round(raw_opp_bp_faced, 1) if raw_opp_bp_faced else None,
        "overall_opp_bp_faced":  round(overall_opp_bp_faced, 1) if overall_opp_bp_faced else None,
        "opp_surf_sample":       opp_surf_sample,
        "used_opp_tour_avg":     used_opp_tour_avg,
        # ── Formula components ────────────────────────────────────────────────
        "returner_mult":         round(c2_returner_mult, 3),
        "serve_quality_adj":     round(c4_serve_qual, 3),
        "opp_serve_tier":        opp_serve_tier,
        "opp_hold_proxy":        round(opp_hold_proxy, 3),
        "player_surface_adj":    round(c5_surf_adj, 3),
        "player_surf_delta_pp":  round(player_surf_delta, 2),
        "cpr_mod":               round(c6_cpr_mod, 3),
        "cpr_factor":            round(c6_cpr_mod, 4),   # compat alias
        "surface_calibration":   round(c5_surf_adj, 3),  # compat alias
        "cpr":                   cpr,
        "bo_scale":              bo_scale,
        "match_format":          match_format,
        "hand_bp_factor":        round(hand_bp_factor, 3),
        # ── C7 Momentum ──────────────────────────────────────────────────────
        "base_proj":             round(base_proj, 2),
        "opp_projected_bp_won":  round(opp_proj_bp, 2),
        "momentum_bonus":        round(momentum_bonus, 3),
        "surface_momentum_mult": surface_momentum_mult,
        "bo5_momentum_mult":     1.0,   # format mult now in C8; kept for UI compat
        # ── Display ──────────────────────────────────────────────────────────
        "player_bp_won_per_match":  round((conv_rate_pct / 100.0) * c1_opp_bp_faced, 1),
        "player_bp_opps_per_match": round(c1_opp_bp_faced, 1),
        "opp_hold_rate_pct":        round(opp_hold_proxy * 100, 1),
        # ── Part 1/2/5: BP generated, games-won %, server-quality badge ──────
        "bp_generated_per_match":   (round(bp_generated_raw, 2) if bp_generated_raw is not None
                                     else (round(player_stats.get("return_bp_opportunities"), 2)
                                           if player_stats.get("return_bp_opportunities") is not None
                                           else None)),
        "bp_generated_quality_adj": (round(bp_generated_quality_adj, 2)
                                     if bp_generated_quality_adj is not None else None),
        "forward_server_factor":    round(bp_forward_server_factor, 3),
        "player_service_games_won_pct": (round(player_stats.get("service_games_won_pct"), 1)
                                         if player_stats.get("service_games_won_pct") is not None else None),
        "player_return_games_won_pct":  (round(player_stats.get("return_games_won_pct"), 1)
                                         if player_stats.get("return_games_won_pct") is not None else None),
        "opp_service_games_won_pct":    (round(opponent_stats.get("service_games_won_pct"), 1)
                                         if opponent_stats.get("service_games_won_pct") is not None else None),
        "opp_return_games_won_pct":     (round(opponent_stats.get("return_games_won_pct"), 1)
                                         if opponent_stats.get("return_games_won_pct") is not None else None),
        "opp_server_quality_tier":  _server_quality_tier_sgw(
                                        opponent_stats.get("service_games_won_pct"), tour),
        "environment":           env,
        "h2h_bp_avg":            round(h2h_bp_avg, 1) if h2h_used else None,
        # ── Quality flags ─────────────────────────────────────────────────────
        "sanity_failed":         sanity_failed,
        "all_surface_blended":   all_surface_blended,
        "all_surface_ref":       round(all_surface_ref, 2) if all_surface_ref else None,
        "ta_used":               ta_used,
        "ta_surface_matches":    ta_surface_matches,
        "p1_ret":                round(p1_ret, 1),
        "p2_srv":                round(p2_srv, 1),
        # ── Expected-sets exposure ────────────────────────────────────────────
        "expected_sets":         round(expected_sets, 2),
        "competitiveness":       comp_label,
        "win_prob_gap":          round(win_prob_gap, 1),
        "p1_win_prob":           round(p_prob, 1),
        "p2_win_prob":           round(o_prob, 1),
        "avg_historical_sets":   round(avg_hist_sets, 2),
        "is_bo5":                is_bo5,
        "bp_won_per_set":        round(base_proj / max(avg_hist_sets, 0.01), 3),
        # ── Reality-check flag (warning only, not a cap) ─────────────────────
        "bp_high_projection":    bp_high_projection,
        "bp_high_threshold":     _high_threshold,
        "bp_momentum_capped":    momentum_capped,
        "bp_momentum_cap":       momentum_cap,
        "bp_momentum_raw":       round(momentum_bonus_raw, 3),
    }


def _last_name(name: str) -> str:
    parts = (name or "").strip().split()
    return parts[-1] if parts else name


def _fmt_num(val, fmt: str = ".1f", default: str = "—") -> str:
    if val is None:
        return default
    try:
        return format(float(val), fmt)
    except Exception:
        return default


def _last_n_values(matches: list, key: str, n: int = 5) -> list:
    """Pull the last `n` non-None values of `key` from the surface match log."""
    if not matches:
        return []
    out = []
    for m in matches[:n]:
        v = m.get(key)
        if v is not None:
            try:
                out.append(round(float(v), 1))
            except Exception:
                pass
    return out


def _under_over_count(values: list, line: float) -> tuple:
    """Returns (over_count, under_count) for a list of values vs the line."""
    if not values or line <= 0:
        return 0, 0
    over  = sum(1 for v in values if v > line)
    under = sum(1 for v in values if v < line)
    return over, under


def _window_phrase(meta: dict) -> str:
    """
    Phrase describing the time window the headline figure was drawn from.
      52w available, healthy           → "in the last 52 weeks"
      2yr fallback                     → "in the last two years"
      TA log unavailable (Sofascore-only) → "in recent form"
      otherwise                        → "in recent form"
    """
    if not meta:
        return "in recent form"
    tier = meta.get("tier", "none")
    if tier == "52w":
        return "in the last 52 weeks"
    if tier == "2yr":
        return "in the last two years"
    return "in recent form"


def _recent_form_phrase(meta: dict, surface: str, player_name: str) -> str:
    """
    Natural-language sample-size acknowledgment when data is thin. Returns
    empty string for healthy samples (≥5 surface matches in 52 weeks) or when
    TA is genuinely unavailable (because the Sofascore tiers still carry the
    projection — there's no point telling the bettor we couldn't reach TA).
    """
    if not meta:
        return ""
    tier      = meta.get("tier", "none")
    surf_n    = meta.get("surface_n", 0) or 0
    total_n   = meta.get("all_surfaces_n", 0) or 0
    warning   = meta.get("warning")
    last      = _last_name(player_name)

    # TA log missing — quietly skip. Don't claim the player has been "quiet".
    if warning == "ta_unavailable" or tier == "ta_unavailable":
        return ""
    if warning == "insufficient":
        return (f" {last} has been quiet recently — only {total_n} matches across all "
                f"surfaces in the last 52 weeks, so the read here is loose.")
    if tier == "52w" and surf_n >= 5:
        return ""  # healthy — don't mention sample size
    if surf_n < 5 and total_n >= 20:
        # Surface specialist case — busy player, just not on this surface
        return (f" {last} has limited {surface.lower()} appearances in the last 52 "
                f"weeks with only {surf_n} matches tracked on the surface, so the "
                f"{surface.lower()} tendencies are based on a small recent sample.")
    if tier == "2yr" and surf_n > 0:
        return (f" {last}'s {surface.lower()} sample is thin in the last year — "
                f"this leans on {surf_n} matches across the last two years.")
    return ""


# ── Prop-specific report builders ────────────────────────────────────────────
def _report_aces(player_name, opponent_name, surface, court, projection,
                 player_surface_stats, opponent_surface_stats,
                 player_hand, opponent_hand,
                 player_recent_meta, prop_line,
                 last5_matches) -> str:
    """4-6 sentence aces-only scouting report."""
    from src.constants import COURT_CPR, CPR_NEUTRAL
    p_last, o_last = _last_name(player_name), _last_name(opponent_name)

    # Prefer the surface ace rate; when there's no surface serve sample (e.g. no
    # recent grass matches) fall back to the player's overall all-surface rate so
    # the report shows the figure the model actually leaned on, not a blank dash.
    _p_aces_surf = player_surface_stats.get("aces")
    _used_overall = _p_aces_surf is None
    p_aces_val  = _p_aces_surf if _p_aces_surf is not None else player_surface_stats.get("overall_aces")
    p_aces      = _fmt_num(p_aces_val, ".1f")
    p_surf_n    = (player_recent_meta or {}).get("surface_n", 0)
    opp_against = projection.get("opp_ace_against")
    suppress    = projection.get("suppression_factor", 1.0) or 1.0
    cpr         = projection.get("cpr", COURT_CPR.get(court, CPR_NEUTRAL))
    cpr_factor  = projection.get("cpr_factor", 1.0) or 1.0
    hand_factor = projection.get("hand_factor", 1.0) or 1.0
    lean        = projection.get("lean", "NEUTRAL")
    exp_sets    = projection.get("expected_sets")

    sentences = []

    # 1. Player ace rate + sample. When TA is unavailable the figure is
    # driven by Sofascore tiers rather than a 52-week window — phrase it
    # honestly instead of claiming a window we don't have.
    sample_note = ""
    if p_surf_n and p_surf_n >= 5:
        sample_note = f" across {p_surf_n} matches"
    window_phrase = _window_phrase(player_recent_meta)
    if _used_overall and p_aces_val is not None:
        sentences.append(
            f"{player_name} averages {p_aces} aces per match across all surfaces — "
            f"with no recent {surface.lower()} serve sample, the projection leans on "
            f"that overall rate rather than a thin surface split."
        )
    else:
        sentences.append(
            f"{player_name} averages {p_aces} aces per match on {surface.lower()} "
            f"{window_phrase}{sample_note}."
        )

    # 2. Opponent return quality / aces conceded
    if opp_against is not None:
        suppress_pct = abs(1.0 - suppress) * 100
        if suppress < 0.98:
            tail = f"above tour average, applying a {suppress_pct:.0f}% suppression factor"
        elif suppress > 1.02:
            tail = f"below tour average, applying a {suppress_pct:.0f}% boost factor"
        else:
            tail = "roughly tour average — no adjustment to the base rate"
        sentences.append(
            f"{o_last} concedes {opp_against:.1f} aces per match on {surface.lower()} "
            f"as a returner — {tail}."
        )
    else:
        ret_pts = opponent_surface_stats.get("return_first_serve_pts_won")
        if ret_pts is not None:
            sentences.append(
                f"{o_last}'s first-serve return points won sits at {ret_pts:.0f}% "
                f"on {surface.lower()} — "
                f"{'a tough returner' if ret_pts > 36 else 'a beatable returner'}."
            )

    # 3. Handedness + CPR effect (combined to save sentences)
    hand_note = ""
    if player_hand and opponent_hand and player_hand != opponent_hand:
        if hand_factor > 1.01:
            hand_note = (f" The {player_hand}H vs {opponent_hand}H matchup opens up "
                         f"ace angles for {p_last} (+{(hand_factor - 1) * 100:.0f}%).")
        elif hand_factor < 0.99:
            hand_note = (f" The {player_hand}H vs {opponent_hand}H matchup cuts into "
                         f"{p_last}'s ace angles ({(hand_factor - 1) * 100:.0f}%).")
        else:
            hand_note = (f" {player_hand}H vs {opponent_hand}H matchup is neutral on "
                         f"ace production.")
    elif player_hand and opponent_hand:
        hand_note = f" Both players are {player_hand}-handed, no handedness adjustment."

    cpr_pct = abs(1.0 - cpr_factor) * 100
    cpr_dir = "boosts" if cpr_factor > 1.0 else "reduces"
    court_label = court if court and court not in ("", "None") else f"{surface.lower()} courts"
    if cpr_pct >= 3:
        sentences.append(
            f"{surface} CPR {cpr} at {court_label} {cpr_dir} ace output by "
            f"{cpr_pct:.0f}% from baseline.{hand_note}"
        )
    elif hand_note:
        sentences.append(hand_note.strip())

    # 4. Last 5 trend
    last5 = _last_n_values(last5_matches, "aces", 5)
    if last5 and prop_line > 0:
        over, under = _under_over_count(last5, prop_line)
        ace_str = " ".join(str(int(v)) if v == int(v) else f"{v}" for v in last5)
        direction = "went over" if over >= under else "went under"
        sentences.append(
            f"Across {p_last}'s last 5 matches (any surface) he hit {ace_str} aces — "
            f"{max(over, under)} of 5 {direction} the {prop_line:.1f} line, "
            f"one input among the surface and overall rates."
        )

    # Data-limit acknowledgement (only if warranted)
    limit_phrase = _recent_form_phrase(player_recent_meta, surface, player_name)
    if limit_phrase:
        sentences.append(limit_phrase.strip())

    # 5. Closing directional lean
    if prop_line > 0:
        sentences.append(
            f"Conditions lean {lean.title()} {prop_line:.1f} aces."
            if lean in ("OVER", "UNDER") else
            f"No clear edge on the {prop_line:.1f} aces line — stay off."
        )
    else:
        sentences.append(
            f"Lean is {lean.title()} on aces."
            if lean in ("OVER", "UNDER") else
            "No directional edge on aces."
        )

    return " ".join(sentences[:6])


def _report_bp(player_name, opponent_name, surface, court, projection,
               player_surface_stats, opponent_surface_stats,
               player_recent_meta, prop_line, h2h_summary) -> str:
    """4-6 sentence break-points-only scouting report."""
    p_last, o_last = _last_name(player_name), _last_name(opponent_name)

    conv        = projection.get("conv_rate_pct")
    surf_conv_n = projection.get("surf_conv_sample") or (player_recent_meta or {}).get("surface_n", 0)
    opp_faced   = projection.get("opp_bp_faced")
    serve_tier  = projection.get("opp_serve_tier")
    env         = projection.get("environment", "STANDARD")
    match_fmt   = projection.get("match_format", "best_of_3")
    fmt_label   = "best-of-5" if match_fmt == "best_of_5" else "best-of-3"
    exp_sets    = projection.get("expected_sets")
    comp        = projection.get("competitiveness")
    lean        = projection.get("lean", "NEUTRAL")

    sentences = []

    # 1. Player conversion rate on surface
    if conv is not None:
        sample_note = f" across {surf_conv_n} matches" if surf_conv_n and surf_conv_n >= 5 else ""
        window_phrase = _window_phrase(player_recent_meta)
        sentences.append(
            f"{player_name} has converted {conv:.0f}% of break points on "
            f"{surface.lower()} {window_phrase}{sample_note}."
        )

    # 2. Opponent BP opportunities conceded
    if opp_faced is not None:
        serve_qual = {
            "Elite": "elite hold — opportunities will be limited",
            "Good":  "solid hold — each opportunity counts",
            "Weak":  "leaky hold — opportunity pool is inflated",
        }.get(serve_tier, "average hold")
        sentences.append(
            f"{o_last} faces {opp_faced:.1f} BPs per match on {surface.lower()} "
            f"({serve_qual})."
        )

    # 2b. Opponent server-quality tier + the player's (quality-adjusted) BP
    # creation against servers of this calibre — addresses that BP stats padded
    # against weak servers don't predict performance vs strong ones (Part 5).
    sq_tier = projection.get("opp_server_quality_tier")
    opp_sgw = projection.get("opp_service_games_won_pct")
    bp_gen  = projection.get("bp_generated_per_match")
    bp_genq = projection.get("bp_generated_quality_adj")
    if sq_tier and bp_gen is not None:
        sgw_txt = (f"{o_last} wins {opp_sgw:.0f}% of service games on {surface.lower()} "
                   f"— {sq_tier.lower()}" if opp_sgw is not None
                   else f"{o_last} rates as a {sq_tier.lower()}")
        if bp_genq is not None and abs(bp_genq - bp_gen) >= 0.3:
            gen_txt = (f"{p_last} generates {bp_gen:.1f} BP/match, "
                       f"{bp_genq:.1f} once quality-adjusted for the servers he's faced")
        else:
            gen_txt = f"{p_last} generates {bp_gen:.1f} BP/match against this calibre of server"
        sentences.append(f"{sgw_txt}; {gen_txt}.")

    # 3. Match environment
    env_label = {
        "HIGH_BREAK": "high-break environment — both servers leaky",
        "SERVE_DOM":  "serve-dominant environment — breaks are at a premium",
        "RET_EDGE":   "returner-edge environment — return quality outpaces serve",
        "WEAK_SERVE": "weak-serve environment — breaks come freely",
        "STANDARD":   "standard environment — no extreme tilt",
    }.get(env, "standard environment")
    sentences.append(f"Match profile is a {env_label}.")

    # 4. Match format + expected sets
    if exp_sets is not None and comp:
        sentences.append(
            f"Format is {fmt_label} with {exp_sets:.1f} expected sets ({comp.lower()}) "
            f"— the longer the match, the more BP windows accumulate."
        )

    # H2H reference (optional)
    if h2h_summary and h2h_summary.get("bp_avg") is not None:
        bp_avg = h2h_summary["bp_avg"]
        sentences.append(
            f"In the H2H {p_last} has averaged {bp_avg:.1f} BPs won across "
            f"{h2h_summary.get('total', 0)} prior meetings."
        )

    # Data-limit acknowledgement
    limit_phrase = _recent_form_phrase(player_recent_meta, surface, player_name)
    if limit_phrase:
        sentences.append(limit_phrase.strip())

    # 5. Closing directional lean
    if prop_line > 0:
        if lean == "OVER":
            sentences.append(f"Line looks beatable Over {prop_line:.1f} BPs won.")
        elif lean == "UNDER":
            sentences.append(f"Line looks rich — lean Under {prop_line:.1f} BPs won.")
        else:
            sentences.append(f"No clean edge on the {prop_line:.1f} line — pass.")
    else:
        sentences.append(
            f"Lean is {lean.title()} on break points won." if lean in ("OVER", "UNDER")
            else "No directional edge on break points."
        )

    return " ".join(sentences[:6])


def _report_df(player_name, opponent_name, surface, court, projection,
               player_surface_stats, opponent_surface_stats,
               player_recent_meta, prop_line, last5_matches) -> str:
    """4-6 sentence double-faults-only scouting report."""
    p_last, o_last = _last_name(player_name), _last_name(opponent_name)

    p_dfs       = _fmt_num(player_surface_stats.get("double_faults"), ".1f")
    p_surf_n    = (player_recent_meta or {}).get("surface_n", 0)
    pressure    = projection.get("pressure_factor", 1.0) or 1.0
    o_ret2      = opponent_surface_stats.get("return_second_serve_pts_won")
    o_ret1      = opponent_surface_stats.get("return_first_serve_pts_won")
    lean        = projection.get("lean", "NEUTRAL")

    sentences = []

    # 1. Player DF rate on surface
    sample_note = f" across {p_surf_n} matches" if p_surf_n and p_surf_n >= 5 else ""
    window_phrase = _window_phrase(player_recent_meta)
    sentences.append(
        f"{player_name} averages {p_dfs} double faults per match on "
        f"{surface.lower()} {window_phrase}{sample_note}."
    )

    # 2. Opponent return pressure on 2nd serve
    if o_ret2 is not None:
        pressure_dir = (
            "applies real pressure on second serves"
            if o_ret2 > 53 else
            "doesn't apply much pressure on second serves"
            if o_ret2 < 48 else
            "applies average second-serve pressure"
        )
        sentences.append(
            f"{o_last} wins {o_ret2:.0f}% of return points on second serve — "
            f"{pressure_dir} ({(pressure - 1) * 100:+.0f}% pressure factor)."
        )
    elif o_ret1 is not None:
        sentences.append(
            f"{o_last}'s return game runs at {o_ret1:.0f}% on first serve — "
            f"applying a {(pressure - 1) * 100:+.0f}% pressure factor on second balls."
        )

    # 3. Surface-specific DF tendency
    surface_note = {
        "Clay":  "Clay gives servers more time to recover on the second ball — DFs slightly suppressed.",
        "Hard":  "Hard courts are neutral on second-serve risk.",
        "Grass": "Grass shortens points so servers push the second ball harder — DFs creep up.",
    }.get(surface, "")
    if surface_note:
        sentences.append(surface_note)

    # 4. Last 5 trend
    last5 = _last_n_values(last5_matches, "double_faults", 5)
    if last5 and prop_line > 0:
        over, under = _under_over_count(last5, prop_line)
        df_str = " ".join(str(int(v)) if v == int(v) else f"{v}" for v in last5)
        direction = "went over" if over >= under else "went under"
        sentences.append(
            f"Across {p_last}'s last 5 matches (any surface) he threw {df_str} double faults — "
            f"{max(over, under)} of 5 {direction} the {prop_line:.1f} line."
        )

    # Data-limit acknowledgement
    limit_phrase = _recent_form_phrase(player_recent_meta, surface, player_name)
    if limit_phrase:
        sentences.append(limit_phrase.strip())

    # 5. Closing directional lean
    if prop_line > 0:
        sentences.append(
            f"Lean is {lean.title()} {prop_line:.1f} double faults."
            if lean in ("OVER", "UNDER") else
            f"No directional edge on the {prop_line:.1f} double-fault line."
        )
    else:
        sentences.append(
            f"Lean is {lean.title()} on double faults."
            if lean in ("OVER", "UNDER") else
            "No directional edge on double faults."
        )

    return " ".join(sentences[:6])


def _report_total_games(player_name, opponent_name, surface, court, projection,
                        player_surface_stats, opponent_surface_stats,
                        player_recent_meta, prop_line, h2h_summary) -> str:
    """4-6 sentence total-games-only scouting report."""
    from src.constants import COURT_CPR, CPR_NEUTRAL
    p_last, o_last = _last_name(player_name), _last_name(opponent_name)

    p_1sw       = _fmt_num(projection.get("p1_srv") or player_surface_stats.get("first_serve_pts_won"), ".0f")
    o_1sw       = _fmt_num(projection.get("p2_srv") or opponent_surface_stats.get("first_serve_pts_won"), ".0f")
    ch          = projection.get("combined_hold")
    gps         = projection.get("games_per_set")
    exp_sets    = projection.get("expected_sets")
    comp        = projection.get("competitiveness")
    env         = projection.get("environment", "STANDARD")
    cpr         = projection.get("cpr", COURT_CPR.get(court, CPR_NEUTRAL))
    lean        = projection.get("lean", "NEUTRAL")

    sentences = []

    # 1. Hold rates for both
    sentences.append(
        f"On {surface.lower()}, {p_last} holds at {p_1sw}% on first serve and "
        f"{o_last} at {o_1sw}% — combined hold of {ch:.0f}%."
        if ch is not None else
        f"On {surface.lower()}, {p_last} holds at {p_1sw}% on first serve and "
        f"{o_last} at {o_1sw}%."
    )

    # 2. Match environment
    env_label = {
        "HIGH_BREAK": "high-break environment — sets run long with multiple breaks",
        "SERVE_DOM":  "serve-dominant environment — sets tend to go to tiebreaks",
        "RET_EDGE":   "returner-edge environment — return quality keeps sets tight",
        "WEAK_SERVE": "weak-serve environment — frequent breaks shorten sets",
        "STANDARD":   "standard environment — no extreme tilt on game volume",
    }.get(env, "standard environment")
    sentences.append(f"Match profile is a {env_label}.")

    # 3. Expected sets + projected games
    if exp_sets is not None and gps is not None and comp:
        sentences.append(
            f"Expected sets sits at {exp_sets:.1f} ({comp.lower()}) at "
            f"{gps:.1f} games per set."
        )

    # 4. H2H games avg + CPR effect
    extras = []
    if h2h_summary and h2h_summary.get("games_avg") is not None:
        ga = h2h_summary["games_avg"]
        extras.append(
            f"H2H on {surface.lower()} averages {ga:.1f} total games across "
            f"{h2h_summary.get('total', 0)} prior meetings"
        )
    if cpr <= 28:
        extras.append(f"slow {surface.lower()} (CPR {cpr}) adds to game volume")
    elif cpr >= 43:
        extras.append(f"fast court (CPR {cpr}) trims a fraction of a game per set")
    if extras:
        sentences.append(("; ".join(extras)).capitalize() + ".")

    # Data-limit acknowledgement
    limit_phrase = _recent_form_phrase(player_recent_meta, surface, player_name)
    if limit_phrase:
        sentences.append(limit_phrase.strip())

    # 5. Closing directional lean
    if prop_line > 0:
        sentences.append(
            f"Total games projects {lean.title()} {prop_line:.1f}."
            if lean in ("OVER", "UNDER") else
            f"No directional edge on {prop_line:.1f} total games."
        )
    else:
        sentences.append(
            f"Lean is {lean.title()} on total games." if lean in ("OVER", "UNDER")
            else "No directional edge on total games."
        )

    return " ".join(sentences[:6])


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
    player_recent_meta: dict = None,
    opponent_recent_meta: dict = None,
    prop_line: float = 0.0,
    player_surface_matches: list = None,
) -> str:
    """
    Prop-specific scouting report — 4 to 6 sentences max, every sentence
    carries a specific number or context point relevant to the selected prop.
    The closing sentence always states a directional lean.

    Reports are routed by prop_type so the bettor never sees stats irrelevant
    to what they're trying to decide. Aces reports do not discuss break
    points; total-games reports do not discuss ace rates; etc.

    Data-limitation acknowledgements are woven into the report naturally
    rather than appearing as a separate header — see _recent_form_phrase.
    """
    common_kwargs = dict(
        player_name=player_name,
        opponent_name=opponent_name,
        surface=surface,
        court=court,
        projection=projection,
        player_surface_stats=player_surface_stats or {},
        opponent_surface_stats=opponent_surface_stats or {},
        player_recent_meta=player_recent_meta,
        prop_line=prop_line or 0.0,
    )

    if prop_type == "Aces":
        return _report_aces(
            **common_kwargs,
            player_hand=player_hand,
            opponent_hand=opponent_hand,
            last5_matches=player_surface_matches or [],
        )
    if prop_type == "Double Faults":
        return _report_df(
            **common_kwargs,
            last5_matches=player_surface_matches or [],
        )
    if prop_type == "Total Games":
        return _report_total_games(
            **common_kwargs,
            h2h_summary=h2h_summary,
        )
    if prop_type == "Break Points Won":
        return _report_bp(
            **common_kwargs,
            h2h_summary=h2h_summary,
        )

    # Fallback for unknown prop types
    lean = projection.get("lean", "NEUTRAL")
    return f"{player_name} vs {opponent_name} on {surface}. Lean is {lean.title()}."
