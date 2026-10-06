// Client-side shaping of /api/results/record into what the Picks screen and
// the pick sheet show. A port of the website's data.js (derivePicks,
// slateDateOf, pickSource, monthRecord, hitStrip) — same grouping, same
// ordering, same scoring, so the app cannot show a different record than the
// site or the Discord recap.
//
// ONLY DISCORD-CARD FIELDS LEAVE THIS FILE (operator ruling 8). A record row
// also carries model_inputs; /api/results/record already strips
// confidence_breakdown. Neither is copied onto a PickRow — the row is built
// field by field below, so a new internal column on the backend can never
// appear in the app by accident.
import { searchPlayers } from './api'

export type Book = 'prizepicks' | 'underdog'

// `history` = GET /api/history has a per-match over/under log for this prop.
// Fantasy Score and Break Points Saved are composites with no per-match log.
export const PROP_TYPES = [
  { key: 'Aces',                   short: 'Aces',            unit: 'aces',               history: true },
  { key: 'Double Faults',          short: 'Double Faults',   unit: 'double faults',      history: true },
  { key: 'Break Points Won',       short: 'Break Pts Won',   unit: 'break points won',   history: true },
  { key: 'Break Points Saved',     short: 'Break Pts Saved', unit: 'break points saved', history: false },
  { key: 'Total Games',            short: 'Total Games',     unit: 'games',              history: true },
  { key: 'Player Total Games Won', short: 'Games Won',       unit: 'games won',          history: true },
  { key: 'Fantasy Score',          short: 'Fantasy Score',   unit: 'fantasy points',     history: false },
] as const
export type PropKey = typeof PROP_TYPES[number]['key']
export const SURFACES = ['Hard', 'Clay', 'Grass'] as const

export const propMeta  = (k: string) => PROP_TYPES.find(p => p.key === k)
export const shortProp = (k: string) => propMeta(k)?.short || k
export const propUnit  = (k: string) => propMeta(k)?.unit || k.toLowerCase()
export const propHasHistory = (k: string) => !!propMeta(k)?.history

export const fmt = (v: unknown, d = 1) =>
  (v == null || isNaN(Number(v))) ? '—' : Number(v).toFixed(d)
export const fmtSigned = (v: unknown, d = 1) => {
  if (v == null || isNaN(Number(v))) return '—'
  const n = Number(v)
  return (n > 0 ? '+' : n < 0 ? '−' : '') + Math.abs(n).toFixed(d)
}
// A line prints as "6.5" and "7", never "7.0".
export const fmtLine = (v: unknown) =>
  (v == null || isNaN(Number(v))) ? '—' : (Number.isInteger(Number(v)) ? String(Number(v)) : Number(v).toFixed(1))

// ── dates (America/New_York) ────────────────────────────────────────────────
export const etToday = () =>
  new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' })
export const etHour = () =>
  Number(new Date().toLocaleString('en-US', { timeZone: 'America/New_York', hour: '2-digit', hour12: false }))

export function prettyDate(ymd: string) {
  if (!ymd) return ''
  const [y, m, d] = ymd.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-US',
    { month: 'short', day: 'numeric', timeZone: 'UTC' })
}
export function etMonthLabel() {
  const [y, m] = etToday().split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString('en-US', { month: 'long', timeZone: 'UTC' })
}

// Mirrors discord-bot/bot.py::_slate_date_of — the ET date a pick's match is
// PLAYED. A list built from noon onward is tomorrow's card; earlier is today's.
export function slateDateOf(p: any): string | null {
  const raw = p?.generated_at
  if (!raw) return null
  try {
    let s = String(raw).trim().replace(' ', 'T')
    if (!/(Z|[+-]\d{2}:?\d{2})$/.test(s)) s += 'Z'
    const d = new Date(s)
    if (isNaN(d.getTime())) return null
    const hour = Number(d.toLocaleString('en-US', { timeZone: 'America/New_York', hour: '2-digit', hour12: false }))
    const ymd = d.toLocaleDateString('en-CA', { timeZone: 'America/New_York' })
    if (hour < 12) return ymd
    const [y, m, dd] = ymd.split('-').map(Number)
    return new Date(Date.UTC(y, m - 1, dd + 1)).toISOString().slice(0, 10)
  } catch { return null }
}

// Mirrors src/database.py::pick_source — the book a pick's board came from.
export const pickSource = (p: any): Book =>
  String(p?.pick_group || 'potd').toLowerCase().startsWith('underdog') ? 'underdog' : 'prizepicks'

export type ResultTone = 'win' | 'loss' | 'void' | 'pending'
const RESULT_META: Record<string, { label: string; tone: ResultTone }> = {
  W: { label: 'Won', tone: 'win' },
  L: { label: 'Lost', tone: 'loss' },
  PUSH: { label: 'Push', tone: 'win' },      // pushes count as wins, per the recap
  VOID: { label: 'Void', tone: 'void' },
  'NEEDS REVIEW': { label: 'Review', tone: 'void' },
}
export const resultMeta = (r: string) =>
  RESULT_META[String(r || '').toUpperCase().trim()] || { label: 'Pending', tone: 'pending' as ResultTone }

// Picks carry no tour column. Best-effort from the tournament string, and
// flagged as a guess so the UI never states it as fact.
export function inferTour(tournament: string) {
  const t = (tournament || '').toLowerCase()
  if (/\bwta\b|women/.test(t)) return 'WTA'
  if (/challenger|\bitf\b|\b125\b/.test(t)) return 'Challenger'
  return 'ATP'
}

export type PickRow = {
  key: string
  id: number | null
  book: Book
  date: string
  player: string
  opponent: string
  propType: string
  line: number | null
  lean: 'OVER' | 'UNDER' | ''
  projection: number | null
  edge: number | null
  confidence: number | null
  result: string
  resultValue: number | null
  surface: string
  tournament: string
  tour: string
  tourInferred: boolean
  isThreeX: boolean
  isPotd: boolean | null       // null = posted before the star was recorded
  playerRank: number | null
  opponentRank: number | null
}

export type PickDay = {
  date: string
  rows: PickRow[]
  star: PickRow | null
  rest: PickRow[]
  wins: number
  losses: number
  pending: number
  winRate: number | null
  settled: boolean
}

const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null)

// Baseline's tracked picks for one book, newest slate first, grouped by slate
// date, in BOARD ORDER (ascending id is the posted order — the ⭐ is logged
// first). Decided picks are kept: the result is the point.
export function derivePicks(record: any, book: Book): PickDay[] {
  const src: any[] = book === 'underdog' ? (record?.underdog?.picks || []) : (record?.picks || [])
  const byDate = new Map<string, PickRow[]>()
  for (const p of src) {
    if (p?.excluded_from_record) continue
    if (pickSource(p) !== book) continue
    const d = slateDateOf(p)
    if (!d) continue
    const proj = num(p.model_projection)
    const line = num(p.line) ?? num(p.original_line)
    const lean = String(p.lean || '').toUpperCase()
    const row: PickRow = {
      key: `${p.id ?? ''}|${p.player}|${p.prop_type}|${p.line}`,
      id: num(p.id),
      book, date: d,
      player: String(p.player || ''),
      opponent: String(p.opponent || ''),
      propType: String(p.prop_type || ''),
      line,
      lean: lean === 'OVER' || lean === 'UNDER' ? lean : '',
      projection: proj,
      edge: proj != null && line != null ? Math.round((proj - line) * 10) / 10 : null,
      confidence: num(p.confidence),
      result: String(p.result || 'PENDING').toUpperCase().trim(),
      resultValue: num(p.result_value),
      surface: String(p.surface || ''),
      tournament: String(p.tournament || ''),
      tour: inferTour(p.tournament),
      tourInferred: true,
      isThreeX: String(p.pick_group || '').toLowerCase().includes('3x'),
      isPotd: p.is_potd == null ? null : !!p.is_potd,
      playerRank: num(p.player_rank),
      opponentRank: num(p.opponent_rank),
    }
    if (!byDate.has(d)) byDate.set(d, [])
    byDate.get(d)!.push(row)
  }
  return [...byDate.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : -1))
    .map(([date, rows]) => {
      rows.sort((a, b) => {
        if (a.id != null && b.id != null && a.id !== b.id) return a.id - b.id
        return (b.confidence ?? -1) - (a.confidence ?? -1)
      })
      // The Pick of the Day: the recorded star, else the first-logged row
      // (ranked[0] is written first), which is how the website reads it too.
      const star = rows.find(r => r.isPotd === true) || rows[0] || null
      let w = 0, l = 0, pending = 0
      for (const r of rows) {
        if (r.result === 'W' || r.result === 'PUSH') w++
        else if (r.result === 'L') l++
        else if (r.result !== 'VOID') pending++
      }
      const decided = w + l
      return {
        date, rows, star, rest: rows.filter(r => r !== star),
        wins: w, losses: l, pending,
        winRate: decided ? Math.round((w / decided) * 1000) / 10 : null,
        settled: pending === 0 && rows.length > 0,
      }
    })
}

// Record for the CURRENT CALENDAR MONTH, Eastern time.
export function monthRecord(days: PickDay[]) {
  const prefix = etToday().slice(0, 7)
  let w = 0, l = 0
  for (const d of days) {
    if (!d.date.startsWith(prefix)) continue
    w += d.wins; l += d.losses
  }
  const decided = w + l
  return { wins: w, losses: l, decided, winRate: decided ? Math.round((w / decided) * 1000) / 10 : null }
}

// ── /api/history → hit windows ──────────────────────────────────────────────
export type HistGame = { date?: string; opponent?: string; value: number }
export type Hist = {
  average: number | null
  sample: number
  games: HistGame[]                       // newest first
  season: { over: number; under: number; push: number; n: number }
}
export function shapeHistory(h: any): Hist {
  const last10: any[] = Array.isArray(h?.last10) ? h.last10 : []
  return {
    average: num(h?.average),
    sample: Number(h?.player_matches || 0),
    games: last10.filter(g => typeof g?.value === 'number'),
    season: { over: Number(h?.over || 0), under: Number(h?.under || 0),
              push: Number(h?.push || 0), n: Number(h?.player_matches || 0) },
  }
}

// ── name → Sofascore id ─────────────────────────────────────────────────────
// Record rows carry names, not ids; /api/history and /api/prop/calculate need
// ids. Search the guessed tour first, then the other one — the guess comes
// from the tournament string and is wrong often enough to matter.
export type Found = { id: string; name: string; tour: 'ATP' | 'WTA'; currentRank: number | null }
const norm = (s: string) => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim()

export async function resolvePlayer(name: string, tourGuess: string): Promise<Found | null> {
  const first: 'ATP' | 'WTA' = tourGuess === 'WTA' ? 'WTA' : 'ATP'
  const order: ('ATP' | 'WTA')[] = first === 'ATP' ? ['ATP', 'WTA'] : ['WTA', 'ATP']
  for (const t of order) {
    let rows: any[] = []
    try { rows = await searchPlayers(name, t) } catch { rows = [] }
    if (!Array.isArray(rows) || !rows.length) continue
    const exact = rows.find(r => norm(r?.name) === norm(name))
    const hit = exact || rows[0]
    if (!hit?.id) continue
    const tour: 'ATP' | 'WTA' = hit.gender === 'F' ? 'WTA' : hit.gender === 'M' ? 'ATP' : t
    return { id: String(hit.id), name: String(hit.name || name), tour, currentRank: num(hit.currentRank) }
  }
  return null
}
