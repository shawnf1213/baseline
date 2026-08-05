import { useSyncExternalStore, useCallback } from 'react'

// Recent player searches — localStorage, capped, most-recent-first.
const KEY = 'baseline_recent_players'
const CAP = 12
let cache = load()
const subs = new Set()

function load() { try { return JSON.parse(localStorage.getItem(KEY) || '[]') } catch { return [] } }
function persist(next) {
  cache = next
  try { localStorage.setItem(KEY, JSON.stringify(next)) } catch { /* quota */ }
  subs.forEach(f => f())
}
function subscribe(f) { subs.add(f); return () => subs.delete(f) }
function snapshot() { return cache }

export function useRecentPlayers() {
  const list = useSyncExternalStore(subscribe, snapshot, snapshot)
  const push = useCallback((p) => {
    if (!p?.id && !p?.name) return
    const id = String(p.id ?? p.name)
    const next = [{ id: p.id, name: p.name, currentRank: p.currentRank ?? null, tour: p.tour || 'ATP', ts: Date.now() },
      ...cache.filter(x => String(x.id ?? x.name) !== id)].slice(0, CAP)
    persist(next)
  }, [])
  const clear = useCallback(() => persist([]), [])
  return { list, push, clear }
}
