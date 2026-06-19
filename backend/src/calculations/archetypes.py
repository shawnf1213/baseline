from src.constants import ATP_TOUR_AVERAGES, WTA_TOUR_AVERAGES, ARCHETYPE_COLORS

# Big Server gate — calibrated against real per-match data (`aces` here is
# aces PER MATCH, not an ace percentage, consistent with how every other stat
# in this module and the tour averages are expressed).
#
# The previous gate (aces > 18 and first_serve_pts_won > 78) only cleared
# Isner-tier servers. Measured ATP All-surface aces/match: Isner 21.5,
# Opelka 16.1, Mpetshi Perricard 14.5, Hurkacz 12.2, Shelton 11.0 — all
# unanimous big servers, yet only Isner passed >18. Hurkacz/Shelton also fell
# just under the >78 first-serve gate (~77.5). Baseliners sit far lower on aces
# (Alcaraz 5.3, Sinner 7.2), so the ace gate is the clean discriminator.
#
# WTA serves produce far fewer aces, so its thresholds scale down accordingly.
_BIG_SERVER = {
    "ATP": {"aces_pm": 10.0, "first_won": 76.0, "ret_first": 40.0},
    "WTA": {"aces_pm": 5.0,  "first_won": 68.0, "ret_first": 42.0},
}


def classify_archetype(stats: dict, tour: str = "ATP") -> str:
    avgs = ATP_TOUR_AVERAGES if tour == "ATP" else WTA_TOUR_AVERAGES

    ace_rate = stats.get("aces") or 0
    first_serve_pts_won = stats.get("first_serve_pts_won") or 0
    return_first = stats.get("return_first_serve_pts_won") or 0
    bp_converted = stats.get("bp_converted") or 0
    net_pts_won = stats.get("net_pts_won") or 0

    if ace_rate == 0 and first_serve_pts_won == 0:
        return "All-Court Player"

    bs = _BIG_SERVER.get(tour, _BIG_SERVER["ATP"])
    if (ace_rate > bs["aces_pm"]
            and first_serve_pts_won > bs["first_won"]
            and return_first < bs["ret_first"]):
        return "Big Server"

    if (ace_rate > avgs["ace_rate"] and
            net_pts_won > avgs["net_pts_won"] and
            return_first < 43):
        return "Serve and Volleyer"

    if (first_serve_pts_won > avgs["first_serve_pts_won"] and
            8 <= ace_rate <= 18 and
            return_first > 44 and
            bp_converted > avgs["bp_converted"]):
        return "Precision Baseliner"

    if (first_serve_pts_won > avgs["first_serve_pts_won"] and
            return_first > 45 and
            ace_rate > avgs["ace_rate"]):
        return "Attacking Baseliner"

    if (return_first > 45 and
            first_serve_pts_won <= avgs["first_serve_pts_won"] and
            ace_rate <= avgs["ace_rate"]):
        return "Solid Baseliner"

    if (return_first > 47 and
            bp_converted > avgs["bp_converted"] and
            first_serve_pts_won < avgs["first_serve_pts_won"]):
        return "Counterpuncher"

    return "All-Court Player"


def get_archetype_color(archetype: str) -> str:
    return ARCHETYPE_COLORS.get(archetype, "#AAAAAA")


def get_archetype_description(archetype: str) -> str:
    descriptions = {
        "Big Server": "Dominates via serve power with elite ace rates and first-serve dominance.",
        "Serve and Volleyer": "Combines strong serve with aggressive net approach.",
        "Precision Baseliner": "Consistent server who blends accuracy with solid return game.",
        "Attacking Baseliner": "Offensive from both wings with strong serve and return.",
        "Solid Baseliner": "Steady defender who relies on return game and consistency.",
        "Counterpuncher": "Elite returner who converts pressure into break opportunities.",
        "All-Court Player": "Balanced profile without a dominant signature strength.",
    }
    return descriptions.get(archetype, "")
