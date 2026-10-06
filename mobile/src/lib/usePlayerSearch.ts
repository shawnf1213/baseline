// Debounced player search against /api/search — the website's hook, with the
// same 3-character floor and cancel-on-keystroke so a slow Sofascore answer
// for "sin" never lands on top of the results for "sinner".
import { useEffect, useRef, useState } from 'react'
import { ApiError, searchPlayers } from './api'

export type SearchHit = { id: string; name: string; gender?: string; currentRank?: number | null }

export function usePlayerSearch(tour: string) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchHit[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const ctl = useRef<AbortController | null>(null)

  useEffect(() => {
    if (query.trim().length < 3) { setResults([]); setError(null); setLoading(false); return }
    ctl.current?.abort()
    const t = setTimeout(async () => {
      const c = new AbortController()
      ctl.current = c
      setLoading(true); setError(null)
      try {
        const data = await searchPlayers(query.trim(), tour)
        if (c.signal.aborted) return
        setResults(Array.isArray(data) ? data.map(r => ({
          id: String(r.id), name: String(r.name || ''), gender: r.gender,
          currentRank: typeof r.currentRank === 'number' ? r.currentRank : null,
        })) : [])
      } catch (e: any) {
        if (c.signal.aborted) return
        setResults([])
        setError(e instanceof ApiError && e.status === 503
          ? 'Player search is busy — try again in a minute'
          : 'Search failed — try again')
      } finally {
        if (!c.signal.aborted) setLoading(false)
      }
    }, 350)
    return () => { clearTimeout(t); ctl.current?.abort() }
  }, [query, tour])

  return { query, setQuery, results, loading, error }
}
