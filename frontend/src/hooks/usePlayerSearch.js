import { useState, useEffect, useRef } from 'react'
import { searchPlayers } from '../utils/api'

export function usePlayerSearch(tour) {
  const [query,   setQuery]   = useState('')
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(null)
  const timer      = useRef(null)
  const controller = useRef(null)   // AbortController for in-flight request

  useEffect(() => {
    if (query.length < 3) {
      setResults([])
      setError(null)
      setLoading(false)
      return
    }

    clearTimeout(timer.current)
    // Cancel any previous in-flight search immediately
    if (controller.current) controller.current.abort()

    timer.current = setTimeout(async () => {
      controller.current = new AbortController()
      setLoading(true)
      setError(null)

      // 10-second wall-clock guard — even if the backend call hangs,
      // the UI will stop spinning and show a message.
      const timeoutId = setTimeout(() => {
        if (controller.current) controller.current.abort()
      }, 10_000)

      try {
        const data = await searchPlayers(query, tour, controller.current.signal)
        console.log('[search] results:', data?.length ?? 0, 'for', query)
        setResults(Array.isArray(data) ? data : [])
      } catch (err) {
        if (err?.name === 'AbortError' || err?.code === 'ERR_CANCELED') {
          // Intentional cancel (new keystroke or timeout) — clear gracefully
          setResults([])
          if (err?.message?.includes('timeout') || err?.code === 'ERR_CANCELED') {
            setError('Search timed out — try again')
          }
          // If aborted by a new keystroke don't set an error
        } else {
          console.error('[search] error:', err?.response?.status, err?.message)
          setError(err?.response?.data?.detail || err?.message || 'Search failed')
          setResults([])
        }
      } finally {
        clearTimeout(timeoutId)
        setLoading(false)
        controller.current = null
      }
    }, 400)

    return () => {
      clearTimeout(timer.current)
      if (controller.current) {
        controller.current.abort()
        controller.current = null
      }
    }
  }, [query, tour])

  return { query, setQuery, results, loading, error }
}
