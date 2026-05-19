COURT_CPR = {
    # ── ATP Hard ──────────────────────────────────────────────────────────────
    "Australian Open":                   40,
    "US Open":                           37,
    "Indian Wells":                      36,   # legacy name
    "Indian Wells Masters":              36,
    "Miami":                             35,
    "Miami Open":                        35,
    "Cincinnati":                        36,
    "Cincinnati Masters":                36,
    "Canada Montreal":                   35,   # legacy name
    "Canadian Open (Montreal/Toronto)":  35,
    "Vienna":                            38,
    "Vienna Open":                       38,
    "Basel":                             39,   # legacy name
    "Swiss Indoors Basel":               39,
    "Rotterdam":                         38,
    "Rotterdam Open":                    38,
    "Doha":                              36,   # legacy name
    "Qatar Open Doha":                   36,
    "Dubai":                             37,   # legacy name
    "Dubai Duty Free Championships":     37,
    "ATP Finals":                        38,   # legacy name
    "ATP Finals Turin":                  38,
    "Dallas Open":                       37,
    "Delray Beach Open":                 37,
    "Adelaide International":            38,
    "Auckland Open":                     37,
    "Acapulco Open":                     38,
    "Washington Citi Open":              37,
    "Winston-Salem Open":                36,
    "Tokyo Japan Open":                  38,
    "Shanghai Masters":                  36,
    "Paris Masters":                     39,
    "Stockholm Open":                    39,
    "Antwerp European Open":             39,
    "Challenger Hard (Generic)":         33,

    # ── ATP Clay ─────────────────────────────────────────────────────────────
    "Roland Garros":                     24,
    "Monte Carlo":                       25,   # legacy name
    "Monte Carlo Masters":               25,
    "Madrid":                            29,   # legacy name
    "Madrid Open":                       29,
    "Barcelona":                         24,   # legacy name
    "Barcelona Open":                    24,
    "Rome":                              24,   # legacy name
    "Italian Open Rome":                 24,
    "Hamburg":                           25,   # legacy name
    "Hamburg Open":                      25,
    "Geneva":                            24,   # legacy name (hard & clay share name — CPR differs by context)
    "Lyon":                              25,   # legacy name
    "Lyon Open":                         25,
    "Buenos Aires Open":                 25,
    "Rio Open":                          26,
    "Santiago Open":                     24,
    "Houston Clay":                      27,
    "Munich Open":                       28,
    "Estoril Open":                      27,
    "Marrakech Open":                    24,
    "Bastad Open":                       24,
    "Umag Open":                         23,
    "Gstaad Open":                       24,
    "Kitzbuhel Open":                    24,
    "Challenger Clay Europe (Generic)":  23,
    "Challenger Clay South America (Generic)": 24,
    "Bordeaux Challenger":               23,
    "Braunschweig Challenger":           23,
    "Valencia Challenger":               24,
    "Monza Challenger":                  23,
    "Aix-en-Provence Challenger":        24,
    "Sanremo Challenger":                23,
    "Geneva Challenger":                 24,

    # ── ATP Grass ────────────────────────────────────────────────────────────
    "Wimbledon":                         43,
    "Queens Club":                       44,   # legacy name
    "Queens Club Championships":         44,
    "Halle":                             44,
    "Stuttgart Grass":                   42,
    "Eastbourne":                        41,   # legacy name
    "Eastbourne International":          41,
    "Mallorca Championships":            42,
    "Hertogenbosch Open":                43,
    "Ilkley Challenger":                 40,
    "Nottingham Challenger":             41,

    # ── WTA Hard ─────────────────────────────────────────────────────────────
    "Australian Open WTA":               38,
    "US Open WTA":                       36,
    "Indian Wells WTA":                  35,
    "Miami Open WTA":                    34,
    "Cincinnati WTA":                    35,
    "Canadian Open WTA":                 34,
    "Wuhan Open":                        36,
    "China Open Beijing":                36,
    "WTA Finals":                        37,
    "Dubai WTA":                         36,
    "Doha WTA":                          35,
    "Adelaide WTA":                      37,
    "Auckland WTA":                      36,
    "Acapulco WTA":                      37,
    "San Jose WTA":                      36,
    "Washington WTA":                    36,
    "Tokyo Pan Pacific":                 37,
    "Osaka WTA":                         36,
    "Linz WTA":                          38,
    "Guadalajara WTA":                   36,
    "WTA 125 Hard (Generic)":            32,
    "Austin WTA 125":                    32,
    "Jiangxi Open WTA 125":              32,

    # ── WTA Clay ─────────────────────────────────────────────────────────────
    "Roland Garros WTA":                 23,
    "Madrid Open WTA":                   28,
    "Italian Open WTA Rome":             23,
    "Stuttgart WTA":                     27,
    "Hamburg WTA":                       24,
    "Prague Open WTA":                   24,
    "Rabat WTA":                         23,
    "Strasbourg WTA":                    24,
    "Warsaw WTA":                        24,
    "Budapest WTA":                      23,
    "Bastad WTA":                        23,
    "Palermo WTA":                       23,
    "San Jose Clay WTA":                 25,
    "Bogota WTA":                        23,
    "Trophee Clarins Paris WTA 125":     23,
    "Catalonia Open WTA 125":            23,
    "Huzhou Open WTA 125 Clay":          23,
    "Emilia-Romagna WTA 125 Clay":       23,
    "WTA 125 Clay (Generic)":            23,

    # ── WTA Grass ────────────────────────────────────────────────────────────
    "Wimbledon WTA":                     42,
    "Eastbourne WTA":                    40,
    "Birmingham WTA":                    41,
    "Hertogenbosch WTA":                 42,
    "Mallorca WTA":                      41,
    "Ilkley WTA 125 Grass":              39,
}

COURTS_BY_SURFACE = {
    "Hard":  ["Australian Open", "US Open", "Indian Wells Masters", "Miami Open",
              "Cincinnati Masters", "Canadian Open (Montreal/Toronto)", "Vienna Open",
              "Swiss Indoors Basel", "Rotterdam Open", "Qatar Open Doha",
              "Dubai Duty Free Championships", "ATP Finals Turin"],
    "Clay":  ["Roland Garros", "Monte Carlo Masters", "Madrid Open", "Barcelona Open",
              "Italian Open Rome", "Hamburg Open", "Lyon Open"],
    "Grass": ["Wimbledon", "Queens Club Championships", "Halle",
              "Stuttgart Grass", "Eastbourne International"],
}

CPR_NEUTRAL = 35

GENERIC_SURFACE_CPR = {"Hard": 36, "Clay": 24, "Grass": 43}
GENERIC_TIER_LABEL  = {"Hard": "Medium-Fast", "Clay": "Slow", "Grass": "Fast"}

ATP_TOUR_AVERAGES = {
    "ace_rate":                     10.0,
    "first_serve_pts_won":          75.0,
    "return_first_serve_pts_won":   32.0,
    "return_pts_won":               40.0,
    "bp_converted":                 45.0,
    "net_pts_won":                  60.0,
    "double_faults":                 3.0,
}

WTA_TOUR_AVERAGES = {
    "ace_rate":                     2.0,
    "first_serve_pts_won":          65.0,
    "return_first_serve_pts_won":   38.0,
    "return_pts_won":               45.0,
    "bp_converted":                 42.0,
    "net_pts_won":                  55.0,
    "double_faults":                4.0,
}

SURFACE_COLORS = {
    "Hard":    "#1565C0",
    "Clay":    "#BF360C",
    "Grass":   "#2E7D32",
    "Unknown": "#424242",
}

ARCHETYPE_COLORS = {
    "Big Server":          "#FF6D00",
    "Serve and Volleyer":  "#AA00FF",
    "Precision Baseliner": "#0091EA",
    "Attacking Baseliner": "#00E676",
    "Solid Baseliner":     "#FFD600",
    "Counterpuncher":      "#FF4444",
    "All-Court Player":    "#AAAAAA",
}
