import axios from 'axios'

// Strip BOM (﻿) that PowerShell can inject when piping env vars to Vercel CLI
const RAW = (import.meta.env.VITE_API_URL || '').replace(/^﻿/, '').trim()
// Deployed builds (Vercel — production AND branch previews) call the SAME-ORIGIN
// `/api` proxy defined in vercel.json, which reverse-proxies to the backend.
// This sidesteps cross-origin CORS entirely, so any *.vercel.app preview works
// without the backend having to allowlist its URL. Local dev talks to the
// backend directly via VITE_API_URL (localhost:5173 is CORS-allowlisted).
const BASE = import.meta.env.PROD ? '' : (RAW || 'http://localhost:8000')

export const api = axios.create({ baseURL: BASE, timeout: 60000 })

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

