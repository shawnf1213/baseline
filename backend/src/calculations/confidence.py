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
}

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
    n = len(player_surface_matches)
    overall_n = 0
    if p1_blended:
        overall_n = (p1_blended.get("overall_matches_played")
                     or p1_blended.get("_ss_career_matches") or 0)
    # Effective sample = surface matches + a discounted share of overall depth
    # (overall data is less surface-relevant, so it counts at ~25%).
    eff_n = n + min(overall_n, 60) * 0.25

    if eff_n < 5:
        sample_score, sample_label = 0, f"{n} surf / {overall_n} overall — very limited data"
    elif eff_n <= 10:
        sample_score, sample_label = 20, f"{n} surf / {overall_n} overall — small effective sample"
    elif eff_n <= 20:
        sample_score, sample_label = 35, f"{n} surf / {overall_n} overall — moderate effective sample"
    elif eff_n <= 40:
        sample_score, sample_label = 50, f"{n} surf / {overall_n} overall — good effective sample"
    else:
        sample_score, sample_label = 60, f"{n} surf / {overall_n} overall — large effective sample"
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
    o_n = len(opponent_surface_matches)
    if o_n > 10:
        opp_score, opp_label = 10, f"Strong opponent data ({o_n} surface matches)"
    elif o_n >= 5:
        opp_score, opp_label = 0, f"Moderate opponent data ({o_n} surface matches)"
    else:
        opp_score, opp_label = -10, f"Limited opponent data ({o_n} surface matches)"
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
    p2_n = len(opponent_surface_matches)
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
    both_deep = (
        n >= 15 and p2_n >= 15
        and not (p1_blended or {}).get("_surface_fallback")
        and not (p2_blended or {}).get("_surface_fallback")
        and (p1_blended or {}).get("_data_quality") != "thin"
        and (p2_blended or {}).get("_data_quality") != "thin"
    )
    if not both_deep and data_ceiling > 84:
        data_ceiling, _cap_reason, _cap_tag = 84, "85+ needs 15+ surface matches both sides on non-fallback data", "sample-capped"
    if prop_type in ("Aces", "Double Faults") and high_variance and data_ceiling > 80:
        data_ceiling, _cap_reason, _cap_tag = 80, f"high-variance {prop_type.lower()} (σ) — coin-flip stat", "variance-capped"
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
