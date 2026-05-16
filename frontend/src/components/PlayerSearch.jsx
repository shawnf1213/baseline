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
            background: '#0a0f0c', border: '1px solid #00e676',
            borderRadius: 12, padding: '14px 16px', cursor: 'pointer',
            boxShadow: '0 0 0 1px #00e67615',
            transition: 'all .2s',
          }}
          onClick={clear}
        >
          <div style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 9, letterSpacing: 3, textTransform: 'uppercase', color: '#00e676', marginBottom: 6 }}>{label}</div>
          <div style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 900, fontSize: 22, color: '#fff', letterSpacing: 0.5, lineHeight: 1.1 }}>{selected.name}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
            {selected.currentRank && (
              <span style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 9, letterSpacing: 1.5, textTransform: 'uppercase', background: '#0f2010', color: '#00e676', border: '1px solid #1a4020', padding: '2px 8px', borderRadius: 4 }}>
                Rank #{selected.currentRank}
              </span>
            )}
            {selected.countryAcr && (
              <span style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 9, letterSpacing: 1, textTransform: 'uppercase', background: '#0d1a35', color: '#6b9fff', border: '1px solid #1a3060', padding: '2px 8px', borderRadius: 4 }}>
                {selected.countryAcr}
              </span>
            )}
            <X size={12} color="#1a4025" style={{ marginLeft: 'auto' }} />
          </div>
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
                width: '100%', boxSizing: 'border-box',
                padding: '13px 36px 13px 38px',
                background: '#0a0f0c',
                border: '1px solid #1a2520',
                borderRadius: 12,
                color: '#4a6a50',
                fontSize: 15,
                fontFamily: '"Barlow Condensed", sans-serif',
                fontWeight: 600,
                letterSpacing: 1,
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
              background: '#080d09', border: '1px solid #1a2520',
              borderRadius: 10, marginTop: 6, overflow: 'hidden',
              boxShadow: '0 12px 32px rgba(0,0,0,0.8)',
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
                    onMouseEnter={e => e.currentTarget.style.background = '#0d1510'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                  >
                    <div>
                      <div style={{ fontWeight: 700, fontSize: 14, fontFamily: '"Barlow Condensed", sans-serif', color: '#fff' }}>{p.name}</div>
                      {p.countryAcr && <div style={{ fontSize: 10, color: '#2a3a30', fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 1 }}>{p.countryAcr}</div>}
                    </div>
                    {p.currentRank && <span style={{ fontSize: 10, color: '#1a3a25', fontFamily: '"Barlow Condensed", sans-serif' }}>#{p.currentRank}</span>}
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
