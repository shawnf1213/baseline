import { useState, useEffect } from 'react'
import { fetchStats } from '../utils/api'

export function usePlayerStats(playerId, tour, playerName = '') {
  const [stats, setStats]   = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]   = useState(null)

  useEffect(() => {
    if (!playerId) { setStats(null); return }
    setLoading(true); setError(null)
    fetchStats(String(playerId), tour, playerName)
      .then(data => setStats(data))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [playerId, tour])

  return { stats, loading, error }
}
