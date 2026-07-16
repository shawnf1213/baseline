import logging
import statistics
from typing import Optional

logger = logging.getLogger(__name__)

VARIANCE_THRESHOLDS = {
    "aces":              (2.0, 4.0),
    "double_faults":     (1.0, 2.0),
    "bp_converted_count":(1.0, 2.0),
    "_total_games":      (3.0, 6.0),
    "total_games_won":   (3.0, 6.0),
}

# Per-prop scaling for the edge-to-variance grade. Aces are the reference (1.0);
# Break Points Won / Total Games lines sit structurally CLOSER to the projection
# (smaller raw ratios), so their ratios are scaled UP so a strong play in that prop
# grades comparably. Tunable as real per-prop ratio distributions accumulate.
PROP_EVR_SCALE = {
    "Aces":                    1.0,
    "Double Faults":           1.5,
    "Break Points Won":        1.6,
    "Total Games":             1.9,
    "Player Total Games Won":  1.5,
}

# Anchor points mapping a (prop-scaled) edge-to-variance ratio → confidence ceiling.
# Interpolated between anchors so the grade is continuous, not stepped. Raw ratio
# >= 2.5 bypasses this (absolute 90-95 override); 89 is the graded top so 90-95 is
# reserved for genuine multi-σ blowouts.
_EVR_ANCHORS = [(0.5, 72), (1.0, 80), (1.5, 85), (2.0, 88), (2.5, 89)]


def _evr_grade(x: float) -> int:
    """Piecewise-linear ceiling from a (prop-scaled) edge-to-variance ratio, so a
    ratio of 0.9 and 0.3 grade differently instead of binning to one number."""
    if x <= _EVR_ANCHORS[0][0]:
        return _EVR_ANCHORS[0][1]
    if x >= _EVR_ANCHORS[-1][0]:
        return _EVR_ANCHORS[-1][1]
    for (x0, y0), (x1, y1) in zip(_EVR_ANCHORS, _EVR_ANCHORS[1:]):
        if x0 <= x <= x1:
            return int(round(y0 + (y1 - y0) * (x - x0) / (x1 - x0)))
    return _EVR_ANCHORS[-1][1]


PROP_STAT_KEY = {
    "Aces":             "aces",
    "Double Faults":    "double_faults",
    "Total Games":      "_total_games",
    "Break Points Won": "bp_converted_count",
    "Player Total Games Won": "total_games_won",
}

# Per-prop confidence ceilings. Player Total Games Won is DERIVED from several
# models (combined games + break points + win-prob share + hold environment), so
# it carries compounded uncertainty — a large sample shouldn't push it to a
# near-lock. Cap it well below the 95 default.
PROP_CONFIDENCE_CEILING = {
    "Player Total Games Won": 80,
    # Total Games added 2026-07-15 from the games_per_set fit (FREEZE_LOG entry 2).
    # The fit measured, on 1,233 matches, that combined hold explains only
    # R^2 = 0.09-0.16 of games-per-set variance — residual sd ~1.2 games/set, i.e.
    # ~+/-2.8 games on a 2.3-set total. The model's Total Games projection is
    # barely better than the tour mean, which is exactly why books price this prop
    # -120/-120 on BOTH sides and why PrizePicks leans on it: it is intrinsically
    # near-coin-flip. A 90+ confidence on a statistic we explain 15% of is a claim
    # the data cannot support, so the ceiling says so. Same treatment as PTGW —
    # both are derived, compounded stats, and both cap at 80.
    "Total Games": 80,
}

# PTGW only: ceiling when the depth test fails (either side under 15 stat-rich
# surface matches). Sits below the 80 prop ceiling so sample depth still
# discriminates for a prop whose ceiling would otherwise flatten every play to 80.
_PTGW_SHALLOW_CEILING = 76

SECTION_LABELS = {
    "sample_size": "Sample Size",
    "h2h":         "H2H Data",
    "consistency": "Consistency",
    "recency":     "Recency Alignment",
    "opponent":    "Opponent Data",
    "venue":       "Venue Familiarity",
}


def _parse_score_games(score: str) -> Optional[float]:
    if not score or score == "—":
        return None
    total = 0
    for part in score.strip().split():
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                total += int(a) + int(b)
            except Exception:
                return None
    return float(total) if total > 0 else None


def _extract_series(matches: list, stat_key: str) -> list:
    if stat_key == "_total_games":
        return [v for v in (_parse_score_games(m.get("score", "")) for m in matches) if v is not None]
    return [m[stat_key] for m in matches if stat_key in m and m[stat_key] is not None]


def finalize_confidence(total, prop_type: str = "", data_ceiling: int = 95) -> int:
    """The SINGLE confidence floor/cap. Floor 25, cap 95 (minus any per-prop
    ceiling for derived props, minus the ``data_ceiling`` imposed by data quality
    / variance — Fixes B/C and the ace-variance cap). This is the ONLY place
    confidence is clamped; applied once as the final step after every modifier."""
    ceiling = min(95, data_ceiling if isinstance(data_ceiling, (int, float)) else 95)
    prop_ceiling = PROP_CONFIDENCE_CEILING.get(prop_type)
    if prop_ceiling is not None:
        ceiling = min(ceiling, prop_ceiling)
    try:
        t = round(total)
    except (TypeError, ValueError):
        t = 25
    return int(max(25, min(ceiling, t)))


def calculate_confidence(
    player_surface_matches: list,
    opponent_surface_matches: list,
    prop_type: str,
    has_h2h_surface: bool,
    has_h2h_other: bool,
    court: str = "",
    ta_career_surface_matches: int = 0,
    ss_recent_surface_matches: int = 0,
    opp_ta_career_matches: int = 0,
    p1_blended: dict = None,
    p2_blended: dict = None,
    projection: float = None,
    prop_line: float = None,
    p1_deep: bool = None,
    p2_deep: bool = None,
) -> dict:
    stat_key = PROP_STAT_KEY.get(prop_type, "aces")
    breakdown = {}
    total = 0

    # 1. Sample size — foundation of confidence.
    # Surface-specific match count is the primary signal, BUT grass (and to a
    # lesser extent clay) seasons are only 3-4 weeks/year, so a strong player
    # legitimately has few surface matches. We credit OVERALL career depth as a
    # partial backstop so a deep, in-form player isn't scored like an unknown
    # just because the surface window is short — the projection itself leans on
    # that all-surface data via the blended stats.
    # STAT-RICH count, not len(player_surface_matches). The raw list holds EVERY
    # surface match (score/opponent/timestamp), including ones with no parsed
    # statistics — Erjavec had 330 raw clay matches but only 38 with aces/DF/BP.
    # Scoring sample depth off the raw list credits matches the stat averages
    # never saw. Every count guard in this module now reads the same stat-rich
    # definition (n, o_n, p2_n) so they cannot disagree about what "usable" means.
    # The LISTS are still used for stat extraction (consistency/recency), where
    # stat-poor entries drop out naturally via _extract_series.
    n = ta_career_surface_matches
    overall_n = 0
    if p1_blended:
        overall_n = (p1_blended.get("overall_matches_played")
                     or p1_blended.get("_ss_career_matches") or 0)
    # Effective sample = surface matches + a discounted share of overall depth
    # (overall data is less surface-relevant, so it counts at ~25%).
    eff_n = n + min(overall_n, 60) * 0.25

    if eff_n < 5:
        sample_score, sample_label = 0, f"{n} surf / {overall_n} overall (stat-rich) —very limited data"
    elif eff_n <= 10:
        sample_score, sample_label = 20, f"{n} surf / {overall_n} overall (stat-rich) —small effective sample"
    elif eff_n <= 20:
        sample_score, sample_label = 35, f"{n} surf / {overall_n} overall (stat-rich) —moderate effective sample"
    elif eff_n <= 40:
        sample_score, sample_label = 50, f"{n} surf / {overall_n} overall (stat-rich) —good effective sample"
    else:
        sample_score, sample_label = 60, f"{n} surf / {overall_n} overall (stat-rich) —large effective sample"
    breakdown["sample_size"] = {"score": sample_score, "max": 60, "label": sample_label}
    total += sample_score

    if n == 0 and overall_n == 0:
        return {"confidence": finalize_confidence(total, prop_type, 65),
                "raw_total": total, "data_ceiling": 65, "cap_tag": "data-capped",
                "breakdown": breakdown}

    # 2. H2H bonus
    if has_h2h_surface:
        h2h_score, h2h_label = 15, "H2H data exists on this surface"
    elif has_h2h_other:
        h2h_score, h2h_label = 5, "H2H data exists on a different surface"
    else:
        h2h_score, h2h_label = 0, "No H2H data found"
    breakdown["h2h"] = {"score": h2h_score, "max": 15, "label": h2h_label}
    total += h2h_score

    # 3. Consistency — std dev over last 20 matches on surface
    last_20 = player_surface_matches[:20]
    vals_20 = _extract_series(last_20, stat_key)
    std_dev = None
    high_variance = False
    consistency_tier = None
    if len(vals_20) >= 3:
        std_dev = statistics.stdev(vals_20)
        low_t, high_t = VARIANCE_THRESHOLDS.get(stat_key, (2.0, 4.0))
        # Consolidated consistency score (was double-counted with main.py's
        # _consistency). Single application here on the designed range:
        # low variance +8, high variance −12.
        if std_dev < low_t:
            consistency_score = 8
            consistency_tier = "Consistent"
            consistency_label = f"Low variance (σ={std_dev:.1f}) — consistent output"
        elif std_dev < high_t:
            consistency_score = 0
            consistency_tier = "Moderate Variance"
            consistency_label = f"Medium variance (σ={std_dev:.1f})"
        else:
            consistency_score = -12
            consistency_tier = "High Variance"
            consistency_label = f"High variance (σ={std_dev:.1f}) — unpredictable"
            high_variance = True
    else:
        consistency_score = 0
        consistency_label = "Too few matches for variance analysis"
    breakdown["consistency"] = {"score": consistency_score, "max": 8,
                                "label": consistency_label, "tier": consistency_tier}
    total += consistency_score

    # 4. Recency alignment — last 5 vs overall surface average
    all_vals = _extract_series(player_surface_matches, stat_key)
    recent_vals = _extract_series(player_surface_matches[:5], stat_key)

    if len(recent_vals) >= 3 and len(all_vals) >= 5:
        overall_avg = statistics.mean(all_vals)
        recent_avg = statistics.mean(recent_vals)
        ref_std = std_dev if std_dev is not None and std_dev > 0 else 1.0
        # Count how many of the last 5 diverge from the player's own surface norm
        # by more than one std dev. Scaled penalty (designed): 3 diverging → −8,
        # 4+ diverging → −15. Alignment bonus reduced to +5 so the reward is
        # symmetric in magnitude with the smaller divergence step.
        diverge_n = sum(1 for v in recent_vals if abs(v - overall_avg) > ref_std)
        if diverge_n >= 4:
            recency_score = -15
            recency_label = (
                f"Recent form strongly diverges — {diverge_n} of last {len(recent_vals)} "
                f"off norm (last 5 avg {recent_avg:.1f} vs surface avg {overall_avg:.1f})"
            )
        elif diverge_n == 3:
            recency_score = -8
            recency_label = (
                f"Recent form diverges — {diverge_n} of last {len(recent_vals)} "
                f"off norm (last 5 avg {recent_avg:.1f} vs surface avg {overall_avg:.1f})"
            )
        else:
            recency_score = 5
            recency_label = (
                f"Recent form aligns — last 5 avg {recent_avg:.1f} vs surface avg {overall_avg:.1f}"
            )
    else:
        recency_score = 0
        recency_label = "Insufficient recent data for alignment check"
    breakdown["recency"] = {"score": recency_score, "max": 5, "label": recency_label}
    total += recency_score

    # 5. Opponent data quality
    # Stat-rich count (see the note on `n`). This previously read the RAW list and
    # printed e.g. "Strong opponent data (330 surface matches)" — awarding a full
    # +10 — for an opponent with 38 usable matches, or 1 during a stats outage.
    o_n = opp_ta_career_matches
    if o_n > 10:
        opp_score, opp_label = 10, f"Strong opponent data ({o_n} stat-rich surface matches)"
    elif o_n >= 5:
        opp_score, opp_label = 0, f"Moderate opponent data ({o_n} stat-rich surface matches)"
    else:
        opp_score, opp_label = -10, f"Limited opponent data ({o_n} stat-rich surface matches)"
    breakdown["opponent"] = {"score": opp_score, "max": 10, "label": opp_label}
    total += opp_score

    # 6. Venue familiarity — 3+ tracked matches at this specific court
    if court and court not in ("None", ""):
        court_lower = court.lower()
        venue_count = sum(
            1 for m in player_surface_matches
            if court_lower in m.get("tournament", "").lower()
        )
        if venue_count >= 3:
            venue_score = 5
            venue_label = f"Venue familiarity — {venue_count} matches at {court}"
        else:
            venue_score = 0
            venue_label = f"Limited venue history ({venue_count} matches at {court})"
    else:
        venue_score = 0
        venue_label = "No specific venue selected"
    breakdown["venue"] = {"score": venue_score, "max": 5, "label": venue_label}
    total += venue_score

    # ── 7. SS career surface match depth (primary data source) ───────────────
    # ta_career_surface_matches is now the SS career match count (renamed in
    # blended_stats for backward compat — see _ta_career_matches alias).
    ss_career = ta_career_surface_matches   # SS career matches on this surface
    if ss_career >= 30:
        ss_career_score = 8
        ss_career_label = f"SS career: {ss_career} surface matches — deep sample"
    elif ss_career >= 15:
        ss_career_score = 0
        ss_career_label = f"SS career: {ss_career} surface matches — adequate"
    elif ss_career >= 5:
        ss_career_score = -10
        ss_career_label = f"SS career: {ss_career} surface matches — thin"
    else:
        ss_career_score = -20
        ss_career_label = (
            f"SS career: {ss_career} surface matches — very thin"
            " (all-surface fallback applied)"
            if ss_career > 0 else
            "SS career: no surface matches found"
        )
    # Halve the surface-thinness penalty when overall career depth is strong.
    # The thin-surface count is already reflected in sample_size; a player with
    # 30+ overall matches isn't an unknown just because the grass window is
    # short, so we don't double-charge the same thinness at full weight.
    if ss_career_score < 0 and overall_n >= 25:
        ss_career_score = round(ss_career_score / 2)
        ss_career_label += " | softened (strong overall sample)"
    breakdown["ta_career"] = {"score": ss_career_score, "max": 8, "label": ss_career_label}
    total += ss_career_score

    # ── 8. SS last-5 surface match depth ──────────────────────────────────────
    ss_m = ss_recent_surface_matches   # stat-rich matches in last-5 log
    if ss_m >= 5:
        ss_score, ss_label = 5, f"SS last-5: {ss_m} stat-rich surface matches"
    elif ss_m >= 3:
        ss_score, ss_label = 0, f"SS last-5: {ss_m} surface matches — moderate"
    else:
        ss_score, ss_label = -8, f"SS last-5: {ss_m} surface matches — insufficient"
    breakdown["ss_recent"] = {"score": ss_score, "max": 5, "label": ss_label}
    total += ss_score

    # ── 9. TA handedness bonus + source agreement ──────────────────────────────
    agree_score = 0
    agree_label = "TA enrichment: not available"
    if p1_blended:
        ta_available = p1_blended.get("_ta_available", False)
        if ta_available:
            # TA confirms handedness: +5
            agree_score += 5
            agree_label = "TA handedness confirmed"

        # Both SS tiers (career vs 3yr) agree within 15% on aces → +3
        ss_career_matches_count = p1_blended.get("_ss_career_matches", 0)
        ss_3yr_matches_count    = p1_blended.get("_ss_3yr_matches", 0)
        if ss_career_matches_count >= 10 and ss_3yr_matches_count >= 5:
            agree_score += 3
            agree_label += " | SS career/3yr tiers agree"

    breakdown["source_agreement"] = {"score": agree_score, "max": 8, "label": agree_label}
    total += agree_score

    _ADJUST_KEYS = {"bonus_cap", "penalty_cap", "confidence_cap", "data_cap"}

    # ── Fix A — bonus STACKING cap +15 ────────────────────────────────────────
    # Multiple small DISCRETIONARY bonuses (consistency, recency-alignment, venue,
    # ss last-5, source agreement) could stack to rescue a play into the high 80s.
    # Cap their COMBINED positive contribution at +15 — the same discipline the
    # −40 penalty cap already applies to the negative side.
    # Deliberately EXCLUDED (these are FOUNDATIONAL data signals, not stackable
    # reward bonuses, and capping them would gut legitimately strong, deep-sample
    # plays like Djokovic — which must still earn high-80s):
    #   • sample_size  — the data foundation (0-60), kept per the Fix-D decision;
    #                    degraded data is handled by the data-quality ceiling.
    #   • h2h, opponent, ta_career — measured data-depth signals, not bonuses.
    _BONUS_KEYS = ("consistency", "recency", "venue", "ss_recent", "source_agreement")
    bonus_sum = sum(max(0, breakdown[k]["score"]) for k in _BONUS_KEYS if k in breakdown)
    if bonus_sum > 15:
        overflow = bonus_sum - 15
        total -= overflow
        breakdown["bonus_cap"] = {
            "score": -round(overflow), "max": 0,
            "label": f"Bonus cap — combined bonuses limited to +15 (was +{round(bonus_sum)})",
        }

    # ── Cap total negative penalties at −40 (unchanged) ───────────────────────
    penalty_sum = sum(
        b["score"] for k, b in breakdown.items()
        if k not in _ADJUST_KEYS and b.get("score", 0) < 0
    )
    if penalty_sum < -40:
        overflow = -40 - penalty_sum   # positive number to add back
        total += overflow
        breakdown["penalty_cap"] = {
            "score": round(overflow), "max": 0,
            "label": f"Penalty cap applied — combined penalties limited to −40 "
                     f"(was {penalty_sum})",
        }

    # ── Fixes B & C + ace-variance — DATA-QUALITY confidence ceiling ──────────
    # High confidence must be impossible on degraded data or a high-variance serve
    # prop, regardless of how favourable the numbers look.
    #   Fix B: either player on surface-fallback data → cap 75; either on very
    #          thin / tour-average data → cap 65.
    #   Fix C: 85+ requires BOTH players 15+ surface matches AND non-fallback,
    #          non-thin data — otherwise cap 84.
    #   Ace variance: a high-variance Aces / Double Faults prop caps at 80 — a big
    #          projected edge on a coin-flip stat is not "high confidence".
    p2_n = opp_ta_career_matches      # stat-rich (see the note on `n`)
    data_ceiling = 95
    _cap_reason = ""
    _cap_tag = None          # short display tag: data-capped / sample-capped / variance-capped
    for _bl in (p1_blended, p2_blended):
        if not _bl:
            continue
        _dq = _bl.get("_data_quality")
        if _dq == "thin":
            if data_ceiling > 65:
                data_ceiling, _cap_reason, _cap_tag = 65, "tour-average / very thin data", "data-capped"
        elif _bl.get("_surface_fallback"):
            if data_ceiling > 75:
                data_ceiling, _cap_reason, _cap_tag = 75, "surface-fallback data", "data-capped"
    # p1_deep / p2_deep come from the caller's depth hysteresis (a completed match
    # history can't shrink, so a count that drops is a degraded fetch — see
    # _deep_with_hysteresis in main.py). Fall back to the raw >=15 test when the
    # caller doesn't supply them, so this stays correct for direct callers/tests.
    _p1_deep = p1_deep if p1_deep is not None else (n >= 15)
    _p2_deep = p2_deep if p2_deep is not None else (p2_n >= 15)
    both_deep = (
        _p1_deep and _p2_deep
        and not (p1_blended or {}).get("_surface_fallback")
        and not (p2_blended or {}).get("_surface_fallback")
        and (p1_blended or {}).get("_data_quality") != "thin"
        and (p2_blended or {}).get("_data_quality") != "thin"
    )
    if not both_deep and data_ceiling > 84:
        data_ceiling, _cap_reason, _cap_tag = 84, "85+ needs 15+ surface matches both sides on non-fallback data", "sample-capped"

    # ── PTGW depth distinction (below its prop ceiling) ───────────────────────
    # Player Total Games Won is hard-capped at 80 by PROP_CONFIDENCE_CEILING, which
    # sits BELOW the 84 depth cap above — so that cap can never bind for this prop,
    # and a 5-surface-match player scored identically to a 39-match one. Re-introduce
    # the depth signal underneath the ceiling: full 80 only when BOTH sides are deep,
    # otherwise 76. With the POD bar at 80 this means only deep-data PTGW plays
    # qualify — the intended strictness for a prop compounded from several models.
    if prop_type == "Player Total Games Won" and not both_deep and data_ceiling > _PTGW_SHALLOW_CEILING:
        data_ceiling = _PTGW_SHALLOW_CEILING
        _cap_reason = "Player Total Games Won needs 15+ stat-rich surface matches both sides for 80"
        _cap_tag = "sample-capped"

    # ── EDGE-TO-VARIANCE RATIO — continuous, per-prop-scaled confidence grade ──
    # Confidence is graded by how far the line sits from the projection relative to
    # the stat's own variability: ratio = |projection − line| / σ. The grade is
    # CONTINUOUS (interpolated, not binned) so 0.9 and 0.3 read differently. Each
    # prop's ratio is scaled to its OWN distribution (BP / Total Games lines sit
    # structurally closer to projections than ace lines) so a top-quartile BP play
    # can reach the mid-80s even though its raw ratio looks modest next to an ace.
    # A RAW ratio ≥ 2.5 is an absolute override (any prop) → opens 90-95 (Badosa).
    # This grade is the NORMAL variance-based confidence, NOT a structural cap — so
    # it carries NO display label; only the data-quality ceilings above do.
    # PTGW is EXCLUDED from EVR grading (FREEZE exception): its distribution is
    # bimodal, so |projection − line| / σ — which assumes a unimodal spread around
    # the mean — is the wrong instrument (a "fat edge on the mean" is a disguised
    # moneyline bet). PTGW confidence is instead mapped from the scenario-mixture
    # P(over) in main.py. The data-level and PTGW 80/76 ceilings above STILL apply;
    # only the EVR ceiling is skipped here.
    _sigma = std_dev if (std_dev is not None and std_dev > 0) else None
    if (prop_type != "Player Total Games Won"
            and _sigma is not None
            and isinstance(projection, (int, float)) and isinstance(prop_line, (int, float))):
        raw_evr = abs(projection - prop_line) / _sigma
        if raw_evr >= 2.5:
            evr_ceiling = 95
        else:
            evr_ceiling = _evr_grade(raw_evr * PROP_EVR_SCALE.get(prop_type, 1.0))
        if evr_ceiling < data_ceiling:
            # EVR is the tighter constraint → normal grading, so drop the label.
            data_ceiling = evr_ceiling
            _cap_tag = None
            _cap_reason = f"edge/variance grade (ratio {raw_evr:.2f})"

    if data_ceiling < 95:
        breakdown["data_cap"] = {
            "score": 0, "max": 0, "tag": _cap_tag,
            "label": f"Data-quality ceiling {data_ceiling} — {_cap_reason}",
        }

    # ── Return the RAW (unclamped) base total plus a finalized value ──────────
    # The floor/cap is NOT applied here as the authoritative step: the API path
    # (main.py) adds more bonuses/penalties on top of raw_total and calls
    # finalize_confidence() ONCE at the very end. The "confidence" value below is
    # the finalized base — used by the standalone website UI, which applies no
    # further modifiers, so its single clamp is also its final step.
    raw_total = total
    confidence = finalize_confidence(raw_total, prop_type, data_ceiling)
    if confidence != int(round(raw_total)):
        breakdown["confidence_cap"] = {
            "score": confidence - int(round(raw_total)), "max": 0,
            "label": f"Floor/cap applied — base {int(round(raw_total))} → {confidence}",
        }
    logger.info(
        "CONF | prop=%s | base_total=%d | penalty_sum=%d | bonus_cap=%s | data_ceiling=%d | "
        "high_var=%s | base_final=%d | breakdown=%s",
        prop_type, total, penalty_sum, "bonus_cap" in breakdown, data_ceiling,
        high_variance, confidence, {k: v.get("score") for k, v in breakdown.items()},
    )
    return {"confidence": confidence, "raw_total": raw_total,
            "data_ceiling": data_ceiling, "cap_tag": _cap_tag, "breakdown": breakdown}
