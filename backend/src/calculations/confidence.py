import statistics
from typing import Optional

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
) -> dict:
    stat_key = PROP_STAT_KEY.get(prop_type, "aces")
    breakdown = {}
    total = 0

    # 1. Sample size — foundation of confidence
    n = len(player_surface_matches)
    if n < 5:
        sample_score, sample_label = 0, f"{n} matches — very limited data"
    elif n <= 10:
        sample_score, sample_label = 20, f"{n} matches — small sample"
    elif n <= 20:
        sample_score, sample_label = 35, f"{n} matches — moderate sample"
    elif n <= 40:
        sample_score, sample_label = 50, f"{n} matches — good sample"
    else:
        sample_score, sample_label = 60, f"{n} matches — large sample"
    breakdown["sample_size"] = {"score": sample_score, "max": 60, "label": sample_label}
    total += sample_score

    if n == 0:
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

    confidence = max(15, min(95, total))
    return {"confidence": confidence, "breakdown": breakdown}
