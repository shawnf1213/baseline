COURT_CPR = {
    "Australian Open": 40,
    "US Open": 37,
    "Indian Wells": 36,
    "Miami": 35,
    "Cincinnati": 36,
    "Canada Montreal": 35,
    "Vienna": 38,
    "Basel": 39,
    "Rotterdam": 38,
    "Doha": 36,
    "Dubai": 37,
    "ATP Finals": 38,
    "Roland Garros": 24,
    "Monte Carlo": 25,
    "Madrid": 29,
    "Barcelona": 24,
    "Rome": 24,
    "Hamburg": 25,
    "Geneva": 24,
    "Lyon": 25,
    "Wimbledon": 43,
    "Queens Club": 44,
    "Halle": 44,
    "Stuttgart Grass": 42,
    "Eastbourne": 41,
}

COURTS_BY_SURFACE = {
    "Hard": [
        "Australian Open", "US Open", "Indian Wells", "Miami", "Cincinnati",
        "Canada Montreal", "Vienna", "Basel", "Rotterdam", "Doha", "Dubai", "ATP Finals",
    ],
    "Clay": [
        "Roland Garros", "Monte Carlo", "Madrid", "Barcelona", "Rome",
        "Hamburg", "Geneva", "Lyon",
    ],
    "Grass": [
        "Wimbledon", "Queens Club", "Halle", "Stuttgart Grass", "Eastbourne",
    ],
}

CPR_NEUTRAL = 35

GENERIC_SURFACE_CPR = {"Hard": 36, "Clay": 24, "Grass": 43}
GENERIC_TIER_LABEL = {"Hard": "Medium-Fast", "Clay": "Slow", "Grass": "Fast"}

ATP_TOUR_AVERAGES = {
    "ace_rate": 10.0,
    "first_serve_pts_won": 75.0,
    "return_first_serve_pts_won": 32.0,
    "return_pts_won": 40.0,
    "bp_converted": 45.0,
    "net_pts_won": 60.0,
    "double_faults": 3.0,
}

WTA_TOUR_AVERAGES = {
    "ace_rate": 2.0,
    "first_serve_pts_won": 65.0,
    "return_first_serve_pts_won": 38.0,
    "return_pts_won": 45.0,
    "bp_converted": 42.0,
    "net_pts_won": 55.0,
    "double_faults": 4.0,
}

SURFACE_COLORS = {
    "Hard": "#1565C0",
    "Clay": "#BF360C",
    "Grass": "#2E7D32",
    "Unknown": "#424242",
}

ARCHETYPE_COLORS = {
    "Big Server": "#FF6D00",
    "Serve and Volleyer": "#AA00FF",
    "Precision Baseliner": "#0091EA",
    "Attacking Baseliner": "#00E676",
    "Solid Baseliner": "#FFD600",
    "Counterpuncher": "#FF4444",
    "All-Court Player": "#AAAAAA",
}
