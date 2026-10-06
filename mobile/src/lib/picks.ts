// Client-side shaping of the record and board payloads into the one row shape
// every card and sheet renders, for all three sports. Tennis is a port of the
// website's data.js (derivePicks, slateDateOf, pickSource, monthRecord,
// hitStrip); NFL and NBA follow NflBoard.jsx / NbaBoard.jsx — same grouping,
// same ordering, same scoring, so the app cannot show a different record than
// the site or the Discord recap.
//
// ONLY DISCORD-CARD FIELDS LEAVE THIS FILE (operator ruling 8). Record rows
// also carry model_inputs, drivers and (for NBA projections) a confidence
// breakdown. None of that is copied onto a PickRow — the row is built field
// by field below, so a new internal column on the backend can never appear in
// the app by accident.
import { fetchNbaRecord, fetchNflRecord, fetchRecord, searchPlayers } from './api'
import type { SportKey } from './sports'

export type Book = 'prizepicks' | 'underdog'

// ── props per sport ─────────────────────────────────────────────────────────
// Tennis: `history` = GET /api/history has a per-match over/under log for the
// prop. Fantasy Score and Break Points Saved are composites with no log.
export const PROP_TYPES = [
  { key: 'Aces',                   short: 'Aces',            unit: 'aces',               history: true },
  { key: 'Double Faults',          short: 'Double Faults',   unit: 'double faults',      history: true },
  { key: 'Break Points Won',       short: 'Break Pts Won',   unit: 'break points won',   history: true },
  { key: 'Break Points Saved',     short: 'Break Pts Saved', unit: 'break points saved', history: false },
  { key: 'Total Games',            short: 'Total Games',     unit: 'games',              history: true },
  { key: 'Player Total Games Won', short: 'Games Won',       unit: 'games won',          history: true },
  { key: 'Fantasy Score',          short: 'Fantasy Score',   unit: 'fantasy points',     history: false },
] as const
export const SURFACES = ['Hard', 'Clay', 'Grass'] as const

// NFL: `stat` is the column in a player's form game log.
export const NFL_PROPS = [
  { key: 'receptions',      label: 'Receptions',      unit: 'receptions',      stat: 'receptions' },
  { key: 'receiving_yards', label: 'Receiving Yards', unit: 'receiving yards', stat: 'receiving_yards' },
  { key: 'rush_yards',      label: 'Rush Yards',      unit: 'rushing yards',   stat: 'rushing_yards' },
  { key: 'pass_yards',      label: 'Pass Yards',      unit: 'passing yards',   stat: 'passing_yards' },
] as const

// NBA: `parts` are the game-log columns a prop is summed from.
export const NBA_PROPS = [
  { key: 'pts',             label: 'Points',          unit: 'points',          parts: ['pts'] },
  { key: 'reb',             label: 'Rebounds',        unit: 'rebounds',        parts: ['reb'] },
  { key: 'ast',             label: 'Assists',         unit: 'assists',         parts: ['ast'] },
  { key: 'fg3m',            label: '3-Pointers Made', unit: 'threes',          parts: ['fg3m'] },
  { key: 'pra',             label: 'Pts + Reb + Ast', unit: 'pts + reb + ast', parts: ['pts', 'reb', 'ast'] },
  { key: 'pr',              label: 'Pts + Reb',       unit: 'pts + reb',       parts: ['pts', 'reb'] },
  { key: 'pa',              label: 'Pts + Ast',       unit: 'pts + ast',       parts: ['pts', 'ast'] },
  { key: 'ra',              label: 'Reb + Ast',       unit: 'reb + ast',       parts: ['reb', 'ast'] },
  { key: 'nba_fantasy_pts', label: 'Fantasy Points',  unit: 'fantasy points',  parts: ['nba_fantasy_pts'] },
] as const

export const tennisProp = (k: string) => PROP_TYPES.find(p => p.key === k)
export const shortProp  = (k: string) => tennisProp(k)?.short || k

export function propLabel(sport: SportKey, key: string) {
  if (sport === 'nfl') return NFL_PROPS.find(p => p.key === key)?.label || key
  if (sport === 'nba') return NBA_PROPS.find(p => p.key === key)?.label || key
  return shortProp(key)
}
export function propUnit(sport: SportKey, key: string) {
  if (sport === 'nfl') return NFL_PROPS.find(p => p.key === key)?.unit || key
  if (sport === 'nba') return NBA_PROPS.find(p => p.key === key)?.unit || key
  return tennisProp(key)?.unit || key.toLowerCase()
}
export function propHasHistory(sport: SportKey, key: string) {
  if (sport === 'nfl') return !!NFL_PROPS.find(p => p.key === key)
  if (sport === 'nba') return !!NBA_PROPS.find(p => p.key === key)
  return !!tennisProp(key)?.history
}

// ── formatting ──────────────────────────────────────────────────────────────
export const fmt = (v: unknown, d = 1) =>
  (v == null || isNaN(Number(v))) ? '—' : Number(v).toFixed(d)
export const fmtSigned = (v: unknown, d = 1) => {
  if (v == null || isNaN(Number(v))) return '—'
  const n = Number(v)
  return (n > 0 ? '+' : n < 0 ? '−' : '') + Math.abs(n).toFixed(d)
}
// A line prints as "6.5" and "7", never "7.0".
export const fmtLine = (v: unknown) =>
  (v == null || isNaN(Number(v))) ? '—'
    : (Number.isInteger(Number(v)) ? String(Number(v)) : Number(v).toFixed(1))

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
export function startTimeLabel(ts?: number | null) {
  if (!ts) return ''
  try {
    return new Date(ts * 1000).toLocaleTimeString('en-US',
      { hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' }) + ' ET'
  } catch { return '' }
}
export function kickoffLabel(iso?: string | null) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ''
    return d.toLocaleString('en-US', { weekday: 'short', hour: 'numeric', minute: '2-digit',
                                       timeZone: 'America/New_York' }) + ' ET'
  } catch { return '' }
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

// Mirrors src/database.py::pick_source — the book a tennis pick came from.
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

// Tennis picks carry no tour column. Best-effort from the tournament string,
// flagged as a guess so the UI never states it as fact.
export function inferTour(tournament: string) {
  const t = (tournament || '').toLowerCase()
  if (/\bwta\b|women/.test(t)) return 'WTA'
  if (/challenger|\bitf\b|\b125\b/.test(t)) return 'Challenger'
  return 'ATP'
}

// ── the row ─────────────────────────────────────────────────────────────────
export type PickRow = {
  key: string
  id: number | null
  sport: SportKey
  book: Book
  date: string
  player: string
  opponent: string
  team: string | null            // NFL / NBA club abbreviation
  propType: string               // tennis: full prop name; NFL/NBA: prop key
  line: number | null
  lean: 'OVER' | 'UNDER' | ''
  projection: number | null
  edge: number | null
  confidence: number | null      // 0–100 on every sport
  result: string
  resultValue: number | null
  surface: string
  tournament: string
  tour: string
  tourInferred: boolean
  isThreeX: boolean
  isPotd: boolean | null         // null = posted before the star was recorded
  playerRank: number | null
  opponentRank: number | null
  // Context the card and the sheet can show (all of it is on the Discord card)
  matchup: string                // NFL/NBA: "Atlanta Falcons at New Orleans Saints"
  startsAt: string               // NFL kickoff ISO / NBA tip-off, when known
  startTs: number | null         // tennis: epoch seconds from the slate
  usageWindow: string            // NFL/NBA: "current + prior season"
  minutes: number | null         // NBA
  rotation: string               // NBA: stable / variable / volatile
  overPrice: number | null       // Underdog prices, when offered
  underPrice: number | null
  playerId: string | null        // tennis Sofascore ids once priced/resolved
  opponentId: string | null
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
const str = (v: unknown) => (v == null ? '' : String(v))

export function blankRow(sport: SportKey, book: Book, date: string): PickRow {
  return {
    key: '', id: null, sport, book, date, player: '', opponent: '', team: null, propType: '',
    line: null, lean: '', projection: null, edge: null, confidence: null, result: 'PENDING',
    resultValue: null, surface: '', tournament: '', tour: '', tourInferred: true, isThreeX: false,
    isPotd: null, playerRank: null, opponentRank: null, matchup: '', startsAt: '', startTs: null,
    usageWindow: '', minutes: null, rotation: '', overPrice: null, underPrice: null,
    playerId: null, opponentId: null,
  }
}

function tallyDay(date: string, rows: PickRow[], starFirst: boolean): PickDay {
  const star = rows.find(r => r.isPotd === true) || (starFirst ? rows[0] || null : null)
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
}

// Baseline's tracked TENNIS picks for one book, newest slate first, grouped by
// slate date, in BOARD ORDER (ascending id is the posted order — the ⭐ is
// logged first). Decided picks are kept: the result is the point.
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
    const lean = str(p.lean).toUpperCase()
    const row: PickRow = {
      ...blankRow('tennis', book, d),
      key: `tennis|${p.id ?? ''}|${p.player}|${p.prop_type}|${p.line}`,
      id: num(p.id),
      player: str(p.player), opponent: str(p.opponent), propType: str(p.prop_type),
      line, lean: lean === 'OVER' || lean === 'UNDER' ? lean : '',
      projection: proj,
      edge: proj != null && line != null ? Math.round((proj - line) * 10) / 10 : null,
      confidence: num(p.confidence),
      result: str(p.result || 'PENDING').toUpperCase().trim(),
      resultValue: num(p.result_value),
      surface: str(p.surface), tournament: str(p.tournament),
      tour: inferTour(p.tournament),
      isThreeX: str(p.pick_group).toLowerCase().includes('3x'),
      isPotd: p.is_potd == null ? null : !!p.is_potd,
      playerRank: num(p.player_rank), opponentRank: num(p.opponent_rank),
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
      return tallyDay(date, rows, true)
    })
}

// NFL / NBA picks come from their own record tables, one row per posted play,
// already stamped with the slate date and the book. NFL stores confidence as a
// probability (0.77) and NBA as a 0–100 score; both land here as 0–100.
export function teamPickRow(sport: SportKey, p: any, bookFallback: Book): PickRow {
  const book: Book = p?.book === 'underdog' ? 'underdog' : p?.book === 'prizepicks' ? 'prizepicks' : bookFallback
  const date = str(p?.slate_date) || etToday()
  const proj = num(p?.model_projection)
  const line = num(p?.line)
  let conf = num(p?.confidence)
  if (sport === 'nfl' && conf != null && conf <= 1) conf = Math.round(conf * 1000) / 10
  const lean = str(p?.lean).toUpperCase()
  const star = sport === 'nba' ? p?.is_star : p?.is_potd
  return {
    ...blankRow(sport, book, date),
    key: `${sport}|${p?.id ?? ''}|${p?.player}|${p?.prop_type}|${p?.line}|${book}`,
    id: num(p?.id),
    player: str(p?.player), opponent: str(p?.opponent), team: p?.team ? str(p.team) : null,
    propType: str(p?.prop_type), line,
    lean: lean === 'OVER' || lean === 'UNDER' ? lean : '',
    projection: proj,
    edge: num(p?.edge) ?? (proj != null && line != null ? Math.round((proj - line) * 10) / 10 : null),
    confidence: conf,
    result: str(p?.result || 'PENDING').toUpperCase().trim(),
    resultValue: num(p?.result_value),
    tour: sport.toUpperCase(), tourInferred: false,
    isPotd: star == null ? null : !!star,
    matchup: str(p?.matchup), startsAt: str(p?.kickoff || p?.tipoff || p?.starts_at),
    usageWindow: str(p?.usage_window), minutes: num(p?.minutes), rotation: str(p?.rotation),
  }
}

export function deriveTeamPicks(sport: SportKey, picks: any[], book: Book): PickDay[] {
  const byDate = new Map<string, PickRow[]>()
  for (const p of picks || []) {
    if (p?.excluded_from_record) continue
    if (p?.book && p.book !== book) continue
    const row = teamPickRow(sport, p, book)
    if (!byDate.has(row.date)) byDate.set(row.date, [])
    byDate.get(row.date)!.push(row)
  }
  return [...byDate.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : -1))
    .map(([date, rows]) => {
      rows.sort((a, b) => (Number(b.isPotd) - Number(a.isPotd)) || ((b.confidence ?? -1) - (a.confidence ?? -1)))
      return tallyDay(date, rows, false)
    })
}

export function derivePicksFor(sport: SportKey, raw: any, book: Book): PickDay[] {
  if (sport === 'tennis') return derivePicks(raw, book)
  return deriveTeamPicks(sport, raw?.picks || [], book)
}

export function loadPicksRaw(sport: SportKey): Promise<any> {
  if (sport === 'nfl') return fetchNflRecord()
  if (sport === 'nba') return fetchNbaRecord()
  return fetchRecord()
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

// ── recent form, one shape for every sport ──────────────────────────────────
export type HistGame = { date?: string; opponent?: string; value: number }
export type Hist = {
  average: number | null
  sample: number
  games: HistGame[]                       // newest first
  season: { over: number; under: number; push: number; n: number }
}

// tennis: /api/history
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

function seasonTally(games: HistGame[], line: number | null) {
  let over = 0, under = 0, push = 0
  if (line != null) for (const g of games) { if (g.value > line) over++; else if (g.value < line) under++; else push++ }
  return { over, under, push, n: games.length }
}

// NFL: /api/nfl/players → players[0].form.games (one row per week)
export function nflHistory(profile: any, propKey: string, line: number | null): Hist | null {
  const stat = NFL_PROPS.find(p => p.key === propKey)?.stat
  const games: any[] = Array.isArray(profile?.form?.games) ? profile.form.games : []
  if (!stat || !games.length) return null
  const rows: HistGame[] = games
    .filter(g => typeof g?.[stat] === 'number')
    .map(g => ({ date: g.week != null ? `Wk ${g.week}` : '', opponent: str(g.opponent_team), value: Number(g[stat]) }))
  // Weeks arrive oldest first; the chart wants newest first.
  rows.sort((a, b) => Number(String(b.date).replace(/\D/g, '')) - Number(String(a.date).replace(/\D/g, '')))
  if (!rows.length) return null
  const avg = rows.reduce((s, g) => s + g.value, 0) / rows.length
  return { average: Math.round(avg * 10) / 10, sample: rows.length, games: rows, season: seasonTally(rows, line) }
}

// NBA: /api/nba/player → games (one row per game, combos summed from parts)
export function nbaHistory(payload: any, propKey: string, line: number | null): Hist | null {
  const parts = NBA_PROPS.find(p => p.key === propKey)?.parts
  const games: any[] = Array.isArray(payload?.games) ? payload.games : []
  if (!parts || !games.length) return null
  const rows: HistGame[] = []
  for (const g of games) {
    let v = 0, ok = true
    for (const k of parts) { if (typeof g?.[k] !== 'number') { ok = false; break }; v += g[k] }
    if (!ok) continue
    const opp = str(g.matchup).split(/\s+(?:vs\.?|@)\s+/i).pop() || ''
    rows.push({ date: str(g.game_date).slice(0, 10), opponent: opp, value: Math.round(v * 10) / 10 })
  }
  rows.sort((a, b) => (a.date! < b.date! ? 1 : -1))
  if (!rows.length) return null
  const avg = rows.reduce((s, g) => s + g.value, 0) / rows.length
  return { average: Math.round(avg * 10) / 10, sample: rows.length, games: rows, season: seasonTally(rows, line) }
}

// ── name → Sofascore id (tennis) ────────────────────────────────────────────
// Record rows carry names, not ids; /api/history and /api/prop/calculate need
// ids. Search the guessed tour first, then the other one — the guess comes
// from the tournament string and is wrong often enough to matter.
export type Found = { id: string; name: string; tour: 'ATP' | 'WTA'; currentRank: number | null }
export const normName = (s: string) =>
  (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim()

export async function resolvePlayer(name: string, tourGuess: string): Promise<Found | null> {
  const first: 'ATP' | 'WTA' = tourGuess === 'WTA' ? 'WTA' : 'ATP'
  const order: ('ATP' | 'WTA')[] = first === 'ATP' ? ['ATP', 'WTA'] : ['WTA', 'ATP']
  const all: any[] = []
  for (const t of order) {
    let rows: any[] = []
    try { rows = await searchPlayers(name, t) } catch { rows = [] }
    if (Array.isArray(rows)) all.push(...rows.map(r => ({ ...r, _tour: t })))
    if (all.some(r => normName(r?.name) === normName(name))) break
  }
  const hit = all.find(r => normName(r?.name) === normName(name)) || all[0]
  if (!hit?.id) return null
  const tour: 'ATP' | 'WTA' = hit.gender === 'F' ? 'WTA' : hit.gender === 'M' ? 'ATP' : hit._tour
  return { id: String(hit.id), name: String(hit.name || name), tour, currentRank: num(hit.currentRank) }
}
