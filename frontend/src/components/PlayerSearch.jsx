import { useState, useRef, useEffect, useCallback } from 'react'
import { usePlayerSearch } from '../hooks/usePlayerSearch'
import { Search, X } from 'lucide-react'

export default function PlayerSearch({ tour, onSelect, label = 'Search player…', selected }) {
  const { query, setQuery, results, loading, error } = usePlayerSearch(tour)
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  const inputRef = useRef(null)
  const blurTimer = useRef(null)

  // Close on click outside — use a blur-based approach so dropdown clicks register first
  const handleFocus = useCallback(() => {
    clearTimeout(blurTimer.current)
    setOpen(true)
  }, [])

  const handleBlur = useCallback(() => {
    // Delay close so a click on a dropdown item fires before we remove it
    blurTimer.current = setTimeout(() => setOpen(false), 150)
  }, [])

  // Also handle mousedown outside as a safety net
  useEffect(() => {
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    document.addEventListener('touchstart', handler)
    return () => {
      document.removeEventListener('mousedown', handler)
      document.removeEventListener('touchstart', handler)
      clearTimeout(blurTimer.current)
    }
  }, [])

  const pick = (player) => {
    clearTimeout(blurTimer.current)
    onSelect(player)
    setQuery('')
    setOpen(false)
  }

  const clear = () => {
    onSelect(null)
    setQuery('')
    setOpen(false)
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  const showDropdown = open && query.length >= 3

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      {selected ? (
        <div
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '10px 14px', background: 'var(--card)', border: '1px solid var(--green)',
            borderRadius: 8, cursor: 'pointer',
          }}
          onClick={clear}
        >
          <div>
            <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 2 }}>{label}</div>
            <div style={{ fontWeight: 700, color: 'var(--white)' }}>{selected.name}</div>
            {selected.currentRank && <div style={{ fontSize: 11, color: 'var(--muted)' }}>Rank #{selected.currentRank}</div>}
          </div>
          <X size={14} color="var(--muted)" />
        </div>
      ) : (
        <>
          <div style={{ position: 'relative' }}>
            <Search size={14} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--muted)', pointerEvents: 'none' }} />
            <input
              ref={inputRef}
              value={query}
              onChange={e => setQuery(e.target.value)}
              onFocus={handleFocus}
              onBlur={handleBlur}
              placeholder={label}
              autoComplete="off"
              style={{
                width: '100%',
                boxSizing: 'border-box',
                padding: '10px 36px 10px 34px',
                background: 'var(--card)',
                border: '1px solid var(--border)',
                borderRadius: 8,
                color: 'var(--white)',
                fontSize: 14,
                outline: 'none',
              }}
            />
            {loading && (
              <div style={{
                position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)',
                width: 14, height: 14,
                border: '2px solid var(--border)', borderTopColor: 'var(--green)',
                borderRadius: '50%', animation: 'spin .6s linear infinite',
              }} />
            )}
          </div>
          {showDropdown && (
            <div style={{
              position: 'absolute', top: '100%', left: 0, right: 0,
              zIndex: 1000,
              background: '#1a1a1a', border: '1px solid var(--border)',
              borderRadius: 8, marginTop: 4, overflow: 'hidden',
              boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
            }}>
              {loading ? (
                <div style={{ padding: '12px 14px', color: 'var(--muted)', fontSize: 13 }}>
                  Searching…
                </div>
              ) : error ? (
                <div style={{ padding: '12px 14px', color: '#FF4444', fontSize: 13 }}>
                  ⚠ {error}
                </div>
              ) : results.length > 0 ? (
                results.slice(0, 6).map(p => (
                  <div
                    key={p.id}
                    onMouseDown={e => { e.preventDefault(); pick(p) }}
                    style={{
                      padding: '10px 14px', cursor: 'pointer',
                      borderBottom: '1px solid var(--border)',
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    }}
                    onMouseEnter={e => e.currentTarget.style.background = '#222'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                  >
                    <div>
                      <div style={{ fontWeight: 600, fontSize: 14, color: 'var(--white)' }}>{p.name}</div>
                      {p.countryAcr && <div style={{ fontSize: 11, color: 'var(--muted)' }}>{p.countryAcr}</div>}
                    </div>
                    {p.currentRank && <span style={{ fontSize: 11, color: 'var(--muted)' }}>#{p.currentRank}</span>}
                  </div>
                ))
              ) : (
                <div style={{ padding: '12px 14px', color: 'var(--muted)', fontSize: 13 }}>
                  No players found
                </div>
              )}
            </div>
          )}
        </>
      )}
      <style>{`@keyframes spin { to { transform: translateY(-50%) rotate(360deg); } }`}</style>
    </div>
  )
}
