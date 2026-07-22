import { useSyncExternalStore, useCallback } from 'react'

// Bookmarks live entirely in localStorage — no auth, no server. Shared across
// every mounted component via useSyncExternalStore so a heart toggled on the
// Board instantly reflects in My Research.
const KEY = 'baseline_bookmarks'
let cache = load()
const subs = new Set()

function load() { try { return JSON.parse(localStorage.getItem(KEY) || '{}') } catch { return {} } }
function persist(next) {
  cache = next
  try { localStorage.setItem(KEY, JSON.stringify(next)) } catch { /* quota */ }
  subs.forEach(f => f())
}
function subscribe(f) { subs.add(f); return () => subs.delete(f) }
function snapshot() { return cache }

export const propBookmarkId   = (r) => `prop:${r.player}|${r.propType}|${r.line}`
export const playerBookmarkId = (name) => `player:${name}`

export function useBookmarks() {
  const map = useSyncExternalStore(subscribe, snapshot, snapshot)
  const toggle = useCallback((item) => {
    const n = { ...cache }
    if (n[item.id]) delete n[item.id]
    else n[item.id] = { ...item, savedAt: Date.now() }
    persist(n)
  }, [])
  const remove = useCallback((id) => { const n = { ...cache }; delete n[id]; persist(n) }, [])
  const has = useCallback((id) => !!cache[id], [map])
  const list = Object.values(map).sort((a, b) => (b.savedAt || 0) - (a.savedAt || 0))
  return { map, list, toggle, remove, has }
}
