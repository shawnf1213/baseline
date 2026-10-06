// Which sports the app shows, and which one is selected.
//
// VISIBILITY COMES FROM THE BACKEND (/api/sports), so a sport can be shown or
// hidden without an app release. Tennis, NFL and NBA are on today; MLB stays
// hidden until its module ships. If the endpoint cannot be read the app falls
// back to those three rather than to nothing.
//
// THE SELECTION IS SHARED. Picks, Board and Project all read the same sport,
// so switching to NFL on the board and opening Project keeps you on NFL — the
// switch is one control drawn on three screens, not three settings.
import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from 'react'
import { fetchSports } from './api'
import { Segmented } from '@/components/ui'

export type SportKey = 'tennis' | 'nfl' | 'nba' | 'mlb'
export type SportInfo = { key: SportKey; label: string; visible: boolean }

const FALLBACK: SportInfo[] = [
  { key: 'tennis', label: 'Tennis', visible: true },
  { key: 'nfl', label: 'NFL', visible: true },
  { key: 'nba', label: 'NBA', visible: true },
  { key: 'mlb', label: 'MLB', visible: false },
]
const KNOWN: SportKey[] = ['tennis', 'nfl', 'nba', 'mlb']

type Ctx = { sports: SportInfo[]; sport: SportKey; setSport: (k: SportKey) => void }
const SportCtx = createContext<Ctx | null>(null)

export function SportProvider({ children }: { children: ReactNode }) {
  const [sports, setSports] = useState<SportInfo[]>(FALLBACK)
  const [sport, setSportRaw] = useState<SportKey>('tennis')

  useEffect(() => {
    let alive = true
    fetchSports().then(d => {
      const list = Array.isArray(d?.sports) ? d.sports : []
      const clean = list
        .filter((s: any) => KNOWN.includes(s?.key))
        .map((s: any) => ({ key: s.key as SportKey, label: String(s.label || s.key), visible: !!s.visible }))
      if (alive && clean.length) setSports(clean)
    }).catch(() => {})
    return () => { alive = false }
  }, [])

  // A sport hidden from the backend can never stay selected.
  useEffect(() => {
    const visible = sports.filter(s => s.visible)
    if (visible.length && !visible.some(s => s.key === sport)) setSportRaw(visible[0].key)
  }, [sports, sport])

  const value = useMemo(() => ({
    sports, sport,
    setSport: (k: SportKey) => { if (sports.some(s => s.key === k && s.visible)) setSportRaw(k) },
  }), [sports, sport])
  return <SportCtx.Provider value={value}>{children}</SportCtx.Provider>
}

export function useSport(): Ctx {
  const c = useContext(SportCtx)
  if (!c) throw new Error('useSport outside SportProvider')
  return c
}

export const sportLabel = (k: SportKey) => FALLBACK.find(s => s.key === k)?.label || k

// The switch itself. Draws nothing when only one sport is visible.
export function SportSwitch({ onChange }: { onChange?: (k: SportKey) => void }) {
  const { sports, sport, setSport } = useSport()
  const visible = sports.filter(s => s.visible)
  if (visible.length <= 1) return null
  return (
    <Segmented options={visible.map(s => ({ key: s.key, label: s.label }))} value={sport}
               onChange={k => { setSport(k); onChange?.(k) }} />
  )
}
