import { useMemo, useState } from 'react'
import { T } from './theme'
import { Card, Chip, Delta, Heart, Spinner, Empty, SectionLabel } from './bits'
import FilterSheet from './FilterSheet'
import { shortProp, prettyDate, startTimeLabel, fmt } from './data'
import { useBookmarks, propBookmarkId } from './useBookmarks'

const DEFAULT_FILTERS = { prop: 'All', tour: 'All', surface: 'All', sort: 'edge' }

export default function BoardTab({ board, loading, error, reload, onOpenPlayer }) {
  const [filters, setFilters] = useState(DEFAULT_FILTERS)
  const [sheet, setSheet] = useState(false)
  const { has, toggle } = useBookmarks()

  const rows = useMemo(() => {
    let r = (board?.rows || []).slice()
    if (filters.prop !== 'All') r = r.filter(x => x.propType === filters.prop)
    if (filters.tour !== 'All') r = r.filter(x => x.tour === filters.tour)
    if (filters.surface !== 'All') r = r.filter(x => x.surface === filters.surface)
    if (filters.sort === 'edge') r.sort((a, b) => Math.abs(b.edge ?? -1) - Math.abs(a.edge ?? -1))
    else if (filters.sort === 'confidence') r.sort((a, b) => (b.confidence ?? -1) - (a.confidence ?? -1))
    else if (filters.sort === 'start') r.sort((a, b) => (a.startTs ?? Infinity) - (b.startTs ?? Infinity))
    return r
  }, [board, filters])

  const activeCount = ['prop', 'tour', 'surface'].filter(k => filters[k] !== 'All').length

  return (
    <div style={{ paddingBottom: 8 }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div>
          <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 26, color: T.white, letterSpacing: 0.5, lineHeight: 1 }}>Board</div>
          {board?.date && (
            <div style={{ color: T.muted, fontSize: 12.5, marginTop: 4 }}>
              {board.isToday ? 'Upcoming props' : `Upcoming · ${prettyDate(board.date)}`}
              {rows.length ? ` · ${rows.length}` : ''}
            </div>
          )}
        </div>
        <button onClick={() => setSheet(true)} style={{
          display: 'inline-flex', alignItems: 'center', gap: 8, minHeight: 44, padding: '0 16px',
          background: activeCount ? 'rgba(0,230,118,0.12)' : T.card, color: activeCount ? T.green : T.white,
          border: `1px solid ${activeCount ? T.green : T.border}`, borderRadius: 12,
          fontFamily: T.cond, fontWeight: 700, fontSize: 14, letterSpacing: 0.8, textTransform: 'uppercase', cursor: 'pointer',
        }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M3 5h18M6 12h12M10 19h4" /></svg>
          Filter{activeCount ? ` · ${activeCount}` : ''}
        </button>
      </div>

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}><Spinner size={28} /></div>}

      {!loading && error && (
        <Empty title="Couldn't load the board" hint="Check your connection and pull to retry."
          icon="⚠️" />
      )}

      {!loading && !error && !rows.length && (
        <Empty icon="🎾"
          title={board?.rows?.length ? 'No props match these filters' : 'No upcoming props right now'}
          hint={board?.rows?.length
            ? 'Try clearing a filter.'
            : "The board only shows upcoming matches — it refreshes after the evening slate posts (~8 PM ET). Search a player to research them any time."} />
      )}

      {!loading && !error && rows.map(r => (
        <PropRow key={r.key} r={r}
          saved={has(propBookmarkId(r))}
          onSave={() => toggle({ id: propBookmarkId(r), kind: 'prop', ...r })}
          onOpen={() => onOpenPlayer({ name: r.player, tour: r.tour })} />
      ))}

      {!loading && !error && !!rows.length && (
        <div style={{ color: T.muted2, fontSize: 11.5, textAlign: 'center', padding: '16px 12px 4px', lineHeight: 1.5 }}>
          Model-tracked props for this slate — projections vs the posted line.
          Tour is inferred from the tournament.
        </div>
      )}

      <FilterSheet open={sheet} onClose={() => setSheet(false)} filters={filters} setFilters={setFilters} />
    </div>
  )
}

function PropRow({ r, saved, onSave, onOpen }) {
  const start = startTimeLabel(r.startTs)
  return (
    <Card onClick={onOpen} style={{ padding: '12px 12px 12px 14px', marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 18, color: T.white, letterSpacing: 0.3 }}>{r.player}</span>
            {r.oddsType === 'demon' && <Tag c={T.amber}>Demon</Tag>}
          </div>
          <div style={{ color: T.muted, fontSize: 12.5, marginTop: 2 }}>
            {r.opponent ? `vs ${r.opponent}` : ''}{r.surface ? ` · ${r.surface}` : ''} · {r.tour}
          </div>
        </div>
        <div style={{ textAlign: 'right', display: 'flex', alignItems: 'center', gap: 2 }}>
          <div>
            <Delta value={r.edge} />
            <div style={{ color: T.muted2, fontSize: 10, letterSpacing: 1, textTransform: 'uppercase', fontFamily: T.cond, fontWeight: 700 }}>vs line</div>
          </div>
          <Heart active={saved} onClick={onSave} />
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginTop: 10 }}>
        <span style={{
          fontFamily: T.cond, fontWeight: 700, fontSize: 12.5, letterSpacing: 0.6, textTransform: 'uppercase',
          color: T.green, background: 'rgba(0,230,118,0.08)', border: `1px solid ${T.border}`,
          padding: '4px 10px', borderRadius: 8,
        }}>{shortProp(r.propType)}</span>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
          <Metric label="Line" value={fmt(r.line, r.line != null && Number.isInteger(r.line) ? 0 : 1)} />
          <Metric label="Proj" value={fmt(r.projection)} accent />
          {r.confidence != null && <Metric label="Conf" value={`${Math.round(r.confidence)}%`} muted />}
        </div>
      </div>
      {start && <div style={{ color: T.muted2, fontSize: 11, marginTop: 8 }}>⏱ {start}</div>}
    </Card>
  )
}

function Metric({ label, value, accent, muted }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 16, color: accent ? T.green : muted ? T.muted : T.white }}>{value}</div>
      <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5, letterSpacing: 1, textTransform: 'uppercase', color: T.muted2 }}>{label}</div>
    </div>
  )
}

function Tag({ c, children }) {
  return <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 10, letterSpacing: 1, textTransform: 'uppercase', color: c, border: `1px solid ${c}`, borderRadius: 6, padding: '1px 6px' }}>{children}</span>
}
