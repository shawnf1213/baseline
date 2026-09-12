import { useMemo } from 'react'
import { T } from './theme'
import { Spinner, Empty, SectionLabel, PageTitle, PersonRow } from './bits'
import PlayerPhoto from './PlayerPhoto'
import { boardPlayers, mergedBoardRows } from './data'
import { useRecentPlayers } from './useRecent'

export default function PlayersTab({ boards, loading, onOpenPlayer }) {
  // Both books — a player appearing only on Underdog belongs on this list.
  const players = useMemo(() => boardPlayers(mergedBoardRows(boards)), [boards])
  const board = boards?.prizepicks || boards?.underdog
  const { list: recent } = useRecentPlayers()


  return (
    <div>
      <PageTitle sub="Everyone on today's board, and whoever you looked at last.">
        Players
      </PageTitle>

      {recent.length > 0 && (
        <>
          <SectionLabel>Recently Viewed</SectionLabel>
          <div className="no-scrollbar" style={{ display: 'flex', gap: 10, overflowX: 'auto', paddingBottom: 6, marginBottom: 18 }}>
            {recent.map(p => (
              <div key={p.id || p.name} onClick={() => onOpenPlayer({ name: p.name, id: p.id, tour: p.tour, currentRank: p.currentRank })}
                style={{ flex: '0 0 auto', width: 78, textAlign: 'center',
                         cursor: 'pointer', WebkitTapHighlightColor: 'transparent' }}>
                <div style={{ display: 'flex', justifyContent: 'center' }}><PlayerPhoto id={p.id} name={p.name} size={54} /></div>
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

      <div className="baseline-cols">
      {!loading && players.map((p, i) => (
        <PersonRow key={p.player} index={i}
          name={p.player}
          photo={<PlayerPhoto id={null} name={p.player} size={44} />}
          meta={[p.surface, p.tour].filter(Boolean).join(' · ')}
          right={
            /* The prop count is why this player is on this list, so it is a
               figure rather than a clause buried in the metadata line. */
            <div style={{ textAlign: 'right', flexShrink: 0, marginRight: 2 }}>
              <div style={{ fontSize: 17, fontWeight: 800, color: T.green,
                            lineHeight: 1 }}>{p.props}</div>
              <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9,
                            letterSpacing: 1, color: T.muted2 }}>
                {p.props === 1 ? 'PROP' : 'PROPS'}
              </div>
            </div>
          }
          onClick={() => onOpenPlayer({ name: p.player, tour: p.tour })} />
      ))}
      </div>
    </div>
  )
}
