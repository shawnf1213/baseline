import { useState, useEffect, useRef } from 'react'

import { searchPlayers } from '../utils/api'

export function usePlayerSearch(tour) {
  const [query, setQuery]     = useState('')
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)
  const timer = useRef(null)

  useEffect(() => {
    if (query.length < 3) { setResults([]); setError(null); return }
    clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      setLoading(true)
      setError(null)
      try {
        const data = await searchPlayers(query, tour)
        console.log('[search] results:', data)
        setResults(Array.isArray(data) ? data : [])
      } catch (err) {
        console.error('[search] error:', err?.response?.status, err?.message, err)
        setError(err?.response?.data?.detail || err?.message || 'Search failed')
        setResults([])
      } finally {
        setLoading(false)
      }
    }, 400)
    return () => clearTimeout(timer.current)
  }, [query, tour])

  return { query, setQuery, results, loading, error }
}
