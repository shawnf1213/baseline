import { useState, useEffect } from 'react'
import { T } from './theme'
import { Card, Heart, Delta, Spinner, Empty, SectionLabel } from './bits'
import PlayerPhoto from './PlayerPhoto'
import { shortProp, fmt } from './data'
import { projectRow, cachedProjection } from './project'
import { useBookmarks } from './useBookmarks'

export default function ResearchTab({ onOpenPlayer }) {
  const { list, toggle } = useBookmarks()
  const props = list.filter(b => b.kind === 'prop')
  const players = list.filter(b => b.kind === 'player')

  // Re-project saved props so they show the current projection vs line (shared
  // cache with the Board, so this is usually instant).
  const [proj, setProj] = useState({})
  useEffect(() => {
    let alive = true
    props.forEach(b => {
      const c = cachedProjection(b.key)
      if (c !== undefined) { setProj(m => (b.key in m ? m : { ...m, [b.key]: c || { failed: true } })); return }
      setProj(m => (m[b.key]?.loading ? m : { ...m, [b.key]: { loading: true } }))
      projectRow(b, b.tour).then(res => { if (alive) setProj(m => ({ ...m, [b.key]: res || { failed: true } })) })
    })
    return () => { alive = false }
  }, [list.length])

  if (!list.length) {
    return (
      <div>
        <Header />
        <Empty icon="🔖" title="Nothing saved yet"
          hint="Tap the heart on any prop or player to pin it here. Saved props update with the latest projection." />
      </div>
    )
  }

  return (
    <div>
      <Header />

      {props.length > 0 && <SectionLabel>Saved Props · {props.length}</SectionLabel>}
      {props.map(b => {
        const p = proj[b.id] || proj[b.key] || {}
        const done = !p.loading && !p.failed && p.projection != null
        const line = b.line
        return (
          <Card key={b.id} onClick={() => onOpenPlayer({ name: b.player, tour: b.tour })}
            style={{ padding: '12px 8px 12px 14px', marginBottom: 10 }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 17, color: T.white, letterSpacing: 0.3 }}>{b.player}</div>
                <div style={{ color: T.muted, fontSize: 12.5, marginTop: 2 }}>
                  {b.opponent ? `vs ${b.opponent} · ` : ''}{b.tour}
                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center' }}>
                <div style={{ textAlign: 'right', minWidth: 40 }}>
                  {done ? <Delta value={p.edge} /> : p.loading ? <Spinner size={14} /> : <span style={{ color: T.muted2, fontSize: 12 }}>—</span>}
                  <div style={{ color: T.muted2, fontSize: 10, letterSpacing: 1, textTransform: 'uppercase', fontFamily: T.cond, fontWeight: 700 }}>vs line</div>
                </div>
                <Heart active onClick={() => toggle(b)} />
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 10 }}>
              <span style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 12.5, letterSpacing: 0.6, textTransform: 'uppercase', color: T.green, background: 'rgba(0,230,118,0.08)', border: `1px solid ${T.border}`, padding: '4px 10px', borderRadius: 8 }}>{shortProp(b.propType)}</span>
              <div style={{ display: 'flex', gap: 14 }}>
                <Mini label="Line" value={fmt(line, line != null && Number.isInteger(line) ? 0 : 1)} />
                <Mini label="Proj" value={done ? fmt(p.projection) : '—'} accent={done} />
              </div>
            </div>
          </Card>
        )
      })}

      {players.length > 0 && <div style={{ marginTop: 22 }}><SectionLabel>Saved Players · {players.length}</SectionLabel></div>}
      {players.map(b => (
        <Card key={b.id} onClick={() => onOpenPlayer({ name: b.player, id: b.playerId, tour: b.tour, currentRank: b.currentRank })}
          style={{ display: 'flex', alignItems: 'center', gap: 12, padding: 10, marginBottom: 8 }}>
          <PlayerPhoto id={b.playerId} name={b.player} size={44} />
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 17, color: T.white, letterSpacing: 0.3 }}>{b.player}</div>
            <div style={{ color: T.muted, fontSize: 12.5 }}>{b.currentRank ? `Rank #${b.currentRank} · ` : ''}{b.tour}</div>
          </div>
          <Heart active onClick={() => toggle(b)} />
        </Card>
      ))}
    </div>
  )
}

function Header() {
  return <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 26, color: T.white, letterSpacing: 0.5, marginBottom: 14 }}>My Research</div>
}
function Mini({ label, value, accent }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 16, color: accent ? T.green : T.white }}>{value}</div>
      <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5, letterSpacing: 1, textTransform: 'uppercase', color: T.muted2 }}>{label}</div>
    </div>
  )
}
