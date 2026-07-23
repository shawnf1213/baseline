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
  if (window.location.hostname === 'baseline-app-three.vercel.app') {
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
export const fetchForm      = (player_id, tour, signal) =>
  api.get('/api/player/form', { params: { player_id, tour }, signal }).then(r => r.data)
export const fetchHistory   = (player_id, tour, prop, surface, line = 0, signal) =>
  api.get('/api/history', { params: { player_id, tour, prop, surface, line }, signal }).then(r => r.data)
export const fetchNextMatch = (player_id, tour, signal) =>
  api.get('/api/player/next-match', { params: { player_id, tour }, signal }).then(r => r.data)
// The full public pick log — the mobile Board re-frames today's rows from this
// as neutral research data (the full PrizePicks market is not persisted server-side).
export const fetchRecord    = (signal) =>
  api.get('/api/results/record', { signal }).then(r => r.data)

