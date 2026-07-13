// ── Tournament configuration by tour and surface ──────────────────────────────
// Each entry: { name, cpr, prev_cpr? }
//   name     — must match exactly what backend COURT_CPR expects
//   cpr      — ST Pace Index (String Tension confirmed or estimated value)
//   prev_cpr — previous year ST Pace Index (for year-over-year indicator when
//              change ≥ 5 pts). Field only present where we have prior-year data.
//
// Speed tiers (ST / ATP scale):
//   < 25     Very Slow
//   25–32    Slow
//   33–38    Average
//   39–44    Fast
//   > 44     Very Fast

export const ST_YOY_THRESHOLD = 5.0   // minimum change to show the YoY indicator

export function getSpeedTier(cpr) {
  if (cpr == null) return null
  if (cpr < 25)  return 'Very Slow'
  if (cpr <= 32) return 'Slow'
  if (cpr <= 38) return 'Average'
  if (cpr <= 44) return 'Fast'
  return 'Very Fast'
}

export const TOURNAMENT_CONFIG = {
  ATP: {
    Hard: [
      { name: "Australian Open",                  cpr: 40 },    // ST estimate (no confirmed value yet)
      { name: "US Open",                           cpr: 42.8 },  // ST confirmed 2025
      { name: "Indian Wells Masters",              cpr: 35.4 },  // ST confirmed 2023
      { name: "Miami Open",                        cpr: 40.6 },  // ST confirmed 2023
      { name: "Cincinnati Masters",                cpr: 38 },    // ST estimate
      { name: "Canadian Open",                     cpr: 38 },    // ST estimate — Montreal/Toronto alternate, either city
      { name: "Vienna Open",                       cpr: 37 },    // ST estimate
      { name: "Swiss Indoors Basel",               cpr: 38 },    // ST estimate
      { name: "Rotterdam Open",                    cpr: 37 },    // ST estimate
      { name: "Qatar Open Doha",                   cpr: 35 },    // ST estimate
      { name: "Dubai Duty Free Championships",     cpr: 36 },    // ST estimate
      { name: "ATP Finals Turin",                  cpr: 39 },    // ST estimate
      { name: "Paris Masters",                     cpr: 39 },    // ST estimate
      { name: "Dallas Open",                       cpr: 37 },    // ST estimate
      { name: "Delray Beach Open",                 cpr: 37 },    // ST estimate
      { name: "Adelaide International",            cpr: 38 },    // ST estimate
      { name: "Auckland Open",                     cpr: 37 },    // ST estimate
      { name: "Acapulco Open",                     cpr: 38 },    // ST estimate
      { name: "Washington DC Open",                cpr: 39 },    // ST estimate — fast outdoor hard
      { name: "Los Cabos Open",                    cpr: 37 },    // ST estimate
      { name: "Winston-Salem Open",                cpr: 38 },    // ST estimate
      { name: "Athens Open",                       cpr: 38 },    // ST estimate — Hellenic Championship, indoor hard
      { name: "Tokyo Japan Open",                  cpr: 38 },    // ST estimate
      { name: "Shanghai Masters",                  cpr: 36 },    // ST estimate
      { name: "Stockholm Open",                    cpr: 39 },    // ST estimate
      { name: "Antwerp European Open",             cpr: 39 },    // ST estimate
      { name: "Challenger Hard (Generic)",         cpr: 36 },    // ST estimate
    ],
    Clay: [
      // Roland Garros 2026 is dramatically faster than 2025 (new Dunlop ball).
      // Bettors using historical mental models of RG as "Very Slow" need this context.
      { name: "Roland Garros",                           cpr: 37.7, prev_cpr: 24.2, prev_year: 2025 }, // ST confirmed 2026
      { name: "Monte Carlo Masters",                     cpr: 30.4 },  // ST confirmed 2026
      { name: "Madrid Open",                             cpr: 31.9, altitude: 5 },  // ST confirmed 2026 — ALTITUDE ~667m
      { name: "Barcelona Open",                          cpr: 27.2 },  // ST confirmed 2026
      { name: "Italian Open Rome",                       cpr: 29.6 },  // ST confirmed 2026
      { name: "Hamburg Open",                            cpr: 28.4 },  // ST confirmed 2026
      { name: "Munich Open",                             cpr: 29.1 },  // ST confirmed 2026
      { name: "Geneva Open",                             cpr: 31.2 },  // ST confirmed 2026
      { name: "Lyon Open",                               cpr: 26 },    // ST estimate
      { name: "Buenos Aires Open",                       cpr: 25 },    // ST estimate
      { name: "Rio Open",                                cpr: 26 },    // ST estimate
      { name: "Santiago Open",                           cpr: 24 },    // ST estimate
      { name: "Houston Clay",                            cpr: 27 },    // ST estimate
      { name: "Estoril Open",                            cpr: 27 },    // ST estimate
      { name: "Marrakech Open",                          cpr: 24 },    // ST estimate
      { name: "Bastad Open",                             cpr: 27 },    // ST estimate — slow Scandinavian clay
      { name: "Umag Open",                               cpr: 27 },    // ST estimate — slow coastal clay
      { name: "Gstaad Open",                             cpr: 31, altitude: 5 },   // ST est — ALTITUDE ~1050m
      { name: "Kitzbuhel Open",                          cpr: 29, altitude: 3 },   // ST est — ALTITUDE ~800m
      { name: "Challenger Clay Europe (Generic)",        cpr: 26 },    // ST estimate
      { name: "Challenger Clay South America (Generic)", cpr: 26 },
      { name: "Bordeaux Challenger",                     cpr: 24 },
      { name: "Braunschweig Challenger",                 cpr: 24 },
      { name: "Valencia Challenger",                     cpr: 24 },
      { name: "Monza Challenger",                        cpr: 24 },
      { name: "Aix-en-Provence Challenger",              cpr: 24 },
      { name: "Sanremo Challenger",                      cpr: 24 },
      { name: "Geneva Challenger",                       cpr: 26 },
    ],
    Grass: [
      // Per Tennis Abstract Serve Impact: Stuttgart is the fastest grass on
      // tour (faster than Wimbledon), then Halle ~ Queens. Wimbledon 36.1 is
      // Average on the ST scale, not the old "Very Fast" 43.
      { name: "Wimbledon",               cpr: 36.1 },  // ST confirmed 2025 — Average
      { name: "Stuttgart",               cpr: 40 },    // ST est — fastest grass, Fast
      { name: "Halle",                   cpr: 38 },    // ST est — Serve Impact 1.10
      { name: "Queens Club Championships", cpr: 37 },  // ST est — Serve Impact 1.08
      { name: "s-Hertogenbosch",         cpr: 36 },    // ST est — Libema Open
      { name: "Mallorca",                cpr: 36 },    // ST est — ~Wimbledon
      { name: "Eastbourne International", cpr: 35 },    // ST est
      { name: "Birmingham",              cpr: 34 },    // ST est — Challenger 125, Edgbaston Priory
      { name: "Nottingham",              cpr: 34 },    // ST est — Rothesay Open Nottingham, ATP 125
    ],
  },

  WTA: {
    Hard: [
      { name: "Australian Open WTA",      cpr: 38 },
      { name: "US Open WTA",              cpr: 41.5 },  // same venue as ATP, WTA-adjusted
      { name: "Indian Wells WTA",         cpr: 34.5 },
      { name: "Miami Open WTA",           cpr: 39.5 },
      { name: "Cincinnati WTA",           cpr: 37 },
      { name: "Canadian Open WTA",        cpr: 37 },
      { name: "Wuhan Open",               cpr: 36 },
      { name: "China Open Beijing",       cpr: 36 },
      { name: "WTA Finals",               cpr: 37 },
      { name: "Dubai WTA",                cpr: 36 },
      { name: "Doha WTA",                 cpr: 35 },
      { name: "Adelaide WTA",             cpr: 37 },
      { name: "Auckland WTA",             cpr: 36 },
      { name: "Acapulco WTA",             cpr: 37 },
      { name: "San Jose WTA",             cpr: 36 },
      { name: "Washington WTA",           cpr: 39 },  // WTA 500, fast outdoor hard
      { name: "Tokyo Pan Pacific",        cpr: 37 },
      { name: "Osaka WTA",                cpr: 36 },
      { name: "Linz WTA",                 cpr: 38 },
      { name: "Guadalajara WTA",          cpr: 37 },
      { name: "Monterrey WTA",            cpr: 37 },  // ST estimate — generic until confirmed
      { name: "Cleveland WTA",            cpr: 37 },  // ST estimate — generic until confirmed
      { name: "Athens Open WTA",          cpr: 37 },  // ST estimate — generic until confirmed
      { name: "WTA 125 Hard (Generic)",   cpr: 36 },
      { name: "Austin WTA 125",           cpr: 36 },
      { name: "Jiangxi Open WTA 125",     cpr: 36 },
    ],
    Clay: [
      // Roland Garros WTA uses the same court as ATP — same ST value.
      { name: "Roland Garros WTA",              cpr: 37.7, prev_cpr: 24.2, prev_year: 2025 }, // ST confirmed 2026
      { name: "Madrid Open WTA",                cpr: 31.0 },
      { name: "Italian Open WTA Rome",          cpr: 28.5 },
      { name: "Stuttgart WTA",                  cpr: 27 },
      { name: "Hamburg WTA",                    cpr: 28.4 },  // ST confirmed 2026 — same court as ATP
      { name: "Prague Open WTA",                cpr: 27 },
      { name: "Rabat WTA",                      cpr: 23 },
      { name: "Strasbourg WTA",                 cpr: 24 },
      { name: "Warsaw WTA",                     cpr: 24 },
      { name: "Budapest WTA",                   cpr: 23 },
      { name: "Bastad WTA",                     cpr: 23 },
      { name: "Palermo WTA",                    cpr: 23 },
      { name: "San Jose Clay WTA",              cpr: 25 },
      { name: "Bogota WTA",                     cpr: 23 },
      { name: "Trophee Clarins Paris WTA 125",  cpr: 23 },
      { name: "Catalonia Open WTA 125",         cpr: 23 },
      { name: "Huzhou Open WTA 125 Clay",       cpr: 23 },
      { name: "Emilia-Romagna WTA 125 Clay",    cpr: 23 },
      { name: "WTA 125 Clay (Generic)",         cpr: 26 },
    ],
    Grass: [
      { name: "Wimbledon WTA",           cpr: 36.1 },  // ST confirmed 2025 — same court as ATP
      { name: "Queens Club WTA",         cpr: 37 },     // WTA 500, week before men (Jun 8 2026)
      { name: "Bad Homburg WTA",         cpr: 36 },     // WTA 500
      { name: "s-Hertogenbosch WTA",     cpr: 36 },     // Libema Open
      { name: "Mallorca WTA",            cpr: 36 },
      { name: "Eastbourne WTA",          cpr: 35 },
      { name: "Birmingham WTA",          cpr: 34 },     // WTA 125, Edgbaston Priory (same venue as ATP)
      { name: "Nottingham WTA",          cpr: 34 },     // ST est — Rothesay Open Nottingham, WTA 250
      { name: "Berlin WTA",              cpr: 35 },     // ST est — bett1open Berlin, WTA 500 on grass
    ],
  },
}

// Legacy flat list (backward compat)
export const COURTS_BY_SURFACE = {
  Hard:  ['Australian Open','US Open','Indian Wells Masters','Miami Open','Cincinnati Masters',
          'Canadian Open (Montreal/Toronto)','Vienna Open','Swiss Indoors Basel','Rotterdam Open',
          'Qatar Open Doha','Dubai Duty Free Championships','ATP Finals Turin'],
  Clay:  ['Roland Garros','Monte Carlo Masters','Madrid Open','Barcelona Open','Italian Open Rome',
          'Hamburg Open','Lyon Open'],
  Grass: ['Wimbledon','Queens Club Championships','Halle','Stuttgart Grass','Eastbourne International'],
}

export const ATP_AVERAGES = {
  aces: 10.0, double_faults: 3.0,
  first_serve_pct: 65.0, first_serve_pts_won: 75.0,
  second_serve_pts_won: 55.0, return_first_serve_pts_won: 32.0,
  return_second_serve_pts_won: 53.0, bp_converted: 45.0, bp_saved: 65.0,
}

export const WTA_AVERAGES = {
  aces: 2.0, double_faults: 4.0,
  first_serve_pct: 62.0, first_serve_pts_won: 65.0,
  second_serve_pts_won: 50.0, return_first_serve_pts_won: 38.0,
  return_second_serve_pts_won: 56.0, bp_converted: 42.0, bp_saved: 60.0,
}

export const SURFACE_COLORS = { Hard: '#42A5F5', Clay: '#EF6C00', Grass: '#2E7D32' }

export const STAT_LABELS = {
  aces: 'Aces / Match',
  double_faults: 'Double Faults / Match',
  first_serve_pct: '1st Serve %',
  first_serve_pts_won: '1st Serve Pts Won',
  second_serve_pts_won: '2nd Serve Pts Won',
  return_first_serve_pts_won: 'Ret Pts Won (1st Srv)',
  return_second_serve_pts_won: 'Ret Pts Won (2nd Srv)',
  bp_converted: 'BP Converted %',
  bp_saved: 'BP Saved %',
}

export const fmt = (v, d = 1) => v == null ? '—' : Number(v).toFixed(d)
export const fmtPct = (v) => v == null ? '—' : `${Number(v).toFixed(0)}%`
