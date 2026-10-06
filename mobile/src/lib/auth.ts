// Sign-in, session storage and entitlement — the app's side of the EXISTING
// backend auth (ruling 1). Nothing here is new identity: it is the same Discord
// OAuth, the same magic-link email (now carrying a code), the same HMAC session
// token and the same /api/auth/me the website uses.
//
// THE SESSION LIVES IN SECURE STORAGE ONLY. expo-secure-store is the iOS
// Keychain. It is never mirrored into AsyncStorage, never logged, and never
// placed in a URL by this file — the one URL it ever travels in is the
// backend's own bounce to baseline://auth, which is a custom-scheme redirect
// handled on-device by the system auth session and never hits the network.
//
// DISCORD RUNS IN THE SYSTEM AUTH SESSION (ASWebAuthenticationSession on iOS):
// Safari's cookies, Safari's password manager, and a browser the app cannot
// read. The flow is the website's flow with one difference — the website asks
// the callback to bounce to https://baselineev.com, the app asks it to bounce
// to baseline://auth. The redirect registered with Discord is the backend
// callback either way, so nothing changes in the Discord developer portal.
import { Platform } from 'react-native'
import * as SecureStore from 'expo-secure-store'
import * as WebBrowser from 'expo-web-browser'
import * as Linking from 'expo-linking'
import { api, setTokenSource } from './api'

const KEY = 'baseline.session'
// Must match app.json `scheme` and the backend's safe_return() allowlist.
export const RETURN_URL = 'baseline://auth'

export type Me = {
  authenticated: boolean
  active: boolean
  reason?: string
  owner?: boolean
  discord_id?: string
  username?: string
  email?: string
  source?: string | null
  invite_url?: string
}

// ON WEB (used only to render store screenshots and for local checks) the
// keychain does not exist; localStorage stands in. The shipped app is iOS.
const WEB = Platform.OS === 'web'

export async function getSession(): Promise<string | null> {
  if (WEB) { try { return globalThis.localStorage?.getItem(KEY) ?? null } catch { return null } }
  try { return await SecureStore.getItemAsync(KEY) } catch { return null }
}
async function setSession(tok: string) {
  if (WEB) { try { globalThis.localStorage?.setItem(KEY, tok) } catch { /* private mode */ }; return }
  await SecureStore.setItemAsync(KEY, tok, {
    keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK,
  })
}
export async function clearSession() {
  if (WEB) { try { globalThis.localStorage?.removeItem(KEY) } catch { /* ignore */ }; return }
  try { await SecureStore.deleteItemAsync(KEY) } catch { /* already gone */ }
}

// api.ts attaches the Bearer header from whatever this returns.
setTokenSource(getSession)

// ── Discord ──────────────────────────────────────────────────────────────────
export async function signInWithDiscord(force = false): Promise<Me> {
  const { url } = await api.get<{ url: string }>('/api/auth/login',
    { redirect: RETURN_URL, ...(force ? { force: 1 } : {}) })
  const res = await WebBrowser.openAuthSessionAsync(url, RETURN_URL,
    { preferEphemeralSession: false })
  if (res.type !== 'success' || !res.url) {
    throw new Error(res.type === 'cancel' || res.type === 'dismiss'
      ? 'Sign-in cancelled' : 'Sign-in did not complete')
  }
  const tok = Linking.parse(res.url).queryParams?.session
  if (typeof tok !== 'string' || !tok) throw new Error('No session returned')
  await setSession(tok)
  return fetchMe()
}

// ── Email code ───────────────────────────────────────────────────────────────
// The backend answers identically whether or not the address is a subscriber
// (magic_link.request_link), so the app can only ever say "check your inbox".
export async function requestEmailCode(email: string): Promise<void> {
  await api.post('/api/auth/magic/request', { email: email.trim() })
}

export async function verifyEmailCode(email: string, code: string): Promise<Me> {
  // Sent as typed (whitespace stripped), not digit-filtered: the backend
  // normalises the six-digit case itself, and the reviewer's fixed code is
  // not numeric.
  const r = await api.post<{ session: string }>('/api/auth/magic/code',
    { email: email.trim(), code: code.replace(/\s/g, '') })
  await setSession(r.session)
  return fetchMe()
}

// ── Entitlement ──────────────────────────────────────────────────────────────
// Re-read from the server every time; never cached on-device beyond the
// in-memory React state. A lapsed membership takes effect on the next check,
// which is the website's behaviour and the only honest one.
export async function fetchMe(): Promise<Me> {
  const tok = await getSession()
  if (!tok) return { authenticated: false, active: false }
  try {
    return await api.get<Me>('/api/auth/me')
  } catch {
    // A network failure must not sign the user out — it reads as "cannot
    // verify right now", and the gate holds the last known state.
    throw new Error('offline')
  }
}

export async function signOut(): Promise<void> {
  await clearSession()
}
