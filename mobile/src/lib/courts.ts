// The tournament list, fetched once from /api/courts (the backend owns it so
// this picker, the website's and the bot's cannot drift apart). Shape:
// tour → surface → [{ name, cpr }].
import { useEffect, useState } from 'react'
import { fetchCourts } from './api'

export type Court = { name: string; cpr?: number }
export type Courts = Record<string, Record<string, Court[]>>

let cache: Courts | null = null
let inflight: Promise<Courts | null> | null = null

function load() {
  if (!inflight) {
    inflight = fetchCourts()
      .then(d => {
        const tours = d?.tours
        if (!tours || !Object.keys(tours).length) return null
        cache = tours
        return tours
      })
      .catch(() => null)
  }
  return inflight
}

export function useCourts(): Courts {
  const [cfg, setCfg] = useState<Courts>(cache || {})
  useEffect(() => {
    if (cache) return
    let alive = true
    load().then(t => { if (t && alive) setCfg(t) })
    return () => { alive = false }
  }, [])
  return cfg
}

// The slate names a tournament the way a broadcast does ("Guadalajara,
// Mexico"); the picker speaks COURT_CPR keys ("Guadalajara WTA").
export function resolveCourtName(tournament: string, cfg: Courts, tour: string, surface: string) {
  const list = cfg?.[tour]?.[surface] || []
  if (!tournament) return ''
  const exact = list.find(c => c.name === tournament)
  if (exact) return exact.name
  const city = tournament.split(',')[0].trim().toLowerCase()
  if (!city) return ''
  const hit = list.find(c => c.name.toLowerCase().startsWith(city))
  return hit ? hit.name : ''
}
