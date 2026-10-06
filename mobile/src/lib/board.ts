// The live board — every line the books list for the sports we price, with
// Baseline's number beside it.
//
// EVERY SPORT ARRIVES PRICED BY THE BACKEND. Tennis rows are priced on the
// server on a schedule (backend/src/board_pricer) through the same throttled
// path the bot uses; the phone only reads them, and a row the server has not
// reached yet shows as pending. NFL and NBA boards are published already
// priced by the bot's scan, merged with the posted record for the ⭐ and the
// result (NflBoard.jsx / NbaBoard.jsx). Nothing here calls the pricing
// endpoint — the one-request-at-a-time Project tool is the only caller.
import { fetchLiveBoard, fetchNbaBoard, fetchNbaRecord, fetchNflBoard, fetchNflRecord } from './api'
import { Book, PickRow, blankRow, etToday, inferTour, normName, teamPickRow } from './picks'
import type { SportKey } from './sports'

// pending = the backend has not priced this row yet; started = the match is
// under way and the row was never priced; nodata = the model could not price it.
export type BoardState = 'done' | 'pending' | 'started' | 'nodata'
export type BoardRow = PickRow & { state: BoardState }
export type BoardData = { rows: BoardRow[]; available: boolean; slate: string | null; pricedCount: number }

const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null)

// The last board each (sport, book) loaded, kept for five minutes so a player
// sheet can show the lines on that player without re-fetching the board.
const lastBoards = new Map<string, { at: number; data: BoardData }>()
const BOARD_KEEP_MS = 5 * 60_000

export type PlayerLine = { propType: string; line: number; lean: string; book: Book; row: BoardRow }

export async function boardLinesFor(sport: SportKey, name: string): Promise<PlayerLine[]> {
  const out: PlayerLine[] = []
  for (const book of ['prizepicks', 'underdog'] as Book[]) {
    const k = `${sport}|${book}`
    let hit = lastBoards.get(k)
    if (!hit || Date.now() - hit.at > BOARD_KEEP_MS) {
      try { hit = { at: Date.now(), data: await loadBoard(sport, book) } } catch { continue }
    }
    for (const r of hit.data.rows) {
      if (normName(r.player) !== normName(name) || r.line == null) continue
      if (out.some(o => o.propType === r.propType && o.line === r.line)) continue
      out.push({ propType: r.propType, line: r.line, lean: r.lean, book, row: r })
    }
  }
  return out
}

export async function loadBoard(sport: SportKey, book: Book): Promise<BoardData> {
  const data = await loadBoardRaw(sport, book)
  lastBoards.set(`${sport}|${book}`, { at: Date.now(), data })
  return data
}

async function loadBoardRaw(sport: SportKey, book: Book): Promise<BoardData> {
  if (sport === 'tennis') {
    const d = await fetchLiveBoard(book)
    const date = d?.slate_date || etToday()
    const rows: BoardRow[] = (Array.isArray(d?.rows) ? d.rows : []).map((r: any) => {
      const proj = num(r.projection)
      const line = num(r.line)
      const lean = String(r.lean || '').toUpperCase()
      const pricing = String(r.pricing || 'pending')
      const state: BoardState = pricing === 'priced' && proj != null ? 'done'
        : pricing === 'failed' ? 'nodata' : pricing === 'started' ? 'started' : 'pending'
      return {
        ...blankRow('tennis', book, date),
        key: `board|tennis|${book}|${r.player}|${r.prop_type}|${r.line}`,
        player: String(r.player || ''), opponent: String(r.opponent || ''),
        propType: String(r.prop_type || ''), line,
        lean: lean === 'OVER' || lean === 'UNDER' ? lean : '',
        projection: proj,
        edge: num(r.edge) ?? (proj != null && line != null ? Math.round((proj - line) * 10) / 10 : null),
        confidence: num(r.confidence),
        surface: String(r.surface || r.surface_used || ''), tournament: String(r.tournament || ''),
        tour: r.tour || inferTour(r.tournament || ''), tourInferred: !r.tour,
        startTs: num(r.start_timestamp), startsAt: String(r.starts_at || ''),
        overPrice: num(r.over_price), underPrice: num(r.under_price),
        playerId: r.player_id ? String(r.player_id) : null,
        opponentId: r.opponent_id ? String(r.opponent_id) : null,
        state,
      }
    })
    return { rows, available: d?.available !== false, slate: d?.slate_date || null,
             pricedCount: rows.filter(r => r.state === 'done').length }
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
  return { rows, available: true, slate: active, pricedCount: rows.length }
}
