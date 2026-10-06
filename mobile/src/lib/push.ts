// Push notifications — registering this device, keeping its preferences, and
// letting go of it on sign out.
//
// The token is Expo's push token for this install; the backend stores it
// against the signed-in member and fans out when a board or a recap posts
// (backend/src/push.py). Registration needs the EAS project id, which exists
// once `eas init` has run — until then this is a quiet no-op, so nothing here
// can break Expo Go testing.
import { Platform } from 'react-native'
import Constants from 'expo-constants'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { api } from './api'
import type { SportKey } from './sports'

export type PushEvent = 'board' | 'recap'
export type PushPrefs = Record<PushEvent, Record<SportKey, boolean>>
export const DEFAULT_PREFS: PushPrefs = {
  board: { tennis: true, nfl: true, nba: true, mlb: true },
  recap: { tennis: true, nfl: true, nba: true, mlb: true },
}
const TOKEN_KEY = 'baseline.push.token'
const PREFS_KEY = 'baseline.push.prefs'

function projectId(): string | null {
  const id = (Constants.expoConfig?.extra as any)?.eas?.projectId
    || (Constants as any).easConfig?.projectId
  if (!id || typeof id !== 'string' || id.startsWith('SET_BY')) return null
  return id
}

export async function loadPrefs(): Promise<PushPrefs> {
  try {
    const raw = await AsyncStorage.getItem(PREFS_KEY)
    if (raw) return { ...DEFAULT_PREFS, ...JSON.parse(raw) }
  } catch { /* fall through */ }
  return DEFAULT_PREFS
}

export async function savePrefs(p: PushPrefs): Promise<void> {
  try { await AsyncStorage.setItem(PREFS_KEY, JSON.stringify(p)) } catch { /* ignore */ }
  const tok = await storedToken()
  if (tok) { try { await api.post('/api/push/prefs', { token: tok, prefs: p }) } catch { /* next launch retries */ } }
}

async function storedToken(): Promise<string | null> {
  try { return await AsyncStorage.getItem(TOKEN_KEY) } catch { return null }
}

// Ask for permission (iOS prompts once), fetch the token, register it with
// the current preferences. Safe to call on every app start while signed in.
export async function registerForPush(): Promise<'registered' | 'denied' | 'unavailable'> {
  if (Platform.OS === 'web') return 'unavailable'
  const pid = projectId()
  if (!pid) return 'unavailable'
  try {
    const Notifications = await import('expo-notifications')
    const Device = await import('expo-device')
    if (!Device.isDevice) return 'unavailable'
    const current = await Notifications.getPermissionsAsync()
    let status = current.status
    if (status !== 'granted') status = (await Notifications.requestPermissionsAsync()).status
    if (status !== 'granted') return 'denied'
    const { data: token } = await Notifications.getExpoPushTokenAsync({ projectId: pid })
    if (!token) return 'unavailable'
    const prefs = await loadPrefs()
    await api.post('/api/push/register', { token, platform: Platform.OS, prefs })
    try { await AsyncStorage.setItem(TOKEN_KEY, token) } catch { /* ignore */ }
    return 'registered'
  } catch {
    return 'unavailable'
  }
}

// On sign out and account deletion: tell the backend to forget this device
// BEFORE the session is cleared (the call needs it), then forget locally.
export async function unregisterPush(): Promise<void> {
  const tok = await storedToken()
  if (tok) { try { await api.post('/api/push/unregister', { token: tok }) } catch { /* best effort */ } }
  try { await AsyncStorage.removeItem(TOKEN_KEY) } catch { /* ignore */ }
}

// How a tapped notification is routed: the Picks tab of that sport.
export function routeFor(data: any): { sport: SportKey | null } {
  const s = String(data?.sport || '')
  return { sport: (['tennis', 'nfl', 'nba', 'mlb'].includes(s) ? (s as SportKey) : null) }
}
