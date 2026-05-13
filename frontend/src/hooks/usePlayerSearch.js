import { useState, useEffect, useRef } from 'react'
import { searchPlayers } from '../utils/api'

export function usePlayerSearch(tour) {
  const [query, setQuery]     = useState('')
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const timer = useRef(null)

  useEffect(() => {
    if (query.length < 3) { setResults([]); return }
    clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      setLoading(true)
      try {
        const data = await searchPlayers(query, tour)
        setResults(data || [])
      } catch { setResults([]) }
      finally { setLoading(false) }
    }, 400)
    return () => clearTimeout(timer.current)
  }, [query, tour])

  return { query, setQuery, results, loading }
}
