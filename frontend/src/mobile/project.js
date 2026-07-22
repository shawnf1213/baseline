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
const projCache = new Map()     // row.key -> result | null

// ── concurrency limiter ──────────────────────────────────────────────────────
const LIMIT = 3
let active = 0
const q = []
function pump() { while (active < LIMIT && q.length) { active++; (q.shift())() } }
function schedule(fn) {
  return new Promise((resolve) => {
    q.push(() => Promise.resolve().then(fn).then(
      (v) => { active--; pump(); resolve(v) },
      () => { active--; pump(); resolve(null) },
    ))
    pump()
  })
}

async function resolvePlayer(name, tourHint) {
  const nk = normName(name)
  if (playerCache.has(nk)) return playerCache.get(nk)
  const tries = tourHint === 'WTA' ? ['WTA', 'ATP'] : tourHint === 'ATP' ? ['ATP', 'WTA'] : ['ATP', 'WTA']
  let found = null
  for (const t of tries) {
    try {
      const res = await searchPlayers(name, t)
      if (Array.isArray(res) && res.length) {
        const m = res.find(r => normName(r.name) === nk) || res[0]
        const tour = m.gender === 'F' ? 'WTA' : m.gender === 'M' ? 'ATP' : t
        found = { id: String(m.id), tour, rank: m.currentRank ?? null }
        break
      }
    } catch { /* try next tour */ }
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
export async function projectRow(row, tourHint) {
  if (projCache.has(row.key)) return projCache.get(row.key)
  const result = await schedule(async () => {
    const p = await resolvePlayer(row.player, tourHint || row.tour)
    if (!p) return null
    const ctx = await getContext(p.id, p.tour)
    let opponentId = ctx?.opponent_id
    const surface = ctx?.surface || row.surface || 'Hard'
    if (!opponentId) {
      const opp = await resolvePlayer(row.opponent, p.tour)
      opponentId = opp?.id
    }
    if (!opponentId) return null
    const data = await calcProp({
      player_id: p.id, opponent_id: opponentId,
      player_name: row.player, opponent_name: row.opponent,
      tour: p.tour, surface, prop_type: row.propType, prop_line: row.line,
    })
    const proj = typeof data?.model_projection === 'number' ? data.model_projection : null
    return {
      projection: proj,
      confidence: typeof data?.confidence === 'number' ? data.confidence : null,
      edge: proj != null ? Math.round((proj - row.line) * 10) / 10 : null,
      tour: p.tour, rank: p.rank, playerId: p.id, opponentId, surface,
      note: data?.note || null,
    }
  })
  projCache.set(row.key, result)
  return result
}

export const cachedProjection = (key) => projCache.get(key)
