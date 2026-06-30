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

    # ── ATP Grass (ST Pace Index, confirmed/estimated) ─────────────────────────
    # Per Tennis Abstract Serve Impact: Stuttgart is the FASTEST grass on tour
    # (faster than Wimbledon), then Halle ~ Queens, then the rest.
    "Wimbledon":                         36.1,  # [ST confirmed 2025] Average
    "Stuttgart":                         40,    # [ST est] Fastest grass (Serve Impact highest) — Fast
    "Stuttgart Grass":                   40,    # legacy/alt name
    "Halle":                             38,    # [ST est] Serve Impact 1.10
    "Halle Open":                        38,    # alt name
    "Queens Club":                       37,    # [ST est] Serve Impact 1.08, legacy name
    "Queens Club Championships":         37,    # [ST est]
    "s-Hertogenbosch":                   36,    # [ST est] Libema Open
    "Hertogenbosch Open":                36,    # [ST est] alt name
    "Libema Open":                       36,    # alt name
    "Mallorca":                          36,    # [ST est] ~Wimbledon speed
    "Mallorca Championships":            36,    # alt name
    "Eastbourne":                        35,    # [ST est] slightly slower than Wimbledon, legacy name
    "Eastbourne International":          35,    # [ST est]
    "Birmingham":                        34,    # [ST est] Challenger 125 grass, Edgbaston Priory
    "Birmingham Challenger":             34,    # alt name
    "Ilkley Challenger":                 34,    # [ST est]
    "Nottingham":                        34,    # [ST est] Rothesay Open Nottingham, ATP 125 / WTA 250
    "Nottingham Open":                   34,    # alt name
    "Rothesay Open Nottingham":          34,    # full name
    "Nottingham Challenger":             34,    # legacy alt name

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

    # ── WTA Grass (ST Pace Index — same venues as ATP) ─────────────────────────
    "Wimbledon WTA":                     36.1,  # [ST confirmed 2025] same court as ATP
    "Queens Club WTA":                   37,    # [ST est] WTA 500 — women play the week before men (Jun 8 2026)
    "Bad Homburg WTA":                   36,    # [ST est] WTA 500
    "Eastbourne WTA":                    35,    # [ST est]
    "Birmingham WTA":                    34,    # [ST est] WTA 125, Edgbaston Priory (same venue as ATP)
    "s-Hertogenbosch WTA":               36,    # [ST est] Libema Open
    "Hertogenbosch WTA":                 36,    # alt name
    "Mallorca WTA":                      36,    # [ST est]
    "Ilkley WTA 125 Grass":              34,    # [ST est]
    "Nottingham WTA":                    34,    # [ST est] Rothesay Open Nottingham, WTA 250 (same venue as ATP 125)
    "Nottingham Open WTA":               34,    # alt name
    "Berlin WTA":                        35,    # [ST est] bett1open Berlin, WTA 500 on grass (LTTC Rot-Weiss)
    "bett1open":                         35,    # alt name
    "bett1open Berlin":                  35,    # alt name
}

COURTS_BY_SURFACE = {
    "Hard":  ["Australian Open", "US Open", "Indian Wells Masters", "Miami Open",
              "Cincinnati Masters", "Canadian Open (Montreal/Toronto)", "Vienna Open",
              "Swiss Indoors Basel", "Rotterdam Open", "Qatar Open Doha",
              "Dubai Duty Free Championships", "ATP Finals Turin"],
    "Clay":  ["Roland Garros", "Monte Carlo Masters", "Madrid Open", "Barcelona Open",
              "Italian Open Rome", "Hamburg Open", "Lyon Open"],
    "Grass": ["Wimbledon", "Stuttgart", "Halle", "Queens Club Championships",
              "s-Hertogenbosch", "Mallorca", "Eastbourne International",
              "Birmingham", "Nottingham"],
}

CPR_NEUTRAL = 35

# Generic surface defaults for when no specific court is selected
GENERIC_SURFACE_CPR   = {"Hard": 36, "Clay": 26, "Grass": 34}
GENERIC_TIER_LABEL    = {"Hard": "Average", "Clay": "Slow", "Grass": "Average"}


def _norm_court(s: str) -> str:
    """Lowercase, accent-fold, drop ATP/WTA tour tags and punctuation."""
    import re as _re
    import unicodedata as _ud
    s = "".join(c for c in _ud.normalize("NFKD", s or "") if not _ud.combining(c))
    s = _re.sub(r"\b(atp|wta)\b", " ", s.lower())
    return _re.sub(r"[^a-z0-9 ]", " ", s).strip()


# ── Indoor hard-court tournaments (NEW SIGNAL 1) ─────────────────────────────
# Indoor hard plays measurably faster than outdoor hard — no wind, truer
# bounce, harder-to-read serve in artificial light — which favours servers.
# This is an ADDITIVE flag layered on top of COURT_CPR; it does NOT change any
# CPR value. Normalised name fragments (clay/grass are always outdoor).
INDOOR_TOURNAMENTS = (
    # NB: _norm_court strips the "atp"/"wta" tag, so "ATP Finals" normalises to
    # "finals" — match on "finals" (year-end finals are indoor) not "atp finals".
    "australian open", "paris", "finals", "vienna", "basel", "rotterdam",
    "dallas", "doha", "dubai", "antwerp", "sofia", "montpellier",
)


def is_indoor_court(court_name: str) -> bool:
    """True when the (resolved) tournament name is one of the indoor hard-court
    events. Caller must additionally gate on surface == 'Hard' before applying
    the indoor serve adjustment / badge."""
    n = _norm_court(court_name or "")
    if not n:
        return False
    return any(frag in n for frag in INDOOR_TOURNAMENTS)


def resolve_court_name(raw: str, tour: str = "ATP") -> str:
    """Map a free-form tournament name (e.g. Sofascore's 'Bad Homburg, Germany')
    to a canonical COURT_CPR key (e.g. 'Bad Homburg WTA') so the right ST Pace
    Index is used. Exact COURT_CPR keys pass straight through unchanged, so the
    website and /prop (which already send canonical keys) are unaffected.

    The city/core is taken from the part before the first comma, then matched
    against COURT_CPR keys; when both an ATP and a WTA variant exist (e.g.
    'Wimbledon' vs 'Wimbledon WTA'), the one matching ``tour`` is preferred.
    Returns ``raw`` unchanged if nothing matches (downstream then uses the
    generic surface default).
    """
    if not raw or raw in ("None",):
        return ""
    if raw in COURT_CPR:            # already canonical
        return raw
    core = _norm_court(raw.split(",")[0])
    if not core:
        return raw
    is_wta = (tour or "").upper() == "WTA"
    matches = []
    for key in COURT_CPR:
        kcore = _norm_court(key)
        if kcore and (kcore == core or core.startswith(kcore + " ")
                      or kcore.startswith(core + " ") or core == kcore):
            matches.append(key)
    if not matches:
        return raw
    wta_keys = [k for k in matches if k.strip().endswith("WTA")]
    atp_keys = [k for k in matches if not k.strip().endswith("WTA")]
    if is_wta and wta_keys:
        return wta_keys[0]
    if not is_wta and atp_keys:
        return atp_keys[0]
    return matches[0]

# ── Tour averages — single source of truth for tour-relative classification ──
# Existing keys (ace_rate, first_serve_pts_won, return_first_serve_pts_won,
# return_pts_won, bp_converted, net_pts_won, double_faults) are KEPT as-is for
# archetype + value-bet calibration. The keys below were added so serve-quality
# tiers, the BP serve adjustment, and games-won displays measure each tour
# against its OWN baseline (ATP serves much bigger than WTA). Update here only.
ATP_TOUR_AVERAGES = {
    "ace_rate":                     10.0,
    "first_serve_pts_won":          75.0,
    "return_first_serve_pts_won":   32.0,
    "return_pts_won":               40.0,
    "bp_converted":                 45.0,
    "net_pts_won":                  60.0,
    "double_faults":                 3.0,
    # ── tour-relative serve/return baselines (Step 1) ──
    "service_games_won":            64.0,
    "return_games_won":             36.0,
    "first_serve_pct":              62.0,
    "second_serve_pts_won":         54.0,
    "bp_generated_per_match":        5.5,
    "bp_converted_pct":             43.0,
    "aces_per_match":                6.2,
    "double_faults_per_match":       2.8,
}

WTA_TOUR_AVERAGES = {
    "ace_rate":                     2.0,
    "first_serve_pts_won":          65.0,
    "return_first_serve_pts_won":   38.0,
    "return_pts_won":               45.0,
    "bp_converted":                 42.0,
    "net_pts_won":                  55.0,
    "double_faults":                4.0,
    # ── tour-relative serve/return baselines (Step 1) ──
    "service_games_won":            57.0,
    "return_games_won":             43.0,
    "first_serve_pct":              62.0,
    "second_serve_pts_won":         50.0,
    "bp_generated_per_match":        7.2,
    "bp_converted_pct":             46.0,
    "aces_per_match":                2.1,
    "double_faults_per_match":       3.4,
}

# ── Opponent-quality weighting (Improvement 1) ───────────────────────────────
# Weight a historical match's stat contribution by the opponent's rank AT THE
# TIME of the match: stats earned vs elites are harder + more predictive; stats
# padded vs weak fields are discounted.
def opponent_quality_weight(rank) -> float:
    if rank is None:
        return 1.0
    if rank <= 20:   return 1.40
    if rank <= 50:   return 1.15
    if rank <= 100:  return 0.95
    if rank <= 150:  return 0.80
    return 0.65


def tier_proxy_rank(comp_tier) -> int:
    """Proxy opponent rank from the per-match strength-of-field tier (comp_tier
    3=main tour / 2=challenger / 1=ITF) when no Sackmann rank is available."""
    if comp_tier is None:
        return 90
    if comp_tier >= 2.5:   return 60     # main tour (mid-field)
    if comp_tier >= 1.5:   return 150    # challenger
    return 250                            # ITF / futures


def tier_proxy_weight(comp_tier, tournament=None) -> float:
    """Fallback opponent-quality weight when the opponent isn't in the current
    rankings list (retired, unranked, or ID mismatch). Drives off the per-match
    competition tier, bumping Grand Slam / Masters fields up via the tournament
    name. Tiers: GS/Masters 1.30 · generic tour 0.95 · Challenger 0.70 · ITF 0.55."""
    name = (tournament or "").lower()
    ct = comp_tier if isinstance(comp_tier, (int, float)) else 3.0
    if ct >= 2.5:  # main tour
        if any(k in name for k in (
            "wimbledon", "roland garros", "french open", "us open", "australian open",
            "masters", "indian wells", "miami", "monte", "madrid", "rome", "internazionali",
            "cincinnati", "shanghai", "canada", "toronto", "montreal", "paris",
        )):
            return 1.30
        return 0.95   # generic ATP/WTA 250-500
    if ct >= 1.5:     # challenger
        return 0.70
    return 0.55        # ITF / futures


# Serve-quality tier cutoffs on SERVICE GAMES WON %, tour-relative (Step 2/3).
# sgw > elite → Elite · >= strong → Strong · >= average → Average · else Weak.
SERVE_QUALITY_TIERS = {
    "ATP": {"elite": 82.0, "strong": 74.0, "average": 64.0},
    "WTA": {"elite": 72.0, "strong": 63.0, "average": 53.0},
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
