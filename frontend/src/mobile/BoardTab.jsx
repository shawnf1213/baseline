import { useMemo, useState, useEffect } from 'react'
import { T } from './theme'
import { Card, Heart, Spinner, Empty, Segment } from './bits'
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

const BOOKS = [
  { key: 'prizepicks', label: 'PrizePicks' },
  { key: 'underdog', label: 'Underdog' },
]

export default function BoardTab({ boards, book, setBook, loading, error, onOpenPlayer }) {
  const [filters, setFilters] = useState(DEFAULT_FILTERS)
  const [sheet, setSheet] = useState(false)
  const { has, toggle } = useBookmarks()
  const board = boards?.[book]

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
            Live {book === 'underdog' ? 'Underdog' : 'PrizePicks'}
            {rows.length ? ` · ${rows.length}` : ''}{projecting ? ' · projecting…' : ''}
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

      <Segment options={BOOKS} value={book} onChange={setBook} style={{ marginBottom: 14 }} />

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}><Spinner size={28} /></div>}

      {!loading && error && (
        <Empty icon="⚠️" title="Couldn't load the board" hint="The live market didn't load. Pull to retry." />
      )}

      {!loading && !error && !rows.length && (
        <Empty icon="🎾"
          title={board?.rows?.length ? 'No props match these filters' : 'No tennis props on the board'}
          hint={board?.rows?.length ? 'Try clearing a filter.'
            : `${book === 'underdog' ? 'Underdog' : 'PrizePicks'} has no tennis lines up right now. Check back when matches are near.`} />
      )}

      {!loading && !error && rows.map((r, i) => (
        <PropRow key={r.key} r={r} index={i}
          saved={has(propBookmarkId(r))}
          onSave={() => toggle({ id: propBookmarkId(r), kind: 'prop', ...r })}
          onOpen={() => onOpenPlayer({ name: r.player, tour: r.tour })} />
      ))}

      {!loading && !error && !!rows.length && (
        <div style={{ color: T.muted2, fontSize: 11.5, textAlign: 'center', padding: '16px 12px 4px', lineHeight: 1.5 }}>
          Live {book === 'underdog' ? 'Underdog' : 'PrizePicks'} lines with Baseline's model projection.
          Edge = projection − line. Tap any prop to open the player.
          {book === 'underdog' && ' Multiplier and one-sided lines are excluded.'}
        </div>
      )}

      <FilterSheet open={sheet} onClose={() => setSheet(false)} filters={filters} setFilters={setFilters} />
    </div>
  )
}

// ── CONVICTION DRIVES THE VISUAL WEIGHT ──────────────────────────────────────
// Every row used to look identical whether it carried a 12-point edge or a coin
// flip, so a reader had to compare small grey numbers to find anything worth
// looking at. Colour, glow and border strength are now a function of
// confidence: the plays worth attention are the ones that LOOK loud, and a scan
// of the board lands on them without reading a single number.
//
// The bands are the ones confidence.py already uses, so what the eye is told
// matches what the model actually said — a card cannot look elite while
// scoring 61.
function tier(conf) {
  if (conf == null) return { key: 'none', label: '', weight: 0 }
  if (conf >= 80) return { key: 'elite', label: 'ELITE', weight: 3 }
  if (conf >= 72) return { key: 'strong', label: 'STRONG', weight: 2 }
  if (conf >= 64) return { key: 'lean', label: 'LEAN', weight: 1 }
  return { key: 'thin', label: '', weight: 0 }
}

function PropRow({ r, saved, onSave, onOpen, index = 0 }) {
  const start = startTimeLabel(r.startTs)
  const hasProj = r._state === 'done'
  const side = hasProj && r.edge != null
    ? (r.edge > 0 ? 'OVER' : r.edge < 0 ? 'UNDER' : null) : null
  const tone = side === 'OVER' ? T.green : side === 'UNDER' ? T.red : T.muted2
  const rgb = side === 'OVER' ? '0,230,118' : side === 'UNDER' ? '255,68,68' : '107,107,107'
  const tr = tier(hasProj ? r.confidence : null)

  return (
    <Card onClick={onOpen} index={index} style={{
      padding: 0, marginBottom: 10, overflow: 'hidden', position: 'relative',
      // Outline and glow both scale with conviction, so the board has a visible
      // top end instead of one flat texture.
      border: `1px solid ${tr.weight >= 2 ? `rgba(${rgb},0.45)` : T.border}`,
      boxShadow: tr.weight >= 3
        ? `0 0 0 1px rgba(${rgb},0.20), 0 6px 26px rgba(${rgb},0.16), 0 6px 18px rgba(0,0,0,0.4)`
        : tr.weight === 2
          ? `0 4px 18px rgba(${rgb},0.09), 0 6px 18px rgba(0,0,0,0.38)`
          : '0 6px 18px rgba(0,0,0,0.35)',
    }}>
      {/* Side rail — the fastest read on the card. Colour says which way, and
          thickness says how strongly. */}
      <div style={{
        position: 'absolute', left: 0, top: 0, bottom: 0,
        width: tr.weight >= 3 ? 5 : tr.weight === 2 ? 4 : 3,
        background: side
          ? `linear-gradient(180deg, rgba(${rgb},0.95), rgba(${rgb},0.35))`
          : T.border,
      }} />

      <div style={{ padding: '12px 12px 12px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
              <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 19,
                             color: T.white, letterSpacing: 0.3 }}>{r.player}</span>
              {tr.label && (
                <span style={{
                  fontFamily: T.cond, fontWeight: 800, fontSize: 9.5, letterSpacing: 1.2,
                  color: tone, padding: '2.5px 7px', borderRadius: 6,
                  background: `rgba(${rgb},0.13)`, border: `1px solid rgba(${rgb},0.4)`,
                  boxShadow: tr.weight >= 3 ? `0 0 12px rgba(${rgb},0.35)` : 'none',
                }}>{tr.label}</span>
              )}
            </div>
            <div style={{ color: T.muted, fontSize: 12.5, marginTop: 2 }}>
              vs {r.opponent}{r.surface ? ` · ${r.surface}` : ''}{r.tour ? ` · ${r.tour}` : ''}
            </div>
          </div>
          <Heart active={saved} onClick={onSave} />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 11 }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 13,
                          letterSpacing: 0.8, textTransform: 'uppercase',
                          color: T.muted, marginBottom: 3 }}>
              {shortProp(r.propType)}
            </div>
            {/* The CALL in words, rather than leaving it to be inferred from the
                sign of a small delta. */}
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
              <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 20,
                             color: tone, letterSpacing: 0.5 }}>
                {side || '—'}
              </span>
              <span style={{ fontSize: 17, fontWeight: 800, color: T.white,
                             fontVariantNumeric: 'tabular-nums' }}>
                {fmt(r.line, Number.isInteger(r.line) ? 0 : 1)}
              </span>
            </div>
          </div>

          {/* The edge, at the largest size on the card — it is the reason to
              look at this row at all. */}
          <div style={{
            textAlign: 'center', minWidth: 86, padding: '7px 10px', borderRadius: 12,
            background: hasProj ? `rgba(${rgb},0.10)` : 'transparent',
            border: `1px solid ${hasProj ? `rgba(${rgb},0.30)` : T.border}`,
          }}>
            {hasProj ? (
              <>
                <div style={{ fontSize: 25, fontWeight: 800, color: tone, lineHeight: 1.05,
                              fontVariantNumeric: 'tabular-nums' }}>
                  {r.edge > 0 ? '+' : ''}{fmt(r.edge)}
                </div>
                <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9,
                              letterSpacing: 1.2, color: T.muted2 }}>
                  EDGE · PROJ {fmt(r.projection)}
                </div>
              </>
            ) : r._state === 'loading' ? <Spinner size={16} />
              : <span style={{ color: T.muted2, fontSize: 12 }}>—</span>}
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                      marginTop: 10, gap: 8 }}>
          <span style={{ color: T.muted2, fontSize: 11 }}>{start ? `⏱ ${start}` : ''}</span>
          {hasProj && r.confidence != null && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, flex: 1,
                          maxWidth: 132 }}>
              <div style={{ flex: 1, height: 4, borderRadius: 3, background: '#1c1c1c',
                            overflow: 'hidden' }}>
                <div style={{ width: `${Math.min(100, r.confidence)}%`, height: '100%',
                              background: tone, borderRadius: 3 }} />
              </div>
              <span style={{ fontSize: 11.5, fontWeight: 800, color: tone,
                             fontVariantNumeric: 'tabular-nums' }}>
                {Math.round(r.confidence)}
              </span>
            </div>
          )}
        </div>
      </div>
    </Card>
  )
}


