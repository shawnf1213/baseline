import { searchPlayers, fetchNextMatch, calcProp } from '../utils/api'
import { normName } from './data'

// Client-side projection of a live PrizePicks prop, reusing existing read-only
// endpoints — no bot, no new server code:
//   player name → /api/search (id + tour)
//   id → /api/player/next-match (opponent_id + surface)
//   → /api/prop/calculate (model projection + confidence)
// Everything is cached and gated behind a small concurrency limit so browsing
// the board never floods the backend / Sofascore.

const playerCache = new Map()   // normName -> {id, tour, rank} | null
const ctxCache = new Map()      // id -> {opponent_id, surface} | null
const projCache = new Map()     // projKey(row) -> result | null
const inflight = new Map()      // projKey(row) -> Promise, so two views that
                                // want the same projection make ONE request

// ── ONE CACHE KEY, DERIVED FROM THE ROW'S CONTENT ────────────────────────────
// This used to cache on row.key, and row.key is not one thing. The board builds
// it as `${player}|${propType}|${line}` while mergedBoardRows — what the player
// page reads — rebuilds it as `${normName(player)}|...`, which is lowercased.
// So every lookup the player page made was a guaranteed miss, and opening a
// player re-projected props the board had already priced seconds earlier. The
// comment on that effect said "shared cache with the Board"; it never was.
//
// Keying on content rather than on whoever built the row also means the two
// books, and the board and the player page, all hit the same entry: the same
// player, prop and line IS the same projection.
export const projKey = (row) =>
  `${normName(row.player)}|${row.propType}|${row.line}`

// ── concurrency limiter ──────────────────────────────────────────────────────
// Gentle on the backend / Sofascore (which rate-limits): too many parallel
// projections cause failures, not speed. 3 in flight is the reliable sweet spot.
//
// THE QUEUE IS SHARED AND THE BOARD FILLS IT. BoardTab speculatively projects up
// to 120 rows on mount so that sorting by edge works across the whole board, and
// it stays mounted underneath the player page. A player page asking for its
// three props therefore joined the back of a queue of ~117, which at three at a
// time is minutes of spinner. Work the reader is actually looking at jumps the
// queue; the board's prefetch is speculative and can wait.
const LIMIT = 3
let active = 0
const q = []                    // [{ key, run }]
function pump() { while (active < LIMIT && q.length) { active++; q.shift().run() } }
function schedule(key, fn, priority) {
  return new Promise((resolve) => {
    const entry = { key, run: () => Promise.resolve().then(fn).then(
      (v) => { active--; pump(); resolve(v) },
      () => { active--; pump(); resolve(null) },
    ) }
    if (priority) q.unshift(entry); else q.push(entry)
    pump()
  })
}
// Pull an already-queued projection to the front. Without this, a row the board
// queued first would still be waiting its turn even though the reader has since
// opened the page that needs it.
function promote(key) {
  const i = q.findIndex(e => e.key === key)
  if (i > 0) q.unshift(q.splice(i, 1)[0])
}

// Resolve a name to {id, tour, rank}. Searches BOTH tours and prefers an EXACT
// name match, with the tour taken from the matched player's gender — so a WTA
// name (e.g. "Lucia Bronzetti") never locks onto a fuzzy male ATP match just
// because ATP was searched first. Only falls back to a fuzzy result when no
// exact name exists in either tour.
export async function resolvePlayer(name, tourHint) {
  const nk = normName(name)
  if (playerCache.has(nk)) return playerCache.get(nk)
  const order = tourHint === 'WTA' ? ['WTA', 'ATP'] : ['ATP', 'WTA']
  const all = []
  for (const t of order) {
    try {
      const res = await searchPlayers(name, t)
      if (Array.isArray(res)) all.push(...res)
    } catch { /* try next tour */ }
    if (all.some(r => normName(r.name) === nk)) break   // exact match found — stop
  }
  const m = all.find(r => normName(r.name) === nk) || all[0] || null
  let found = null
  if (m) {
    const tour = m.gender === 'F' ? 'WTA' : m.gender === 'M' ? 'ATP' : (tourHint || 'ATP')
    found = { id: String(m.id), tour, rank: m.currentRank ?? null }
  }
  playerCache.set(nk, found)
  return found
}

async function getContext(id, tour) {
  if (ctxCache.has(id)) return ctxCache.get(id)
  let ctx = null
  try {
    const nm = await fetchNextMatch(id, tour)
    if (nm && nm.opponent_id) ctx = { opponent_id: String(nm.opponent_id), surface: nm.surface || '' }
  } catch { /* leave null */ }
  ctxCache.set(id, ctx)
  return ctx
}

// Returns { projection, confidence, edge, tour, playerId, opponentId, surface }
// or null when the player/opponent can't be resolved or the model has no data.
export async function projectRow(row, tourHint, { priority = false } = {}) {
  const key = projKey(row)
  if (projCache.has(key)) return projCache.get(key)
  // Already running (or queued) for someone else — join it rather than firing a
  // second identical request, and pull it forward if this caller is the one the
  // reader is looking at.
  const pending = inflight.get(key)
  if (pending) { if (priority) promote(key); return pending }

  const run = schedule(key, async () => {
    const p = await resolvePlayer(row.player, tourHint || row.tour)
    if (!p) return null
    const ctx = await getContext(p.id, p.tour)
    let opponentId = ctx?.opponent_id
    const surface = ctx?.surface || row.surface || 'Hard'
    // From the slate join. '' is the honest value when the name did not match —
    // the backend then does exactly what it did before this change.
    const court = row.tournament || ''
    if (!opponentId) {
      const opp = await resolvePlayer(row.opponent, p.tour)
      opponentId = opp?.id
    }
    if (!opponentId) return null
    // Retry once on a transient failure (rate-limit / timeout) — a short pause
    // usually clears it, and the first attempt often warms the backend cache.
    let data = null
    for (let attempt = 0; attempt < 2 && data == null; attempt++) {
      if (attempt > 0) await new Promise(r => setTimeout(r, 3500))
      try {
        // court + qualifying MATCH THE BOT'S PAYLOAD (pick_of_day._evaluate).
        // Omitting court made the backend fall back to a generic surface pace
        // instead of the real court's ST Pace Index, and that is not a rounding
        // difference: on Dolehide vs Zarazua at Guadalajara it moved pace 36 ->
        // 37, which flipped the serve profile from Average to Strong Server and
        // the projection from 5.5 to 6.8. Same prop, two numbers, depending on
        // whether you read it here or in Discord.
        data = await calcProp({
          player_id: p.id, opponent_id: opponentId,
          player_name: row.player, opponent_name: row.opponent,
          tour: p.tour, surface, court,
          qualifying: /qualif/i.test(court),
          prop_type: row.propType, prop_line: row.line,
        })
      } catch { data = null }
    }
    if (!data) return null
    const proj = typeof data.model_projection === 'number' ? data.model_projection : null
    return {
      projection: proj,
      confidence: typeof data?.confidence === 'number' ? data.confidence : null,
      edge: proj != null ? Math.round((proj - row.line) * 10) / 10 : null,
      tour: p.tour, rank: p.rank, playerId: p.id, opponentId, surface,
      note: data?.note || null,
    }
  }, priority).then((result) => {
    projCache.set(key, result)
    inflight.delete(key)
    return result
  }, () => {
    inflight.delete(key)
    return null
  })
  inflight.set(key, run)
  return run
}

// Takes the ROW, not a key — see projKey. Passing row.key was the bug: the
// caller's idea of a key and the cache's were different strings.
export const cachedProjection = (row) => projCache.get(projKey(row))
