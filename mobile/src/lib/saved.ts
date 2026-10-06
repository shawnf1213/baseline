// Saved players and recently viewed players — on this device only (operator
// ruling 4: Saved is on-device). AsyncStorage, one JSON list each, with a
// module-level copy and listeners so every screen that shows the list sees
// the same one without a round trip to storage.
import { useEffect, useState } from 'react'
import AsyncStorage from '@react-native-async-storage/async-storage'
import type { SportKey } from './sports'
import { normName } from './picks'

export type SavedPlayer = {
  key: string
  sport: SportKey
  name: string
  id?: string | null
  tour?: string
  team?: string | null
  rank?: number | null
  at: number
}

const SAVED_KEY = 'baseline.saved.v1'
const RECENT_KEY = 'baseline.recent.v1'
const RECENT_MAX = 12

export const playerKey = (sport: SportKey, name: string) => `${sport}|${normName(name)}`

type Store = { list: SavedPlayer[]; loaded: boolean; subs: Set<() => void> }
const stores: Record<string, Store> = {
  [SAVED_KEY]: { list: [], loaded: false, subs: new Set() },
  [RECENT_KEY]: { list: [], loaded: false, subs: new Set() },
}

async function load(key: string) {
  const st = stores[key]
  if (st.loaded) return
  try {
    const raw = await AsyncStorage.getItem(key)
    const parsed = raw ? JSON.parse(raw) : []
    st.list = Array.isArray(parsed) ? parsed.filter(p => p && p.key && p.name && p.sport) : []
  } catch { st.list = [] }
  st.loaded = true
  st.subs.forEach(fn => fn())
}

async function save(key: string, list: SavedPlayer[]) {
  const st = stores[key]
  st.list = list
  st.subs.forEach(fn => fn())
  try { await AsyncStorage.setItem(key, JSON.stringify(list)) } catch { /* storage full or unavailable */ }
}

function useStore(key: string) {
  const [, tick] = useState(0)
  useEffect(() => {
    const st = stores[key]
    const fn = () => tick(n => n + 1)
    st.subs.add(fn)
    load(key)
    return () => { st.subs.delete(fn) }
  }, [key])
  return stores[key]
}

export function useSaved() {
  const st = useStore(SAVED_KEY)
  const has = (key: string) => st.list.some(p => p.key === key)
  const toggle = (p: Omit<SavedPlayer, 'key' | 'at'>) => {
    const key = playerKey(p.sport, p.name)
    const next = has(key) ? st.list.filter(x => x.key !== key)
      : [{ ...p, key, at: Date.now() }, ...st.list]
    save(SAVED_KEY, next)
  }
  return { list: st.list, ready: st.loaded, has, toggle }
}

export function useRecent() {
  const st = useStore(RECENT_KEY)
  const push = (p: Omit<SavedPlayer, 'key' | 'at'>) => {
    const key = playerKey(p.sport, p.name)
    const next = [{ ...p, key, at: Date.now() }, ...st.list.filter(x => x.key !== key)].slice(0, RECENT_MAX)
    save(RECENT_KEY, next)
  }
  const clear = () => save(RECENT_KEY, [])
  return { list: st.list, ready: st.loaded, push, clear }
}
