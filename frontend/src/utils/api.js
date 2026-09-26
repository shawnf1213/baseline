import axios from 'axios'

// Where /api requests go:
//  • Production host → straight to the backend (its origin is CORS-allowlisted).
//    A DIRECT call avoids a proxy hop, so the slow prop-calculate endpoint
//    (minutes for a cold player) never hits a Vercel edge timeout.
//  • Everything else — preview (*.vercel.app hashes) AND local dev — uses the
//    SAME-ORIGIN `/api` proxy (vercel.json rewrite in prod, Vite dev proxy
//    locally). Critically we do NOT fall back to VITE_API_URL here: Vercel sets
//    it to the backend origin, and calling that cross-origin from a preview URL
//    the backend hasn't CORS-allowlisted fails every request.
function resolveBase() {
  if (typeof window === 'undefined') return ''
  // Both public hostnames go DIRECT to the backend. Without listing a host
  // here it falls back to the same-origin /api rewrite, which runs through a
  // Vercel edge function with a hard timeout — and a cold prop-calculate can
  // take minutes, so projections would fail on the new domain while working on
  // the old one. Every public origin must appear in BOTH this list and the
  // backend's CORS allowlist.
  const DIRECT = ['baselineev.com', 'www.baselineev.com',
                  'baselineev.vercel.app', 'baseline-app-three.vercel.app']
  if (DIRECT.includes(window.location.hostname)) {
    return 'https://backend-production-84ab.up.railway.app'
  }
  return ''
}
const BASE = resolveBase()

export const api = axios.create({ baseURL: BASE, timeout: 60000 })

// PrizePicks is CORS-locked to browsers, so it ALWAYS goes through the same-origin
// `/pp` proxy (Vercel rewrite in prod, Vite proxy in dev) — never cross-origin.
export const fetchPrizePicksBoard = (signal) =>
  axios.get('/pp/projections?per_page=1000', { timeout: 20000, signal }).then(r => r.data)

// Underdog publishes its whole board on one unauthenticated endpoint, but it is
// CORS-locked to browsers, so it takes the same same-origin `/ud` proxy treatment.
//
// `v1`, NOT `beta/v6`. The beta prefix now answers 426 "a new version is
// required to continue" to everyone, which reads like a block and is not one —
// they shipped a new client and retired that path. The timeout is generous
// because the response is ~25MB: every sport on one endpoint.
export const fetchUnderdogBoard = (signal) =>
  axios.get('/ud/v1/over_under_lines', { timeout: 45000, signal }).then(r => r.data)

export const searchPlayers  = (query, tour, signal) =>
  api.get('/api/search', { params: { query, tour }, signal }).then(r => r.data)
export const fetchStats     = (player_id, tour, player_name = '') => api.post('/api/player/stats', { player_id, tour, player_name }).then(r => r.data)
// Prop calculate can take up to 5 min when fetching two uncached players from Sofascore
export const calcProp       = (body) => api.post('/api/prop/calculate', body, { timeout: 300000 }).then(r => r.data)
export const fetchH2H       = (body) => api.post('/api/h2h', body).then(r => r.data)

// ── Read-only endpoints used by the mobile research app ──────────────────────
// All GETs against existing backend routes — no new server code required.
export const fetchSlate     = (signal) =>
  api.get('/api/slate/today', { signal }).then(r => r.data)
// The tournament picker, tour-split, with each venue's ST Pace Index. Served
// rather than hardcoded so the bot and this app cannot drift apart again — see
// useTournamentConfig().
export const fetchCourts    = (signal) =>
  api.get('/api/courts', { signal }).then(r => r.data)
export const fetchForm      = (player_id, tour, signal) =>
  api.get('/api/player/form', { params: { player_id, tour }, signal }).then(r => r.data)
export const fetchHistory   = (player_id, tour, prop, surface, line = 0, signal) =>
  api.get('/api/history', { params: { player_id, tour, prop, surface, line }, signal }).then(r => r.data)
export const fetchNextMatch = (player_id, tour, signal) =>
  api.get('/api/player/next-match', { params: { player_id, tour }, signal }).then(r => r.data)
// The full public pick log — the mobile Board re-frames today's rows from this
// as neutral research data (the full PrizePicks market is not persisted server-side).
// The FULL scanned NFL market — every line the model could price, with our
// number beside it. This is the board; fetchNflRecord is the record of what was
// posted. Different things, different tables.
export const fetchNflBoard  = (slate_date, signal) =>
  api.get('/api/nfl/board', { params: { slate_date }, signal }).then(r => r.data)

// Published player profiles — role, usage, matchup splits and recent form.
// Still PUBLISHED by the bot rather than computed on request: a profile reads
// several parquet datasets and the board needs 117 of them, which is a batch
// job, not a page load. The backend can now compute them (backend/nfl), so this
// is a caching decision rather than a limitation.
export const fetchNflPlayer = (player, signal) =>
  api.get('/api/nfl/players', { params: { player }, signal }).then(r => r.data)

// ── NFL PRICING ──────────────────────────────────────────────────────────────
// The NFL answer to calcProp. These exist because the model moved into the
// backend (backend/nfl) — before that the website could only show what the bot
// had already scanned, never price something new.
export const searchNflPlayers = (query, signal) =>
  api.get('/api/nfl/search', { params: { query }, signal }).then(r => r.data)

export const fetchNflPropTypes = (signal) =>
  api.get('/api/nfl/props', { signal }).then(r => r.data)

// Same generous timeout as calcProp: a player the container has not touched
// today pulls several nflverse parquet datasets before it can answer.
export const projectNfl = (body) =>
  api.post('/api/nfl/project', body, { timeout: 300000 }).then(r => r.data)

export const fetchNflRecord = (signal) =>
  api.get('/api/nfl/results/record', { signal }).then(r => r.data)

export const fetchRecord    = (signal) =>
  api.get('/api/results/record', { signal }).then(r => r.data)

// The headline record only — a few hundred bytes, computed server-side. The
// landing page is public and must paint fast; fetchRecord ships the whole pick
// log (620KB) and is for the in-app track record, where that detail is the
// point.
export const fetchPublicRecord = (signal) =>
  api.get('/api/results/summary', { signal }).then(r => r.data)

