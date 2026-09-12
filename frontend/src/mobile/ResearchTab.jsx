import { useState, useEffect } from 'react'
import { T } from './theme'
import { Card, Heart, Empty, SectionLabel, PageTitle, PersonRow,
         SideRail, sideTone, tier, tierCardStyle } from './bits'
import { Reveal, EdgeScale } from './motion'
import PlayerPhoto from './PlayerPhoto'
import { shortProp } from './data'
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
      {props.map((b, i) => {
        const p = proj[b.id] || proj[b.key] || {}
        const done = !p.loading && !p.failed && p.projection != null
        const { side, tone, rgb } = sideTone(done ? p.edge : null)
        return (
          <Reveal key={b.id} i={i}>
            <Card onClick={() => onOpenPlayer({ name: b.player, tour: b.tour })}
                  style={{ padding: '12px 12px 13px 17px', marginBottom: T.s2,
                           position: 'relative', overflow: 'hidden',
                           ...tierCardStyle(p.confidence, rgb) }}>
              <SideRail rgb={rgb} weight={tier(p.confidence).weight || 1} />
              <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
                <PlayerPhoto name={b.player} size={38} ring={false} />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontFamily: T.cond, fontWeight: 800,
                                fontSize: 18, color: T.white,
                                letterSpacing: 0.3, lineHeight: 1.1 }}>
                    {b.player}
                  </div>
                  <div style={{ color: T.muted2, fontSize: 11.5, marginTop: 1 }}>
                    {shortProp(b.propType)}
                    {b.opponent ? ` · vs ${b.opponent}` : ''}
                  </div>
                </div>
                <span style={{ fontFamily: T.cond, fontWeight: 800,
                               fontSize: 16, color: tone, letterSpacing: 0.4 }}>
                  {side || ''}
                </span>
                <Heart active onClick={(e) => { e.stopPropagation(); toggle(b) }} />
              </div>

              {/* The SAME scale the board draws, so a pinned play is
                  recognisably the object that was pinned. */}
              {done ? (
                <EdgeScale line={b.line} proj={p.projection}
                           tone={tone} rgb={rgb} />
              ) : (
                <div style={{ color: T.muted2, fontSize: 12, marginTop: 10 }}>
                  {p.loading ? 'Re-projecting…' : 'No current projection.'}
                </div>
              )}
            </Card>
          </Reveal>
        )
      })}

      {players.length > 0 && <div style={{ marginTop: 22 }}><SectionLabel>Saved Players · {players.length}</SectionLabel></div>}
      {players.map((b, i) => (
        <PersonRow key={b.id} index={i} name={b.player}
          photo={<PlayerPhoto id={b.playerId} name={b.player} size={44} />}
          meta={[b.currentRank ? `Rank #${b.currentRank}` : '', b.tour]
            .filter(Boolean).join(' · ')}
          right={<Heart active onClick={(e) => { e.stopPropagation(); toggle(b) }} />}
          onClick={() => onOpenPlayer({ name: b.player, id: b.playerId,
                                        tour: b.tour,
                                        currentRank: b.currentRank })} />
      ))}
    </div>
  )
}

function Header() {
  return (
    <PageTitle sub="Props and players you saved, kept on this device.">
      My Research
    </PageTitle>
  )
}
