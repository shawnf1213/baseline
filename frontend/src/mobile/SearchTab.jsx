import { useState } from 'react'
import { T } from './theme'
import { Spinner, Empty, SectionLabel, PageTitle, PersonRow,
         GlassTabs } from './bits'
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
      <PageTitle sub="Any player on tour \u2014 not just the ones priced today.">
        Search
      </PageTitle>

      <GlassTabs value={tour} onChange={setTour} style={{ marginBottom: T.s3 }}
                 options={[{ key: 'ATP', label: 'ATP' },
                           { key: 'WTA', label: 'WTA' }]} />

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
            background: 'rgba(255,255,255,0.04)',
            border: `1px solid ${T.glassLine}`, borderRadius: T.r2,
            color: T.white, fontSize: 16, outline: 'none', boxSizing: 'border-box',
          }}
          onFocus={e => e.target.style.border = `1px solid ${T.green}`}
          onBlur={e => e.target.style.border = `1px solid ${T.glassLine}`}
        />
      </div>

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 28 }}><Spinner /></div>}
      {error && <div style={{ color: T.red, fontSize: 13.5, textAlign: 'center', padding: '10px 16px', lineHeight: 1.5 }}>{error}</div>}

      {query.length >= 3 && !loading && !error && !results.length && (
        <Empty icon="🔍" title="No players found" hint="Try a different spelling or the other tour." />
      )}

      {!!results.length && results.map((p, i) => (
        <PlayerRow key={p.id} p={p} index={i} onClick={() => open(p)} />
      ))}

      {query.length < 3 && (
        <>
          {recent.length > 0 ? (
            <>
              <SectionLabel right={<button onClick={clear} style={{ background: 'transparent', border: 'none', color: T.muted, fontFamily: T.cond, fontWeight: 700, fontSize: 11, letterSpacing: 1, textTransform: 'uppercase', cursor: 'pointer' }}>Clear</button>}>Recent</SectionLabel>
              {recent.map((p, i) => (
                <PlayerRow key={p.id || p.name} p={p} index={i} onClick={() => { push(p); onOpenPlayer({ name: p.name, id: p.id, tour: p.tour, currentRank: p.currentRank }) }} />
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

function PlayerRow({ p, onClick, index = 0 }) {
  return (
    <PersonRow index={index} onClick={onClick} name={p.name}
      photo={<PlayerPhoto id={p.id} name={p.name} size={44} />}
      meta={[p.currentRank ? `Rank #${p.currentRank}` : (p.tour || ''),
             p.countryAcr].filter(Boolean).join(' · ')} />
  )
}
