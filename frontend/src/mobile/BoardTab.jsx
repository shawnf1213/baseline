import { useMemo, useState, useEffect } from 'react'
import { T } from './theme'
import { Card, Delta, Heart, Spinner, Empty } from './bits'
import FilterSheet from './FilterSheet'
import { shortProp, startTimeLabel, fmt } from './data'
import { projectRow, cachedProjection } from './project'
import { useBookmarks, propBookmarkId } from './useBookmarks'

const DEFAULT_FILTERS = { prop: 'All', tour: 'All', surface: 'All', sort: 'start' }
const PROJECT_CAP = 120  // auto-project the whole current view (throttled in project.js)

// Lazily project a set of rows (cached + concurrency-limited in project.js).
function useBoardProjections(rows) {
  const [map, setMap] = useState({})
  useEffect(() => {
    let alive = true
    rows.slice(0, PROJECT_CAP).forEach(row => {
      const cached = cachedProjection(row.key)
      if (cached !== undefined) {
        setMap(m => (row.key in m ? m : { ...m, [row.key]: cached || { failed: true } }))
        return
      }
      setMap(m => (m[row.key]?.loading ? m : { ...m, [row.key]: { loading: true } }))
      projectRow(row).then(res => { if (alive) setMap(m => ({ ...m, [row.key]: res || { failed: true } })) })
    })
    return () => { alive = false }
  }, [rows])
  return map
}

export default function BoardTab({ board, loading, error, onOpenPlayer }) {
  const [filters, setFilters] = useState(DEFAULT_FILTERS)
  const [sheet, setSheet] = useState(false)
  const { has, toggle } = useBookmarks()

  // Filter first (depends only on board + filters → stable input for projection).
  const filtered = useMemo(() => {
    let r = (board?.rows || []).slice()
    if (filters.prop !== 'All') r = r.filter(x => x.propType === filters.prop)
    if (filters.tour !== 'All') r = r.filter(x => x.tour === filters.tour || !x.tour)
    if (filters.surface !== 'All') r = r.filter(x => x.surface === filters.surface || !x.surface)
    return r
  }, [board, filters.prop, filters.tour, filters.surface])

  const proj = useBoardProjections(filtered)

  // Merge projections in, then sort.
  const rows = useMemo(() => {
    const merged = filtered.map(r => {
      const p = proj[r.key]
      if (p && !p.loading && !p.failed) {
        return { ...r, projection: p.projection, edge: p.edge, confidence: p.confidence, tour: p.tour || r.tour, _state: p.projection == null ? 'nodata' : 'done' }
      }
      return { ...r, _state: p?.loading ? 'loading' : 'idle' }
    })
    const s = filters.sort
    if (s === 'edge') merged.sort((a, b) => Math.abs(b.edge ?? -1) - Math.abs(a.edge ?? -1))
    else if (s === 'confidence') merged.sort((a, b) => (b.confidence ?? -1) - (a.confidence ?? -1))
    else merged.sort((a, b) => (a.startTs ?? Infinity) - (b.startTs ?? Infinity))
    return merged
  }, [filtered, proj, filters.sort])

  const activeCount = ['prop', 'tour', 'surface'].filter(k => filters[k] !== 'All').length
  const projecting = filtered.slice(0, PROJECT_CAP).some(r => proj[r.key]?.loading)

  return (
    <div style={{ paddingBottom: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div>
          <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 26, color: T.white, letterSpacing: 0.5, lineHeight: 1 }}>Board</div>
          <div style={{ color: T.muted, fontSize: 12.5, marginTop: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
            <span className="live-dot" style={{ width: 6, height: 6 }} />
            Live PrizePicks{rows.length ? ` · ${rows.length}` : ''}{projecting ? ' · projecting…' : ''}
          </div>
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
        <Empty icon="⚠️" title="Couldn't load the board" hint="The live market didn't load. Pull to retry." />
      )}

      {!loading && !error && !rows.length && (
        <Empty icon="🎾"
          title={board?.rows?.length ? 'No props match these filters' : 'No tennis props on the board'}
          hint={board?.rows?.length ? 'Try clearing a filter.' : 'PrizePicks has no tennis lines up right now. Check back when matches are near.'} />
      )}

      {!loading && !error && rows.map(r => (
        <PropRow key={r.key} r={r}
          saved={has(propBookmarkId(r))}
          onSave={() => toggle({ id: propBookmarkId(r), kind: 'prop', ...r })}
          onOpen={() => onOpenPlayer({ name: r.player, tour: r.tour })} />
      ))}

      {!loading && !error && !!rows.length && (
        <div style={{ color: T.muted2, fontSize: 11.5, textAlign: 'center', padding: '16px 12px 4px', lineHeight: 1.5 }}>
          Live PrizePicks lines with Baseline's model projection. Edge = projection − line.
          Tap any prop to open the player.
        </div>
      )}

      <FilterSheet open={sheet} onClose={() => setSheet(false)} filters={filters} setFilters={setFilters} />
    </div>
  )
}

function PropRow({ r, saved, onSave, onOpen }) {
  const start = startTimeLabel(r.startTs)
  const hasProj = r._state === 'done'
  return (
    <Card onClick={onOpen} style={{ padding: '12px 12px 12px 14px', marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 18, color: T.white, letterSpacing: 0.3 }}>{r.player}</span>
          <div style={{ color: T.muted, fontSize: 12.5, marginTop: 2 }}>
            vs {r.opponent}{r.surface ? ` · ${r.surface}` : ''}{r.tour ? ` · ${r.tour}` : ''}
          </div>
        </div>
        <div style={{ textAlign: 'right', display: 'flex', alignItems: 'center', gap: 2 }}>
          <div style={{ minWidth: 44 }}>
            {hasProj ? <Delta value={r.edge} />
              : r._state === 'loading' ? <Spinner size={14} />
              : <span style={{ color: T.muted2, fontSize: 12 }}>—</span>}
            <div style={{ color: T.muted2, fontSize: 10, letterSpacing: 1, textTransform: 'uppercase', fontFamily: T.cond, fontWeight: 700 }}>vs line</div>
          </div>
          <Heart active={saved} onClick={onSave} />
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginTop: 10 }}>
        <span style={{
          fontFamily: T.cond, fontWeight: 700, fontSize: 12.5, letterSpacing: 0.6, textTransform: 'uppercase',
          color: T.green, background: 'rgba(0,230,118,0.08)', border: `1px solid ${T.border}`, padding: '4px 10px', borderRadius: 8,
        }}>{shortProp(r.propType)}</span>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
          <Metric label="Line" value={fmt(r.line, Number.isInteger(r.line) ? 0 : 1)} />
          <Metric label="Proj" value={hasProj ? fmt(r.projection) : '—'} accent={hasProj} />
          {hasProj && r.confidence != null && <Metric label="Conf" value={`${Math.round(r.confidence)}%`} muted />}
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
