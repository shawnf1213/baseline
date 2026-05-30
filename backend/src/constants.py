"""
Court speed data — String Tension ST Pace Index values.

ST Pace Index is computed from real match data (ace rates + break-point
defence vs career baselines), calibrated against the ATP published CPI.
Source: stringtension.com

Speed-tier thresholds (ST / ATP scale):
  < 25         Very Slow
  25 – 32      Slow
  33 – 38      Average
  39 – 44      Fast
  > 44         Very Fast

CPR_NEUTRAL = 35 (mid-Average range). All prop formula adjustments are
relative to this neutral point, so the sign/direction of each factor
remains correct — only the magnitude changes with new court values.
"""

# ---------------------------------------------------------------------------
# Speed tier helper (used by props.py, main.py, and the test endpoint)
# ---------------------------------------------------------------------------
def get_speed_tier(cpr: float) -> str:
    if cpr < 25:  return "Very Slow"
    if cpr <= 32: return "Slow"
    if cpr <= 38: return "Average"
    if cpr <= 44: return "Fast"
    return "Very Fast"


# ---------------------------------------------------------------------------
# Year-over-year reference — courts with confirmed multi-year ST data.
# Used to show bettors when this year's surface plays significantly
# differently from what they may expect from historical mental models.
# Any gap >= 5 points triggers a YoY indicator in the UI.
# ---------------------------------------------------------------------------
ST_PACE_PREVIOUS_YEAR = {
    # Roland Garros dramatically faster in 2026 (new Dunlop ball)
    "Roland Garros":     {"prev": 24.2, "prev_year": 2025},
    "Roland Garros WTA": {"prev": 24.2, "prev_year": 2025},
}
ST_YOY_THRESHOLD = 5.0   # minimum change to show the indicator


# ---------------------------------------------------------------------------
# Court pace index — String Tension ST Pace Index values (confirmed)
# or best estimates where ST data is not yet published.
#
# Confirmed ST values are labelled [ST confirmed].
# Estimate values are labelled [ST estimate].
# ---------------------------------------------------------------------------
COURT_CPR = {
    # ── ATP Hard ──────────────────────────────────────────────────────────────
    "Australian Open":                   40,    # [ST estimate — no confirmed ST yet; keep prior value]
    "US Open":                           42.8,  # [ST confirmed, 2025]
    "Indian Wells":                      35.4,  # [ST confirmed, 2023] legacy name
    "Indian Wells Masters":              35.4,  # [ST confirmed, 2023]
    "Miami":                             40.6,  # [ST confirmed, 2023] legacy name
    "Miami Open":                        40.6,  # [ST confirmed, 2023]
    "Cincinnati":                        38,    # [ST estimate]
    "Cincinnati Masters":                38,    # [ST estimate]
    "Canada Montreal":                   38,    # [ST estimate] legacy name
    "Canadian Open (Montreal/Toronto)":  38,    # [ST estimate]
    "Vienna":                            37,    # [ST estimate]
    "Vienna Open":                       37,    # [ST estimate]
    "Basel":                             38,    # [ST estimate] legacy name
    "Swiss Indoors Basel":               38,    # [ST estimate]
    "Rotterdam":                         37,    # [ST estimate]
    "Rotterdam Open":                    37,    # [ST estimate]
    "Doha":                              35,    # [ST estimate] legacy name
    "Qatar Open Doha":                   35,    # [ST estimate]
    "Dubai":                             36,    # [ST estimate] legacy name
    "Dubai Duty Free Championships":     36,    # [ST estimate]
    "ATP Finals":                        39,    # [ST estimate] legacy name
    "ATP Finals Turin":                  39,    # [ST estimate]
    "Paris Masters":                     39,    # [ST estimate]
    "Paris Bercy":                       39,    # [ST estimate] legacy alias
    "Dallas Open":                       37,    # [ST estimate]
    "Delray Beach Open":                 37,    # [ST estimate]
    "Adelaide International":            38,    # [ST estimate]
    "Auckland Open":                     37,    # [ST estimate]
    "Acapulco Open":                     38,    # [ST estimate]
    "Washington Citi Open":              37,    # [ST estimate]
    "Winston-Salem Open":                36,    # [ST estimate]
    "Tokyo Japan Open":                  38,    # [ST estimate]
    "Shanghai Masters":                  36,    # [ST estimate]
    "Stockholm Open":                    39,    # [ST estimate]
    "Antwerp European Open":             39,    # [ST estimate]
    "Challenger Hard (Generic)":         36,    # [ST estimate — user specified]

    # ── ATP Clay ─────────────────────────────────────────────────────────────
    "Roland Garros":                     37.7,  # [ST confirmed, 2026 — significantly faster than 2025 (24.2)]
    "Monte Carlo":                       30.4,  # [ST confirmed, 2026] legacy name
    "Monte Carlo Masters":               30.4,  # [ST confirmed, 2026]
    "Madrid":                            31.9,  # [ST confirmed, 2026] legacy name
    "Madrid Open":                       31.9,  # [ST confirmed, 2026]
    "Barcelona":                         27.2,  # [ST confirmed, 2026] legacy name
    "Barcelona Open":                    27.2,  # [ST confirmed, 2026]
    "Rome":                              29.6,  # [ST confirmed, 2026] legacy name
    "Italian Open Rome":                 29.6,  # [ST confirmed, 2026]
    "Hamburg":                           28.4,  # [ST confirmed, 2026] legacy name
    "Hamburg Open":                      28.4,  # [ST confirmed, 2026]
    "Munich":                            29.1,  # [ST confirmed, 2026] legacy name
    "Munich Open":                       29.1,  # [ST confirmed, 2026]
    "Geneva":                            31.2,  # [ST confirmed, 2026] ATP clay legacy name
    "Geneva Open":                       31.2,  # [ST confirmed, 2026]
    "Lyon":                              26,    # [ST estimate — no confirmed value]
    "Lyon Open":                         26,    # [ST estimate]
    "Buenos Aires Open":                 25,    # [ST estimate]
    "Rio Open":                          26,    # [ST estimate]
    "Santiago Open":                     24,    # [ST estimate]
    "Houston Clay":                      27,    # [ST estimate]
    "Estoril Open":                      27,    # [ST estimate]
    "Marrakech Open":                    24,    # [ST estimate]
    "Bastad Open":                       24,    # [ST estimate]
    "Umag Open":                         23,    # [ST estimate]
    "Gstaad Open":                       24,    # [ST estimate]
    "Kitzbuhel Open":                    24,    # [ST estimate]
    "Challenger Clay Europe (Generic)":  26,    # [ST estimate — user specified 26]
    "Challenger Clay South America (Generic)": 26,
    "Bordeaux Challenger":               24,    # [ST estimate]
    "Braunschweig Challenger":           24,    # [ST estimate]
    "Valencia Challenger":               24,    # [ST estimate]
    "Monza Challenger":                  24,    # [ST estimate]
    "Aix-en-Provence Challenger":        24,    # [ST estimate]
    "Sanremo Challenger":                24,    # [ST estimate]
    "Geneva Challenger":                 26,    # [ST estimate]

    # ── ATP Grass ────────────────────────────────────────────────────────────
    "Wimbledon":                         36.1,  # [ST confirmed, 2025 — significantly slower than prior est. 43]
    "Queens Club":                       38,    # [ST estimate] legacy name
    "Queens Club Championships":         38,    # [ST estimate]
    "Halle":                             38,    # [ST estimate]
    "Stuttgart Grass":                   37,    # [ST estimate]
    "Eastbourne":                        36,    # [ST estimate] legacy name
    "Eastbourne International":          36,    # [ST estimate]
    "Mallorca Championships":            37,    # [ST estimate]
    "Hertogenbosch Open":                38,    # [ST estimate]
    "Ilkley Challenger":                 36,    # [ST estimate]
    "Nottingham Challenger":             36,    # [ST estimate]

    # ── WTA Hard ─────────────────────────────────────────────────────────────
    # Roland Garros and Wimbledon WTA share the same court as ATP — same ST values.
    # Other WTA hardcourt values are adjusted ~1-2 slower than ATP equivalents
    # (women's game generates fewer aces, calibration is slightly different).
    "Australian Open WTA":               38,
    "US Open WTA":                       41.5,  # same venue, slightly adjusted for WTA
    "Indian Wells WTA":                  34.5,
    "Miami Open WTA":                    39.5,
    "Cincinnati WTA":                    37,
    "Canadian Open WTA":                 37,
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
    "WTA 125 Hard (Generic)":            36,    # [ST estimate — user specified 36]
    "Austin WTA 125":                    36,
    "Jiangxi Open WTA 125":              36,

    # ── WTA Clay ─────────────────────────────────────────────────────────────
    "Roland Garros WTA":                 37.7,  # [ST confirmed, 2026 — same court as ATP]
    "Madrid Open WTA":                   31.0,
    "Italian Open WTA Rome":             28.5,
    "Stuttgart WTA":                     27,
    "Hamburg WTA":                       27.5,
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
    "WTA 125 Clay (Generic)":            26,    # [ST estimate — user specified 26]

    # ── WTA Grass ────────────────────────────────────────────────────────────
    "Wimbledon WTA":                     36.1,  # [ST confirmed, 2025 — same court as ATP]
    "Eastbourne WTA":                    36,
    "Birmingham WTA":                    37,
    "Hertogenbosch WTA":                 38,
    "Mallorca WTA":                      37,
    "Ilkley WTA 125 Grass":              36,
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

# Generic surface defaults for when no specific court is selected
GENERIC_SURFACE_CPR   = {"Hard": 36, "Clay": 26, "Grass": 36}
GENERIC_TIER_LABEL    = {"Hard": "Average", "Clay": "Slow", "Grass": "Average"}

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
