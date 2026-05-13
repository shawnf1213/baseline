from src.constants import ATP_TOUR_AVERAGES, WTA_TOUR_AVERAGES

ARCHETYPE_SURFACE_EDGES = {
    ("Big Server", "Hard"): 0.06,
    ("Big Server", "Grass"): 0.08,
    ("Big Server", "Clay"): -0.04,
    ("Serve and Volleyer", "Grass"): 0.07,
    ("Serve and Volleyer", "Hard"): 0.03,
    ("Serve and Volleyer", "Clay"): -0.05,
    ("Counterpuncher", "Clay"): 0.06,
    ("Counterpuncher", "Hard"): 0.02,
    ("Counterpuncher", "Grass"): -0.03,
    ("Precision Baseliner", "Clay"): 0.04,
    ("Precision Baseliner", "Hard"): 0.04,
    ("Attacking Baseliner", "Hard"): 0.04,
    ("Attacking Baseliner", "Grass"): 0.03,
    ("Solid Baseliner", "Clay"): 0.03,
}


def _safe_rate(stats_dict: dict, surface: str, key: str = "win_rate", default: float = 50.0) -> float:
    surface_stats = stats_dict.get(surface, {})
    if not surface_stats:
        surface_stats = stats_dict.get("All", {})
    val = surface_stats.get(key, default)
    return val if val is not None else default


def calculate_win_probability(
    player_stats: dict,
    opponent_stats: dict,
    h2h_summary: dict,
    surface: str,
) -> dict:
    recent_p1 = _safe_rate(player_stats, "All") / 100
    recent_p2 = _safe_rate(opponent_stats, "All") / 100
    surf_p1 = _safe_rate(player_stats, surface) / 100
    surf_p2 = _safe_rate(opponent_stats, surface) / 100

    h2h_total = h2h_summary.get("total", 0)
    h2h_rate = 0.5
    if h2h_total > 0:
        h2h_rate = h2h_summary.get("p1_wins", 0) / h2h_total

    recent_match_avg = (recent_p1 + (1 - recent_p2)) / 2
    surface_match_avg = (surf_p1 + (1 - surf_p2)) / 2

    model_prob = (
        recent_match_avg * 0.40
        + surface_match_avg * 0.30
        + surf_p1 * 0.15
        + (1 - surf_p2) * 0.10
        + h2h_rate * 0.05
    )
    model_prob = max(0.05, min(0.95, model_prob))

    return {
        "model_probability": model_prob,
        "recent_match_avg": recent_match_avg,
        "surface_match_avg": surface_match_avg,
        "h2h_rate": h2h_rate,
        "components": {
            "Recent avg (40%)": round(recent_match_avg * 100, 1),
            "Surface avg (30%)": round(surface_match_avg * 100, 1),
            "Player surface (15%)": round(surf_p1 * 100, 1),
            "Opponent surface (10%)": round((1 - surf_p2) * 100, 1),
            "H2H avg (5%)": round(h2h_rate * 100, 1),
        },
    }


def implied_from_american(odds_str: str) -> float:
    try:
        val = float(str(odds_str).replace("+", "").strip())
        if val > 0:
            return 100 / (val + 100)
        else:
            return abs(val) / (abs(val) + 100)
    except (ValueError, TypeError):
        return 0.5


def american_from_probability(prob: float) -> str:
    if prob <= 0 or prob >= 1:
        return "N/A"
    if prob >= 0.5:
        return f"-{round(prob / (1 - prob) * 100)}"
    else:
        return f"+{round((1 - prob) / prob * 100)}"


def _archetype_surface_modifier(player_arch: str, opp_arch: str, surface: str) -> float:
    player_edge = ARCHETYPE_SURFACE_EDGES.get((player_arch, surface), 0)
    opp_edge = ARCHETYPE_SURFACE_EDGES.get((opp_arch, surface), 0)
    return max(-0.10, min(0.10, player_edge - opp_edge))


def calculate_value_bet(
    model_prob: float,
    implied_prob: float,
    player_arch: str,
    opp_arch: str,
    surface: str,
    recent_edge_strong: bool = False,
) -> dict:
    modifier = _archetype_surface_modifier(player_arch, opp_arch, surface)

    if recent_edge_strong:
        adjusted = model_prob + modifier * 0.3
    else:
        adjusted = model_prob + modifier

    adjusted = max(0.05, min(0.95, adjusted))
    edge = adjusted - implied_prob

    if edge > 0.05:
        lean = "OVER"
        confidence = min(95, int(abs(edge) * 250))
    elif edge < -0.05:
        lean = "UNDER"
        confidence = min(95, int(abs(edge) * 250))
    else:
        lean = "NEUTRAL"
        confidence = 50

    return {
        "lean": lean,
        "confidence": confidence,
        "model_probability": round(adjusted * 100, 1),
        "implied_probability": round(implied_prob * 100, 1),
        "edge": round(edge * 100, 1),
        "modifier_pct": round(modifier * 100, 1),
        "fair_odds": american_from_probability(adjusted),
    }
