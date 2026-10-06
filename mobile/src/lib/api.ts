// The one HTTP client. Every screen goes through here, for three reasons the
// website's utils/api.js learned the hard way:
//
//   1. ONE BASE URL. The web client picks its origin per hostname; a native
//      app has no hostname, so it reads EXPO_PUBLIC_API_URL once. That value is
//      public by definition (it ships in the bundle) and carries no secret.
//
//   2. ONE PLACE THE SESSION IS ATTACHED. The backend expects
//      `Authorization: Bearer <session>` (see /api/auth/me in main.py). The
//      token is read from secure storage by the auth module and injected here;
//      no screen ever handles it directly.
//
//   3. ONE TIMEOUT POLICY. A cold /api/prop/calculate can take minutes while a
//      search takes 150ms, so the pricing calls take a long budget and
//      everything else a short one — the same split calcProp vs api.get has on
//      the web.
//
// Phase 1 ships the client with a NO-OP token source. Phase 2 wires secure
// storage; nothing here changes shape when it does.

import Constants from 'expo-constants'

const BASE = (process.env.EXPO_PUBLIC_API_URL
  || (Constants.expoConfig?.extra as any)?.apiUrl
  || 'https://backend-production-84ab.up.railway.app').replace(/\/+$/, '')

export const API_BASE = BASE

// Replaced by the auth module in Phase 2. Kept as a hook rather than an import
// so lib/api never depends on the auth layer (which depends on this).
let _tokenSource: () => Promise<string | null> = async () => null
export function setTokenSource(fn: () => Promise<string | null>) { _tokenSource = fn }

export class ApiError extends Error {
  status: number
  body: unknown
  constructor(status: number, body: unknown, message?: string) {
    super(message || `HTTP ${status}`)
    this.status = status
    this.body = body
  }
}

const SHORT_MS = 30_000
const LONG_MS = 300_000   // pricing — matches calcProp's 5-minute budget on the web

async function request<T>(method: 'GET' | 'POST' | 'DELETE', path: string,
                          opts: { body?: unknown; params?: Record<string, any>;
                                  timeoutMs?: number; signal?: AbortSignal } = {}): Promise<T> {
  const url = new URL(BASE + path)
  for (const [k, v] of Object.entries(opts.params || {})) {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v))
  }
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  const tok = await _tokenSource()
  if (tok) headers.Authorization = `Bearer ${tok}`

  // Timeout via AbortController, composed with a caller's own signal so a
  // screen unmounting still cancels an in-flight pricing call.
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), opts.timeoutMs ?? SHORT_MS)
  opts.signal?.addEventListener('abort', () => ctl.abort())
  try {
    const r = await fetch(url.toString(), {
      method, headers, signal: ctl.signal,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    })
    const text = await r.text()
    let data: unknown = null
    try { data = text ? JSON.parse(text) : null } catch { data = text }
    if (!r.ok) throw new ApiError(r.status, data)
    return data as T
  } finally {
    clearTimeout(timer)
  }
}

export const api = {
  get:  <T,>(path: string, params?: Record<string, any>, signal?: AbortSignal) =>
          request<T>('GET', path, { params, signal }),
  post: <T,>(path: string, body?: unknown, signal?: AbortSignal) =>
          request<T>('POST', path, { body, signal }),
  // Pricing endpoints only — the long budget. Everything else stays short so
  // a hung request cannot pin a screen for five minutes.
  price: <T,>(path: string, body: unknown, signal?: AbortSignal) =>
          request<T>('POST', path, { body, signal, timeoutMs: LONG_MS }),
}

// ── Read-only endpoints the screens will use (Phase 4). Listed now so the
// surface the app touches is declared in one place and reviewable against the
// Phase 0 endpoint groups — nothing admin or diagnostic appears here.
export const fetchSummary   = () => api.get<any>('/api/results/summary')
export const fetchRecord    = () => api.get<any>('/api/results/record')
export const fetchSlate     = () => api.get<any>('/api/slate/today')
export const fetchCourts    = () => api.get<any>('/api/courts')
export const searchPlayers  = (query: string, tour: string) =>
  api.get<any[]>('/api/search', { query, tour })
export const fetchStats     = (player_id: string, tour: string, player_name = '') =>
  api.post<any>('/api/player/stats', { player_id, tour, player_name })
export const fetchForm      = (player_id: string, tour: string) =>
  api.get<any>('/api/player/form', { player_id, tour })
export const fetchHistory   = (player_id: string, tour: string, prop: string,
                               surface: string, line = 0) =>
  api.get<any>('/api/history', { player_id, tour, prop, surface, line })
export const fetchNextMatch = (player_id: string, tour: string) =>
  api.get<any>('/api/player/next-match', { player_id, tour })
export const calcProp       = (body: unknown, signal?: AbortSignal) =>
  api.price<any>('/api/prop/calculate', body, signal)
export const fetchNbaBoard  = () => api.get<any>('/api/nba/board')
export const fetchNbaRecord = () => api.get<any>('/api/nba/results/record')
