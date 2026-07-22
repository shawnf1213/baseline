// Client-side shaping of existing backend data for the mobile research app.
// No new endpoints — everything here derives from /api/results/record,
// /api/slate/today and /api/history responses.

// Prop types. `history` = supported by GET /api/history (over/under logs);
// Fantasy Score is a composite with no per-match log, so it has no hit strip.
export const PROP_TYPES = [
  { key: 'Aces', short: 'Aces', history: true },
  { key: 'Double Faults', short: 'Double Faults', history: true },
  { key: 'Break Points Won', short: 'Break Pts Won', history: true },
  { key: 'Total Games', short: 'Total Games', history: true },
  { key: 'Player Total Games Won', short: 'Games Won', history: true },
  { key: 'Fantasy Score', short: 'Fantasy Score', history: false },
]
export const SURFACES = ['Hard', 'Clay', 'Grass']
export const TOURS = ['ATP', 'WTA', 'Challenger']

export const shortProp = (t) =>
  (PROP_TYPES.find(p => p.key === t)?.short) || t

// ── dates (America/New_York, DST-correct) ────────────────────────────────────
export function etDate(ts) {
  if (!ts) return null
  try {
    const d = new Date(ts)
    if (isNaN(d)) return null
    return d.toLocaleDateString('en-CA', { timeZone: 'America/New_York' }) // YYYY-MM-DD
  } catch { return null }
}
export function etToday() {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' })
}
export function prettyDate(ymd) {
  if (!ymd) return ''
  const [y, m, d] = ymd.split('-').map(Number)
  const dt = new Date(Date.UTC(y, m - 1, d))
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}
export function startTimeLabel(ts) {
  if (!ts) return null
  try {
    return new Date(ts * 1000).toLocaleTimeString('en-US', {
      hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York',
    }) + ' ET'
  } catch { return null }
}

// ── name normalization (accent-insensitive) ──────────────────────────────────
export function normName(s) {
  return (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim()
}
const lastName = (s) => { const t = normName(s).split(/\s+/); return t[t.length - 1] || '' }

// Picks carry no `tour` column — infer best-effort from the tournament string.
// Flagged as inference in the UI, never presented as authoritative.
export function inferTour(tournament) {
  const t = (tournament || '').toLowerCase()
  if (/\bwta\b|women/.test(t)) return 'WTA'
  if (/challenger|\bitf\b|\b125\b/.test(t)) return 'Challenger'
  return 'ATP'
}

// Build name → {start-timestamp, tour, surface} maps from the slate (best-effort
// join). The slate's atp[]/wta[] arrays are a REAL tour + surface signal — the
// PrizePicks board carries neither, so this enriches rows where names match.
function mapsFromSlate(slate) {
  const startMap = {}, tourMap = {}, surfaceMap = {}
  const add = (name, ts, tour, surf) => {
    if (!name) return
    const n = normName(name), ln = lastName(name)
    if (ts) { startMap[n] = ts; if (ln && !(ln in startMap)) startMap[ln] = ts }
    if (tour) { tourMap[n] = tour; if (ln && !(ln in tourMap)) tourMap[ln] = tour }
    if (surf) { surfaceMap[n] = surf; if (ln && !(ln in surfaceMap)) surfaceMap[ln] = surf }
  }
  for (const r of (slate?.atp || [])) { add(r.p1, r.start_timestamp, 'ATP', r.surface); add(r.p2, r.start_timestamp, 'ATP', r.surface) }
  for (const r of (slate?.wta || [])) { add(r.p1, r.start_timestamp, 'WTA', r.surface); add(r.p2, r.start_timestamp, 'WTA', r.surface) }
  return { startMap, tourMap, surfaceMap }
}

// PrizePicks stat_type (lowercased) → Baseline prop type (mirrors the bot's PROP_MAP).
const PP_PROP_MAP = {
  'aces': 'Aces',
  'double faults': 'Double Faults', 'double fault': 'Double Faults',
  'break points won': 'Break Points Won',
  'total games': 'Total Games',
  'total games won': 'Player Total Games Won',
  'fantasy score': 'Fantasy Score',
}

// Parse the LIVE PrizePicks board (JSON:API) into neutral prop rows — the same
// shape the Board renders, but sourced from the live market instead of logged
// picks. Projections/edges start null and are computed client-side on demand.
// Mirrors the bot's _parse_board: tennis league, standard lines, singles only,
// opponent from `attributes.description`.
export function parsePrizePicksBoard(json, slate) {
  const empty = { date: etToday(), isToday: true, rows: [], source: 'prizepicks' }
  if (!json || typeof json !== 'object') return empty
  const inc = {}
  for (const i of (json.included || [])) inc[`${i.type}:${i.id}`] = i
  const { startMap, tourMap, surfaceMap } = mapsFromSlate(slate)
  const seen = new Set()
  const rows = []
  for (const proj of (json.data || [])) {
    const a = proj.attributes || {}
    const rel = proj.relationships || {}
    const lref = (rel.league || {}).data || {}
    const lname = (((inc[`${lref.type}:${lref.id}`] || {}).attributes || {}).name || '').toLowerCase()
    if (!lname.includes('tennis')) continue
    const propType = PP_PROP_MAP[(a.stat_type || '').trim().toLowerCase()]
    if (!propType) continue
    if ((a.odds_type || 'standard').toLowerCase() !== 'standard') continue
    if (a.line_score == null) continue
    const pref = ((rel.new_player || rel.player) || {}).data || {}
    const player = ((inc[`${pref.type}:${pref.id}`] || {}).attributes || {}).name || ''
    const opponent = (a.description || '').trim()
    if (!player || player.includes('/') || opponent.includes('/') || !opponent) continue
    const line = Number(a.line_score)
    if (isNaN(line)) continue
    const key = `${player}|${propType}|${line}`
    if (seen.has(key)) continue
    seen.add(key)
    rows.push({
      key, player, opponent, propType, line,
      projection: null, edge: null, confidence: null,   // computed lazily via /api/prop/calculate
      surface: lookup(surfaceMap, player) || '',
      tour: lookup(tourMap, player) || '',               // may be '' until projection resolves it
      tournament: '',
      oddsType: 'standard',
      startTs: lookup(startMap, player),
    })
  }
  return { date: etToday(), isToday: true, rows, source: 'prizepicks' }
}
function lookup(map, player) {
  return map[normName(player)] ?? map[lastName(player)] ?? null
}

// Results that mean the match is already decided — NOT researchable. Only
// undecided props (PENDING / not-yet-graded) are upcoming or in-play.
const DECIDED = new Set(['W', 'L', 'PUSH', 'VOID', 'NEEDS REVIEW'])
const isUpcoming = (p) => !DECIDED.has(String(p.result || '').toUpperCase().trim())

// Derive the research Board from the pick log. Shows only UPCOMING/in-play props
// (the most recent generated slate whose matches haven't resolved) as neutral
// rows — never completed matches, and never a picks feed.
export function deriveBoard(record, slate) {
  const picks = (record?.picks || [])
    .filter(p => !p.excluded_from_record)
    .filter(isUpcoming)
  const withDate = picks.map(p => ({ p, d: etDate(p.generated_at) })).filter(x => x.d)
  if (!withDate.length) return { date: null, isToday: false, rows: [] }
  const maxDate = withDate.reduce((m, x) => (x.d > m ? x.d : m), '0000-00-00')
  const dayPicks = withDate.filter(x => x.d === maxDate).map(x => x.p)
  const { startMap, tourMap } = mapsFromSlate(slate)

  const seen = new Set()
  const rows = []
  for (const p of dayPicks) {
    const key = `${p.player}|${p.prop_type}|${p.line}`
    if (seen.has(key)) continue
    seen.add(key)
    const proj = typeof p.model_projection === 'number' ? p.model_projection : null
    const line = typeof p.line === 'number' ? p.line : (typeof p.original_line === 'number' ? p.original_line : null)
    const edge = (proj != null && line != null) ? Math.round((proj - line) * 10) / 10 : null
    rows.push({
      key,
      player: p.player,
      opponent: p.opponent || '',
      propType: p.prop_type,
      line,
      projection: proj,
      edge,
      confidence: typeof p.confidence === 'number' ? p.confidence : null,
      surface: p.surface || '',
      // Prefer the slate's real ATP/WTA signal; fall back to tournament-string inference.
      tour: lookup(tourMap, p.player) || inferTour(p.tournament),
      tournament: p.tournament || '',
      oddsType: p.odds_type || 'standard',
      startTs: lookup(startMap, p.player),
    })
  }
  return { date: maxDate, isToday: maxDate === etToday(), rows }
}

// Distinct players present on the board (for the Players tab).
export function boardPlayers(rows) {
  const m = new Map()
  for (const r of rows) {
    if (!m.has(r.player)) {
      m.set(r.player, { player: r.player, tour: r.tour, surface: r.surface, tournament: r.tournament, props: 0 })
    }
    m.get(r.player).props += 1
  }
  return [...m.values()].sort((a, b) => b.props - a.props)
}

// ── /api/history → last-5 / last-10 over/under/push vs a reference line ───────
// history.last10 = [{date, opponent, value, over}] newest-first. We recompute
// over/under/push vs the reference (today's board line if the player has one,
// else the player's own average) from the real per-match values.
export function hitStrip(history, refLine) {
  const last10 = Array.isArray(history?.last10) ? history.last10 : []
  const ref = (refLine != null && !isNaN(refLine)) ? Number(refLine)
            : (typeof history?.average === 'number' ? history.average : null)
  const tally = (arr) => {
    let o = 0, u = 0, pu = 0
    for (const m of arr) {
      if (m?.value == null || ref == null) continue
      if (m.value > ref) o++
      else if (m.value < ref) u++
      else pu++
    }
    return { o, u, pu, n: o + u + pu }
  }
  return {
    ref,
    average: typeof history?.average === 'number' ? history.average : null,
    sample: history?.player_matches ?? 0,
    l5: tally(last10.slice(0, 5)),
    l10: tally(last10),
    values: last10.map(m => (typeof m?.value === 'number' ? m.value : null)),
  }
}

// Player headshot straight from Sofascore by id (real data, not a placeholder).
// The backend exposes no photo, so this is sourced client-side; callers fall
// back to an initials avatar when it fails to load.
export const sofaImg = (id) => id ? `https://api.sofascore.app/api/v1/player/${id}/image` : null

export const initials = (name) => {
  const t = (name || '').trim().split(/\s+/)
  if (!t.length) return '?'
  return ((t[0]?.[0] || '') + (t.length > 1 ? t[t.length - 1][0] : '')).toUpperCase()
}

export const fmt = (v, d = 1) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(d)
export const fmtSigned = (v, d = 1) => {
  if (v == null || isNaN(v)) return '—'
  const n = Number(v)
  return (n > 0 ? '+' : n < 0 ? '−' : '') + Math.abs(n).toFixed(d)
}
