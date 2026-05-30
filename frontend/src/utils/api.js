import axios from 'axios'

// Strip BOM (﻿) that PowerShell can inject when piping env vars to Vercel CLI
const BASE = (import.meta.env.VITE_API_URL || '').replace(/^﻿/, '').trim() || 'http://localhost:8000'

export const api = axios.create({ baseURL: BASE, timeout: 60000 })

export const searchPlayers  = (query, tour, signal) =>
  api.get('/api/search', { params: { query, tour }, signal }).then(r => r.data)
export const fetchStats     = (player_id, tour, player_name = '') => api.post('/api/player/stats', { player_id, tour, player_name }).then(r => r.data)
// Prop calculate can take up to 5 min when fetching two uncached players from Sofascore
export const calcProp       = (body) => api.post('/api/prop/calculate', body, { timeout: 300000 }).then(r => r.data)
export const fetchH2H       = (body) => api.post('/api/h2h', body).then(r => r.data)

