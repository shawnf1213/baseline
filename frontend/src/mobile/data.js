// Client-side shaping of existing backend data for the mobile research app.
// No new endpoints — everything here derives from /api/results/record,
// /api/slate/today and /api/history responses.

// Prop types. `history` = supported by GET /api/history (over/under logs);
// Fantasy Score is a composite with no per-match log, so it has no hit strip.
// Break Points Saved is COMPOSITE — reconstructed from faced x save% rather than
// logged per match — so like Fantasy Score it has no hit strip.
//
// Sets Won / Sets Played are deliberately ABSENT: /api/prop/calculate has no
// branch for them (they are derived from the scenario mixture inside the bot),
// so a set row could never show a projection. Underdog lists them; we drop them
// in parseUnderdogBoard rather than render permanent dashes.
export const PROP_TYPES = [
  { key: 'Aces', short: 'Aces', history: true },
  { key: 'Double Faults', short: 'Double Faults', history: true },
  { key: 'Break Points Won', short: 'Break Pts Won', history: true },
  { key: 'Break Points Saved', short: 'Break Pts Saved', history: false },
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

// ── Underdog board ───────────────────────────────────────────────────────────
// Mirrors discord-bot/underdog.py: same PROP_MAP, same straight-only filter, same
// appearance -> solo_game -> other-side opponent resolution. Kept in sync by hand
// because the bot's copy is Python and server-side; if one changes, change both.
const UD_PROP_MAP = {
  'Aces': 'Aces',
  'Double Faults': 'Double Faults',
  'Breakpoints Won': 'Break Points Won',
  'Break Points Saved': 'Break Points Saved',
  'Games Won': 'Player Total Games Won',
  'Games Played': 'Total Games',
  // 'Sets Won' / 'Sets Played' intentionally unmapped — see PROP_TYPES.
}
const UD_RANK_PREFIX = /^\(\s*\d+\s*\)\s*/   // "(1) Aryna Sabalenka"
const udClean = (s) => (s || '').trim().replace(UD_RANK_PREFIX, '').trim()

// A line is only takeable if BOTH sides exist at level (1.0x) payout. Underdog
// mixes multiplier lines (0.73x/1.37x …) and one-sided lines onto the same feed;
// those are a different bet, so the bot skips them and so do we.
function udIsStraight(ln) {
  const opts = ln?.options || []
  if (opts.length < 2) return false
  const choices = new Set(opts.map(o => o?.choice))
  if (!(choices.has('higher') && choices.has('lower') && choices.size === 2)) return false
  return opts.every(o => Math.abs(Number(o?.payout_multiplier) - 1) < 1e-9)
}

export function parseUnderdogBoard(json, slate) {
  const empty = { date: etToday(), isToday: true, rows: [], source: 'underdog' }
  if (!json || typeof json !== 'object') return empty
  const players = {}
  for (const p of (json.players || [])) {
    if (String(p?.sport_id || '').toUpperCase() === 'TENNIS') players[p.id] = p
  }
  const apps = {}
  for (const a of (json.appearances || [])) if (a?.player_id in players) apps[a.id] = a
  const solo = {}
  for (const g of (json.solo_games || [])) solo[g.id] = g

  const { startMap, tourMap, surfaceMap } = mapsFromSlate(slate)
  const seen = new Set()
  const rows = []
  for (const ln of (json.over_under_lines || [])) {
    const st = (ln?.over_under || {}).appearance_stat || {}
    const app = apps[st.appearance_id]
    if (!app) continue
    if (!udIsStraight(ln)) continue
    const propType = UD_PROP_MAP[st.display_stat]
    if (!propType) continue
    if (ln.live_event) continue
    const game = solo[app.match_id] || {}
    const pid = app.player_id
    const opponent = udClean(
      pid === game.home_player_id ? game.away_player_name
      : pid === game.away_player_id ? game.home_player_name
      : ''
    )
    const pl = players[pid] || {}
    const player = udClean(`${pl.first_name || ''} ${pl.last_name || ''}`)
    const line = Number(ln.stat_value)
    if (!player || !opponent || isNaN(line)) continue
    if (player.includes('/') || opponent.includes('/')) continue   // doubles
    const key = `${player}|${propType}|${line}`
    if (seen.has(key)) continue
    seen.add(key)
    let overPx = null, underPx = null
    for (const o of (ln.options || [])) {
      if (o.choice === 'higher') overPx = o.american_price
      else if (o.choice === 'lower') underPx = o.american_price
    }
    rows.push({
      key, player, opponent, propType, line,
      projection: null, edge: null, confidence: null,
      surface: lookup(surfaceMap, player) || '',
      tour: lookup(tourMap, player) || '',
      tournament: '',
      oddsType: 'standard',
      startTs: lookup(startMap, player),
      overPrice: overPx, underPrice: underPx,
      startsAt: game.scheduled_at || null,
    })
  }
  return { date: etToday(), isToday: true, rows, source: 'underdog' }
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

// ── Baseline's own picks (the bot's boards), tracked ─────────────────────────
// This is the ONLY part of the app that mirrors the bot. The Boards tabs above
// are independent: they show the live market for the prop types Baseline scans,
// whether or not the bot picked them.

// Mirrors discord-bot/bot.py::_slate_date_of — the ET date a pick's match is
// actually PLAYED. A list built from noon onward is tomorrow's card; earlier is
// today's. Must match the bot exactly or the app groups picks differently than
// the recap does.
export function slateDateOf(p) {
  const raw = p?.generated_at
  if (!raw) return null
  try {
    // Stamps arrive as '2026-06-27T04:33:29.129430+00:00' — already offset-aware.
    // Only a naive stamp gets a 'Z'; appending one to an existing offset yields
    // an invalid date, which silently emptied the whole tab.
    let s = String(raw).trim().replace(' ', 'T')
    if (!/(Z|[+-]\d{2}:?\d{2})$/.test(s)) s += 'Z'
    const d = new Date(s)
    if (isNaN(d)) return null
    const hour = Number(d.toLocaleString('en-US', { timeZone: 'America/New_York', hour: '2-digit', hour12: false }))
    const ymd = d.toLocaleDateString('en-CA', { timeZone: 'America/New_York' })
    if (hour < 12) return ymd
    const [y, m, dd] = ymd.split('-').map(Number)
    const next = new Date(Date.UTC(y, m - 1, dd + 1))
    return next.toISOString().slice(0, 10)
  } catch { return null }
}

// Mirrors src/database.py::pick_source — the book a pick's board came from.
export function pickSource(p) {
  const g = String(p?.pick_group || 'potd').toLowerCase()
  return g.startsWith('underdog') ? 'underdog' : 'prizepicks'
}

const RESULT_META = {
  W: { label: 'WON', tone: 'win' },
  L: { label: 'LOST', tone: 'loss' },
  PUSH: { label: 'PUSH', tone: 'win' },      // pushes count as wins, per the recap
  VOID: { label: 'VOID', tone: 'void' },
  'NEEDS REVIEW': { label: 'REVIEW', tone: 'void' },
}
export const resultMeta = (r) =>
  RESULT_META[String(r || '').toUpperCase().trim()] || { label: 'PENDING', tone: 'pending' }

// Baseline's tracked picks for one book, newest slate first, grouped by slate date.
// Unlike deriveBoard this KEEPS decided picks — the result is the point.
export function derivePicks(record, source = 'prizepicks', slate = null) {
  // The backend scores the two books SEPARATELY and shapes the payload to match:
  // record.picks is PrizePicks-only, and Underdog lives under record.underdog
  // in the same shape. Reading record.picks for both silently returns nothing
  // for Underdog. The pickSource filter stays as a guard, not the selector.
  const src = source === 'underdog' ? (record?.underdog?.picks || []) : (record?.picks || [])
  const picks = src
    .filter(p => !p.excluded_from_record)
    .filter(p => pickSource(p) === source)
  const { startMap, tourMap } = mapsFromSlate(slate)

  const byDate = new Map()
  for (const p of picks) {
    const d = slateDateOf(p)
    if (!d) continue
    const proj = typeof p.model_projection === 'number' ? p.model_projection : null
    const line = typeof p.line === 'number' ? p.line
               : (typeof p.original_line === 'number' ? p.original_line : null)
    const row = {
      key: `${p.id ?? ''}|${p.player}|${p.prop_type}|${p.line}`,
      id: p.id,
      player: p.player,
      opponent: p.opponent || '',
      propType: p.prop_type,
      line,
      lean: (p.lean || '').toUpperCase(),
      projection: proj,
      edge: (proj != null && line != null) ? Math.round((proj - line) * 10) / 10 : null,
      confidence: typeof p.confidence === 'number' ? p.confidence : null,
      result: String(p.result || 'PENDING').toUpperCase().trim(),
      resultValue: typeof p.result_value === 'number' ? p.result_value : null,
      surface: p.surface || '',
      // Picks carry no tour column. The slate is a REAL signal where the name
      // matches; anything else is a guess from the tournament string that
      // defaults to ATP — and would confidently mislabel a WTA player. Flag it
      // so the UI can decline to show a guess as fact.
      tour: lookup(tourMap, p.player) || inferTour(p.tournament),
      tourInferred: !lookup(tourMap, p.player),
      tournament: p.tournament || '',
      oddsType: p.odds_type || 'standard',
      isThreeX: String(p.pick_group || '').toLowerCase().includes('3x'),
      startTs: lookup(startMap, p.player),
    }
    if (!byDate.has(d)) byDate.set(d, [])
    byDate.get(d).push(row)
  }

  const days = [...byDate.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : -1))
    .map(([date, rows]) => {
      rows.sort((a, b) => (b.confidence ?? -1) - (a.confidence ?? -1))
      let w = 0, l = 0, pending = 0
      for (const r of rows) {
        if (r.result === 'W' || r.result === 'PUSH') w++
        else if (r.result === 'L') l++
        else if (r.result !== 'VOID') pending++
      }
      const decided = w + l
      return {
        date, rows, wins: w, losses: l, pending,
        winRate: decided ? Math.round((w / decided) * 1000) / 10 : null,
        settled: pending === 0 && rows.length > 0,
      }
    })
  return { source, days }
}

// Rolling record across the last N slate days that have any decided pick.
export function rollingRecord(days, windowDays = 30) {
  const cutoff = new Date(Date.now() - windowDays * 86400000).toISOString().slice(0, 10)
  let w = 0, l = 0
  for (const d of days) {
    if (d.date < cutoff) continue
    w += d.wins; l += d.losses
  }
  const decided = w + l
  return { wins: w, losses: l, winRate: decided ? Math.round((w / decided) * 1000) / 10 : null, decided }
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
