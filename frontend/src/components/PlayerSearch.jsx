import { useState, useRef, useEffect } from 'react'
import { usePlayerSearch } from '../hooks/usePlayerSearch'
import { Search, X } from 'lucide-react'

export default function PlayerSearch({ tour, onSelect, label = 'Search player…', selected }) {
  const { query, setQuery, results, loading } = usePlayerSearch(tour)
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    const handler = (e) => { if (!ref.current?.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    document.addEventListener('touchstart', handler)
    return () => {
      document.removeEventListener('mousedown', handler)
      document.removeEventListener('touchstart', handler)
    }
  }, [])

  const pick = (player) => {
    onSelect(player)
    setQuery('')
    setOpen(false)
  }

  const clear = () => { onSelect(null); setQuery('') }

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      {selected ? (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '10px 14px', background: 'var(--card)', border: '1px solid var(--green)',
          borderRadius: 8, cursor: 'pointer'
        }} onClick={clear}>
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
            <Search size={14} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--muted)' }} />
            <input
              value={query}
              onChange={e => { setQuery(e.target.value); setOpen(true) }}
              onFocus={() => setOpen(true)}
              placeholder={label}
              style={{
                width: '100%', padding: '10px 12px 10px 34px',
                background: 'var(--card)', border: '1px solid var(--border)',
                borderRadius: 8, color: 'var(--white)', fontSize: 14, outline: 'none',
              }}
            />
            {loading && (
              <div style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', width: 14, height: 14, border: '2px solid var(--border)', borderTopColor: 'var(--green)', borderRadius: '50%', animation: 'spin .6s linear infinite' }} />
            )}
          </div>
          {query.length >= 3 && results.length > 0 && (
            <div style={{
              position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50,
              background: '#1a1a1a', border: '1px solid var(--border)', borderRadius: 8,
              marginTop: 4, overflow: 'hidden',
            }}>
              {results.slice(0, 5).map(p => (
                <div key={p.id} onClick={() => pick(p)} style={{
                  padding: '10px 14px', cursor: 'pointer', borderBottom: '1px solid var(--border)',
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  transition: 'background .15s',
                }}
                  onMouseEnter={e => e.currentTarget.style.background = '#222'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                >
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 14 }}>{p.name}</div>
                    {p.countryAcr && <div style={{ fontSize: 11, color: 'var(--muted)' }}>{p.countryAcr}</div>}
                  </div>
                  {p.currentRank && <span style={{ fontSize: 11, color: 'var(--muted)' }}>#{p.currentRank}</span>}
                </div>
              ))}
            </div>
          )}
        </>
      )}
      <style>{`@keyframes spin { to { transform: translateY(-50%) rotate(360deg); } }`}</style>
    </div>
  )
}
