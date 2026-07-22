import { useMemo } from 'react'
import { T } from './theme'
import { Card, Spinner, Empty, SectionLabel } from './bits'
import PlayerPhoto from './PlayerPhoto'
import { boardPlayers } from './data'
import { useRecentPlayers } from './useRecent'

export default function PlayersTab({ board, loading, onOpenPlayer }) {
  const players = useMemo(() => boardPlayers(board?.rows || []), [board])
  const { list: recent } = useRecentPlayers()

  return (
    <div>
      <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 26, color: T.white, letterSpacing: 0.5, marginBottom: 14 }}>Players</div>

      {recent.length > 0 && (
        <>
          <SectionLabel>Recently Viewed</SectionLabel>
          <div className="no-scrollbar" style={{ display: 'flex', gap: 10, overflowX: 'auto', paddingBottom: 6, marginBottom: 18 }}>
            {recent.map(p => (
              <div key={p.id || p.name} onClick={() => onOpenPlayer({ name: p.name, id: p.id, tour: p.tour, currentRank: p.currentRank })}
                style={{ flex: '0 0 auto', width: 82, textAlign: 'center', cursor: 'pointer' }}>
                <div style={{ display: 'flex', justifyContent: 'center' }}><PlayerPhoto id={p.id} name={p.name} size={58} /></div>
                <div style={{ color: T.white, fontSize: 11.5, marginTop: 6, lineHeight: 1.2, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>{p.name}</div>
              </div>
            ))}
          </div>
        </>
      )}

      <SectionLabel right={board?.date && <span style={{ color: T.muted2, fontSize: 11 }}>{board.isToday ? 'today' : 'latest slate'}</span>}>
        On the Board
      </SectionLabel>

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner size={26} /></div>}

      {!loading && !players.length && (
        <Empty icon="🎾" title="No players on the board yet"
          hint="The slate populates after the evening update. Use Search to open any player now." />
      )}

      {!loading && players.map(p => (
        <Card key={p.player} onClick={() => onOpenPlayer({ name: p.player, tour: p.tour })}
          style={{ display: 'flex', alignItems: 'center', gap: 12, padding: 10, marginBottom: 8 }}>
          <PlayerPhoto id={null} name={p.player} size={44} />
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 17, color: T.white, letterSpacing: 0.3 }}>{p.player}</div>
            <div style={{ color: T.muted, fontSize: 12.5, marginTop: 1 }}>
              {p.props} prop{p.props !== 1 ? 's' : ''}{p.surface ? ` · ${p.surface}` : ''} · {p.tour}
            </div>
          </div>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={T.muted2} strokeWidth="2" strokeLinecap="round"><path d="M9 6l6 6-6 6" /></svg>
        </Card>
      ))}
    </div>
  )
}
