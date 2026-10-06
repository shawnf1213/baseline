// The live board — every line the books list for the sports we price, with
// Baseline's number beside it.
//
// TENNIS is priced ON THE PHONE, row by row, through /api/prop/calculate —
// the website's approach (frontend/src/mobile/project.js), ported: resolve
// the name to an id, find the scheduled opponent, price, cache. Three
// requests in flight at a time, because Sofascore rate-limits and more
// parallelism produces failures rather than speed. The backend caches each
// projection, so a second reader of the same board is fast.
//
// NFL and NBA arrive ALREADY PRICED: the bot scans and publishes the whole
// board, the backend stores it, and the record of what was posted is merged
// in for the ⭐ and the result (NflBoard.jsx / NbaBoard.jsx).
import { calcProp, fetchLiveBoard, fetchNbaBoard, fetchNbaRecord, fetchNextMatch,
         fetchNflBoard, fetchNflRecord } from './api'
import { Book, Found, PickRow, blankRow, etToday, inferTour, normName, resolvePlayer,
         teamPickRow } from './picks'
import type { SportKey } from './sports'

export type BoardState = 'idle' | 'loading' | 'done' | 'nodata'
export type BoardRow = PickRow & { state: BoardState }
export type BoardData = { rows: BoardRow[]; available: boolean; slate: string | null }

const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null)

export async function loadBoard(sport: SportKey, book: Book): Promise<BoardData> {
  if (sport === 'tennis') {
    const d = await fetchLiveBoard(book)
    const date = d?.slate_date || etToday()
    const rows: BoardRow[] = (Array.isArray(d?.rows) ? d.rows : []).map((r: any) => ({
      ...blankRow('tennis', book, date),
      key: `board|tennis|${book}|${r.player}|${r.prop_type}|${r.line}`,
      player: String(r.player || ''), opponent: String(r.opponent || ''),
      propType: String(r.prop_type || ''), line: num(r.line),
      surface: String(r.surface || ''), tournament: String(r.tournament || ''),
      tour: r.tour || inferTour(r.tournament || ''), tourInferred: !r.tour,
      startTs: num(r.start_timestamp), startsAt: String(r.starts_at || ''),
      overPrice: num(r.over_price), underPrice: num(r.under_price),
      state: 'idle' as BoardState,
    }))
    return { rows, available: d?.available !== false, slate: d?.slate_date || null }
  }

  // NFL / NBA: the scanned market plus the posted record, merged.
  const [boardRows, recRows] = await Promise.all([
    (sport === 'nfl' ? fetchNflBoard() : fetchNbaBoard()).then(d => (Array.isArray(d?.rows) ? d.rows : [])).catch(() => []),
    (sport === 'nfl' ? fetchNflRecord() : fetchNbaRecord()).then(d => (Array.isArray(d?.picks) ? d.picks : [])).catch(() => []),
  ])
  const slates = [...new Set<string>(boardRows.map((p: any) => String(p.slate_date || '')).filter(Boolean))].sort().reverse()
  const today = etToday()
  const active = slates.includes(today) ? today : (slates.filter(s => s > today).sort()[0] || slates[0] || null)
  const posted = new Map<string, any>()
  for (const p of recRows) posted.set(`${p.slate_date}|${p.player}|${p.prop_type}`, p)
  const rows: BoardRow[] = boardRows
    .filter((p: any) => p.slate_date === active && (!p.book || p.book === book))
    .map((p: any) => {
      const hit = posted.get(`${p.slate_date}|${p.player}|${p.prop_type}`)
      const merged = hit ? { ...p, result: hit.result, result_value: hit.result_value,
                             is_potd: hit.is_potd, is_star: hit.is_star } : p
      const row = teamPickRow(sport, merged, book)
      return { ...row, key: `board|${row.key}`, state: 'done' as BoardState }
    })
    .sort((a: BoardRow, b: BoardRow) => (Number(b.isPotd) - Number(a.isPotd)) || ((b.confidence ?? -1) - (a.confidence ?? -1)))
  return { rows, available: true, slate: active }
}

// ── tennis pricing queue ────────────────────────────────────────────────────
export type Priced = {
  projection: number | null; confidence: number | null; edge: number | null
  lean: 'OVER' | 'UNDER' | ''; tour: 'ATP' | 'WTA'
  playerId: string; opponentId: string; surface: string
}

const playerCache = new Map<string, Found | null>()
const ctxCache = new Map<string, { opponent_id: string; surface: string } | null>()
const projCache = new Map<string, Priced | null>()
const inflight = new Map<string, Promise<Priced | null>>()

// One cache key, derived from the row's content: the same player, prop and
// line IS the same projection whichever book or screen asked for it.
export const projKey = (r: PickRow) => `${normName(r.player)}|${r.propType}|${r.line}`
export const cachedPrice = (r: PickRow) => projCache.get(projKey(r))   // undefined = never priced

const LIMIT = 3
let active = 0
const q: { key: string; run: () => void }[] = []
function pump() { while (active < LIMIT && q.length) { active++; q.shift()!.run() } }
function schedule<T>(key: string, fn: () => Promise<T>, priority: boolean): Promise<T | null> {
  return new Promise(resolve => {
    const entry = { key, run: () => Promise.resolve().then(fn).then(
      v => { active--; pump(); resolve(v) },
      () => { active--; pump(); resolve(null) }) }
    if (priority) q.unshift(entry); else q.push(entry)
    pump()
  })
}
function promote(key: string) {
  const i = q.findIndex(e => e.key === key)
  if (i > 0) q.unshift(q.splice(i, 1)[0])
}

async function cachedResolve(name: string, tour: string): Promise<Found | null> {
  const k = normName(name)
  if (playerCache.has(k)) return playerCache.get(k)!
  const f = await resolvePlayer(name, tour)
  playerCache.set(k, f)
  return f
}

async function context(id: string, tour: string) {
  if (ctxCache.has(id)) return ctxCache.get(id)!
  let ctx: { opponent_id: string; surface: string } | null = null
  try {
    const nm = await fetchNextMatch(id, tour)
    if (nm?.opponent_id) ctx = { opponent_id: String(nm.opponent_id), surface: nm.surface || '' }
  } catch { /* leave null */ }
  ctxCache.set(id, ctx)
  return ctx
}

export function priceRow(row: PickRow, priority = false): Promise<Priced | null> {
  const key = projKey(row)
  if (projCache.has(key)) return Promise.resolve(projCache.get(key)!)
  const pending = inflight.get(key)
  if (pending) { if (priority) promote(key); return pending }
  const run = schedule<Priced | null>(key, async () => {
    const p = await cachedResolve(row.player, row.tour)
    if (!p) return null
    const ctx = await context(p.id, p.tour)
    let opponentId = ctx?.opponent_id
    const surface = ctx?.surface || row.surface || 'Hard'
    const court = row.tournament || ''
    if (!opponentId) opponentId = (await cachedResolve(row.opponent, p.tour))?.id
    if (!opponentId || row.line == null) return null
    let data: any = null
    for (let attempt = 0; attempt < 2 && data == null; attempt++) {
      if (attempt > 0) await new Promise(r => setTimeout(r, 3500))
      try {
        data = await calcProp({
          player_id: p.id, opponent_id: opponentId,
          player_name: row.player, opponent_name: row.opponent,
          tour: p.tour, surface, court, qualifying: /qualif/i.test(court),
          prop_type: row.propType, prop_line: row.line,
        })
      } catch { data = null }
    }
    if (!data) return null
    const proj = num(data.model_projection)
    const edge = proj != null ? Math.round((proj - row.line) * 10) / 10 : null
    const lean = String(data.lean || '').toUpperCase()
    return {
      projection: proj, confidence: num(data.confidence), edge,
      lean: lean === 'OVER' || lean === 'UNDER' ? lean : (edge == null || edge === 0 ? '' : edge > 0 ? 'OVER' : 'UNDER'),
      tour: p.tour, playerId: p.id, opponentId, surface,
    }
  }, priority).then(res => { projCache.set(key, res); inflight.delete(key); return res },
                    () => { inflight.delete(key); return null })
  inflight.set(key, run)
  return run
}

export function applyPrice(row: BoardRow, p: Priced | null | undefined): BoardRow {
  if (p === undefined) return row
  if (p === null) return { ...row, state: 'nodata' }
  return {
    ...row, state: p.projection == null ? 'nodata' : 'done',
    projection: p.projection, confidence: p.confidence, edge: p.edge, lean: p.lean,
    tour: p.tour, tourInferred: false, playerId: p.playerId, opponentId: p.opponentId,
    surface: row.surface || p.surface,
  }
}
