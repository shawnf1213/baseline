import { useEffect, useState } from 'react'
import { TOURNAMENT_CONFIG } from './constants'
import { fetchCourts } from './api'

// ── THE TOURNAMENT LIST, FETCHED ─────────────────────────────────────────────
// This app and the Discord bot each kept their own copy of the court list and
// they drifted: 118 tournaments here against 43 there. A venue the bot didn't
// offer couldn't be picked, so that projection fell back to generic surface
// pace while the same matchup on the site priced off the real court — and a
// pace of 36 against 37 is enough to move a number. The backend owns the list
// now (it already owns the COURT_CPR values it has to agree with) and both
// clients read it.
//
// TOURNAMENT_CONFIG stays as the FALLBACK, not as a second source of truth. It
// renders instantly on first paint and covers a cold backend; the fetched list
// replaces it in place the moment it lands. Shape is identical — tour →
// surface → [{ name, cpr }] — so nothing downstream knows the difference.

let cache = null        // the fetched list, once, for the life of the tab
let inflight = null     // so ten mounted pickers make one request

function load() {
  if (!inflight) {
    inflight = fetchCourts()
      .then((d) => {
        const tours = d?.tours
        // An empty answer is the backend's documented "use your snapshot".
        if (!tours || !Object.keys(tours).length) return null
        cache = tours
        return tours
      })
      .catch(() => null)
  }
  return inflight
}

export function useTournamentConfig() {
  const [cfg, setCfg] = useState(cache || TOURNAMENT_CONFIG)
  useEffect(() => {
    if (cache) return undefined
    let alive = true
    load().then((t) => { if (t && alive) setCfg(t) })
    return () => { alive = false }
  }, [])
  return cfg
}
