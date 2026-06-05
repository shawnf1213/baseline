import logging
import statistics
from typing import Optional

logger = logging.getLogger(__name__)

VARIANCE_THRESHOLDS = {
    "aces":              (2.0, 4.0),
    "double_faults":     (1.0, 2.0),
    "bp_converted_count":(1.0, 2.0),
    "_total_games":      (3.0, 6.0),
}

PROP_STAT_KEY = {
    "Aces":             "aces",
    "Double Faults":    "double_faults",
    "Total Games":      "_total_games",
    "Break Points Won": "bp_converted_count",
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
        return {"confidence": 15, "breakdown": breakdown}

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
    if len(vals_20) >= 3:
        std_dev = statistics.stdev(vals_20)
        low_t, high_t = VARIANCE_THRESHOLDS.get(stat_key, (2.0, 4.0))
        if std_dev < low_t:
            consistency_score = 15
            consistency_label = f"Low variance (σ={std_dev:.1f}) — consistent output"
        elif std_dev < high_t:
            consistency_score = 8
            consistency_label = f"Medium variance (σ={std_dev:.1f})"
        else:
            consistency_score = 0
            consistency_label = f"High variance (σ={std_dev:.1f}) — unpredictable"
    else:
        consistency_score = 0
        consistency_label = "Too few matches for variance analysis"
    breakdown["consistency"] = {"score": consistency_score, "max": 15, "label": consistency_label}
    total += consistency_score

    # 4. Recency alignment — last 5 vs overall surface average
    all_vals = _extract_series(player_surface_matches, stat_key)
    recent_vals = _extract_series(player_surface_matches[:5], stat_key)

    if len(recent_vals) >= 3 and len(all_vals) >= 5:
        overall_avg = statistics.mean(all_vals)
        recent_avg = statistics.mean(recent_vals)
        ref_std = std_dev if std_dev is not None and std_dev > 0 else 1.0
        diff = abs(recent_avg - overall_avg)
        if diff <= ref_std:
            recency_score = 10
            recency_label = (
                f"Recent form aligns — last 5 avg {recent_avg:.1f} vs surface avg {overall_avg:.1f}"
            )
        else:
            recency_score = -10
            recency_label = (
                f"Recent form diverges — last 5 avg {recent_avg:.1f} vs surface avg {overall_avg:.1f}"
            )
    else:
        recency_score = 0
        recency_label = "Insufficient recent data for alignment check"
    breakdown["recency"] = {"score": recency_score, "max": 10, "label": recency_label}
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

    # ── Cap total negative penalties at −40 ───────────────────────────────────
    # A thin-grass player could stack ss_career −20, opponent −10, recency −10,
    # ss_recent −8 = −48 in penalties alone, crushing an otherwise-fine player
    # to the floor. Sum the negative-scoring components and, if they exceed −40,
    # add back the overflow so no single matchup loses more than 40 points to
    # penalties combined. Positive scores (sample size, H2H, consistency) are
    # untouched.
    penalty_sum = sum(
        b["score"] for b in breakdown.values() if b.get("score", 0) < 0
    )
    if penalty_sum < -40:
        overflow = -40 - penalty_sum   # positive number to add back
        total += overflow
        breakdown["penalty_cap"] = {
            "score": round(overflow), "max": 0,
            "label": f"Penalty cap applied — combined penalties limited to −40 "
                     f"(was {penalty_sum})",
        }

    # ── Confidence floor 25 (was 15) ──────────────────────────────────────────
    # The 15 floor was too punishing for WTA / grass props where surface
    # samples are structurally small. Only an absolute no-data case (handled by
    # the n == 0 early return above) should sit that low; everything else gets
    # at least 25.
    confidence = max(25, min(95, total))
    logger.info(
        "CONF | prop=%s | base_total=%d | penalty_sum=%d | final=%d | breakdown=%s",
        prop_type, total, penalty_sum, confidence,
        {k: v.get("score") for k, v in breakdown.items()},
    )
    return {"confidence": confidence, "breakdown": breakdown}
