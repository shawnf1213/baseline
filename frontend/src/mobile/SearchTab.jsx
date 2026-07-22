import { useState } from 'react'
import { T } from './theme'
import { Card, Chip, Spinner, Empty, SectionLabel } from './bits'
import PlayerPhoto from './PlayerPhoto'
import { usePlayerSearch } from '../hooks/usePlayerSearch'
import { useRecentPlayers } from './useRecent'

export default function SearchTab({ onOpenPlayer }) {
  const [tour, setTour] = useState('ATP')
  const { query, setQuery, results, loading, error } = usePlayerSearch(tour)
  const { list: recent, push, clear } = useRecentPlayers()

  const open = (p) => {
    const t = p.gender === 'F' ? 'WTA' : p.gender === 'M' ? 'ATP' : tour
    push({ id: p.id, name: p.name, currentRank: p.currentRank, tour: t })
    onOpenPlayer({ name: p.name, id: p.id, tour: t, currentRank: p.currentRank })
  }

  return (
    <div>
      <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 26, color: T.white, letterSpacing: 0.5, marginBottom: 14 }}>Search</div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        {['ATP', 'WTA'].map(t => <Chip key={t} active={tour === t} onClick={() => setTour(t)}>{t}</Chip>)}
      </div>

      <div style={{ position: 'relative', marginBottom: 18 }}>
        <span style={{ position: 'absolute', left: 14, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={T.muted} strokeWidth="2" strokeLinecap="round"><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></svg>
        </span>
        <input
          autoFocus
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search any player…"
          style={{
            width: '100%', minHeight: 50, padding: '0 16px 0 42px',
            background: T.card, border: `1px solid ${T.border}`, borderRadius: 13,
            color: T.white, fontSize: 16, outline: 'none', boxSizing: 'border-box',
          }}
          onFocus={e => e.target.style.border = `1px solid ${T.green}`}
          onBlur={e => e.target.style.border = `1px solid ${T.border}`}
        />
      </div>

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 28 }}><Spinner /></div>}
      {error && <div style={{ color: T.red, fontSize: 13.5, textAlign: 'center', padding: '10px 16px', lineHeight: 1.5 }}>{error}</div>}

      {query.length >= 3 && !loading && !error && !results.length && (
        <Empty icon="🔍" title="No players found" hint="Try a different spelling or the other tour." />
      )}

      {!!results.length && results.map(p => (
        <PlayerRow key={p.id} p={p} onClick={() => open(p)} />
      ))}

      {query.length < 3 && (
        <>
          {recent.length > 0 ? (
            <>
              <SectionLabel right={<button onClick={clear} style={{ background: 'transparent', border: 'none', color: T.muted, fontFamily: T.cond, fontWeight: 700, fontSize: 11, letterSpacing: 1, textTransform: 'uppercase', cursor: 'pointer' }}>Clear</button>}>Recent</SectionLabel>
              {recent.map(p => (
                <PlayerRow key={p.id || p.name} p={p} onClick={() => { push(p); onOpenPlayer({ name: p.name, id: p.id, tour: p.tour, currentRank: p.currentRank }) }} />
              ))}
            </>
          ) : (
            <Empty icon="🎾" title="Find a player" hint="Search by name to open their research dashboard — form, surface splits and hit rates." />
          )}
        </>
      )}
    </div>
  )
}

function PlayerRow({ p, onClick }) {
  return (
    <Card onClick={onClick} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: 10, marginBottom: 8 }}>
      <PlayerPhoto id={p.id} name={p.name} size={44} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 17, color: T.white, letterSpacing: 0.3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}</div>
        <div style={{ color: T.muted, fontSize: 12.5, marginTop: 1 }}>
          {p.currentRank ? `Rank #${p.currentRank}` : (p.tour || '')}{p.countryAcr ? ` · ${p.countryAcr}` : ''}
        </div>
      </div>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={T.muted2} strokeWidth="2" strokeLinecap="round"><path d="M9 6l6 6-6 6" /></svg>
    </Card>
  )
}
