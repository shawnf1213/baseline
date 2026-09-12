import { useMemo, useState, useEffect } from 'react'
import { T } from './theme'
import { Card, Heart, Spinner, Empty, tier, sideTone, SideRail, TierBadge,
         ConfBar, BigStat, tierCardStyle, PageTitle, Pill, GlassTabs,
         SectionLabel } from './bits'
import FilterSheet from './FilterSheet'
import { shortProp, startTimeLabel, fmt, calibratedConfidence } from './data'
import { projectRow, cachedProjection } from './project'
import { useBookmarks, propBookmarkId } from './useBookmarks'
import NflBoard from './NflBoard'
import { Num } from './motion'

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
  // SPORT LIVES HERE, NOT IN THE NAV. Seven bottom-tabs is already a crowded
  // rail, and "which sport" is a filter on the board rather than a different
  // place in the app — the same shape as the PrizePicks/Underdog switch that
  // is already on this screen.
  const [sport, setSport] = useState('tennis')
  const [filters, setFilters] = useState(DEFAULT_FILTERS)
  const [sheet, setSheet] = useState(false)
  // Counts for the NFL header, reported up by NflBoard so the two sports
  // present the same title block rather than one being bare.
  const [nflMeta, setNflMeta] = useState({})
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

  // Displayed confidence is CALIBRATED across the visible board — see
  // data.calibratedConfidence. The raw score gates; it does not describe.
  const calib = useMemo(() => calibratedConfidence(rows), [rows])
  const activeCount = ['prop', 'tour', 'surface'].filter(k => filters[k] !== 'All').length
  const projecting = filtered.slice(0, PROJECT_CAP).some(r => proj[r.key]?.loading)

  const SportSwitch = (
    <GlassTabs value={sport} onChange={setSport} style={{ marginBottom: 0 }}
               options={[{ key: 'tennis', label: '🎾 Tennis' },
                         { key: 'nfl', label: '🏈 NFL' }]} />
  )

  // The NFL board is a DIFFERENT KIND OF VIEW, not the tennis board with other
  // rows in it. Tennis prices the live book board through /api/prop/calculate;
  // NFL has no such endpoint (the model ships with the bot, not the backend),
  // so what it shows is what was actually posted, with results. Filters and
  // bookmarks belong to the tennis shape and are deliberately not drawn here.
  if (sport === 'nfl') {
    return (
      <div style={{ paddingBottom: 8 }}>
        <PageTitle sub={<>
          <Pill live>Live</Pill>
          {nflMeta.count ? (
            <span style={{ color: T.muted2 }}>{nflMeta.count} priced</span>
          ) : null}
          {nflMeta.label ? (
            <span style={{ color: T.muted2 }}>{nflMeta.label}</span>
          ) : null}
        </>}>Board</PageTitle>
        <div style={{ marginBottom: T.s5 }}>{SportSwitch}</div>
        <NflBoard onMeta={setNflMeta} />
      </div>
    )
  }

  return (
    <div style={{ paddingBottom: 8 }}>
      <PageTitle
        sub={<>
          <Pill live>Live</Pill>
          {rows.length ? (
            <span style={{ color: T.muted2 }}>
              {rows.length} priced{projecting ? ' · projecting…' : ''}
            </span>
          ) : projecting ? (
            <span style={{ color: T.muted2 }}>projecting…</span>
          ) : null}
        </>}
        right={
          <button onClick={() => setSheet(true)} style={{
            display: 'inline-flex', alignItems: 'center', gap: 8, minHeight: 44,
            padding: '0 16px', borderRadius: T.r2, cursor: 'pointer',
            background: activeCount
              ? `linear-gradient(160deg, ${T.green}24, ${T.green}0D)` : T.glass,
            color: activeCount ? T.green : T.white,
            border: `1px solid ${activeCount ? `${T.green}55` : T.glassLine}`,
            fontFamily: T.cond, fontWeight: 700, fontSize: 14,
            letterSpacing: 0.8, textTransform: 'uppercase',
          }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M3 5h18M6 12h12M10 19h4" />
            </svg>
            Filter{activeCount ? ` · ${activeCount}` : ''}
          </button>
        }>
        Board
      </PageTitle>

      {SportSwitch}

      {/* The book, as a quiet secondary choice rather than a second slab. */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8,
                    marginTop: T.s2, marginBottom: T.s5 }}>
        <span style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5,
                       letterSpacing: 1.2, textTransform: 'uppercase',
                       color: T.muted2 }}>Book</span>
        <div style={{ display: 'flex', gap: 4, padding: 3, borderRadius: 999,
                      background: 'rgba(255,255,255,0.025)',
                      border: `1px solid ${T.glassLine}` }}>
          {BOOKS.map(b => {
            const on = b.key === book
            return (
              <button key={b.key} onClick={() => setBook(b.key)} style={{
                minHeight: 30, padding: '0 13px', borderRadius: 999,
                cursor: 'pointer',
                border: on ? `1px solid ${T.green}55` : '1px solid transparent',
                background: on ? `${T.green}1C` : 'transparent',
                color: on ? T.green : T.muted2,
                fontFamily: T.cond, fontWeight: 800, fontSize: 12,
                letterSpacing: 1, textTransform: 'uppercase',
                WebkitTapHighlightColor: 'transparent',
              }}>{b.label}</button>
            )
          })}
        </div>
      </div>

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}><Spinner size={28} /></div>}

      {!loading && error && (
        <Empty icon="⚠️" title="Couldn't load the board" hint="The live market didn't load. Pull to retry." />
      )}

      {!loading && !error && !!rows.length && (
        <>
          <BoardSummary rows={rows} projecting={projecting} />
          <TopPlays rows={rows} onOpen={r => onOpenPlayer({ name: r.player, tour: r.tour })}
                    saved={r => has(propBookmarkId(r))}
                    onSave={(r, e) => { e.stopPropagation()
                      toggle({ id: propBookmarkId(r), kind: 'prop', ...r }) }} />
          <SectionLabel right={<span style={{ color: T.muted2, fontSize: 11 }}>
            {rows.length} lines
          </span>}>Full board</SectionLabel>
        </>
      )}

      {!loading && !error && !rows.length && (
        <Empty icon="🎾"
          title={board?.rows?.length ? 'No props match these filters' : 'No tennis props on the board'}
          hint={board?.rows?.length ? 'Try clearing a filter.'
            : `${book === 'underdog' ? 'Underdog' : 'PrizePicks'} has no tennis lines up right now. Check back when matches are near.`} />
      )}

      <div className="baseline-cols">
        {!loading && !error && rows.map((r, i) => (
          <PropRow key={r.key}
            r={{ ...r, confidence: calib.get(r.key) ?? r.confidence }} index={i}
            saved={has(propBookmarkId(r))}
            onSave={() => toggle({ id: propBookmarkId(r), kind: 'prop', ...r })}
            onOpen={() => onOpenPlayer({ name: r.player, tour: r.tour })} />
        ))}
      </div>

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

// ── WHAT THE BOARD ADDS UP TO ────────────────────────────────────────────────
// The board opened straight into thirty-six identical cards. Nothing told a
// reader how big today is, whether there is anything worth their time in it, or
// where the good end of it is — they had to scroll the whole thing and work it
// out. This is that answer, before the list.
export function BoardSummary({ rows, projecting }) {
  const done = rows.filter(r => r._state === 'done')
  const conf = (r) => r.confidence || 0
  const elite = done.filter(r => conf(r) >= 80).length
  const strong = done.filter(r => conf(r) >= 72 && conf(r) < 80).length
  const best = done.reduce(
    (m, r) => (Math.abs(r.edge || 0) > Math.abs(m?.edge ?? -1) ? r : m), null)
  const matches = new Set(
    done.map(r => [r.player, r.opponent].sort().join('|'))).size

  const cells = [
    { k: 'Priced', v: projecting && !done.length ? '…' : String(done.length),
      n: projecting && !done.length ? null : done.length,
      s: matches ? `${matches} match${matches === 1 ? '' : 'es'}` : '' },
    { k: 'Elite', v: String(elite), n: elite, s: '80+ conf',
      tone: elite ? T.green : T.muted2 },
    { k: 'Strong', v: String(strong), n: strong, s: '72–79',
      tone: strong ? T.green : T.muted2 },
    { k: 'Biggest edge',
      v: best ? fmt(Math.abs(best.edge)) : '—',
      n: best ? Math.abs(best.edge) : null, dp: 1,
      s: best ? `${sideTone(best.edge).side} · ${best.player}` : '',
      tone: best ? sideTone(best.edge).tone : T.muted2 },
  ]

  return (
    <Card style={{ padding: '13px 14px', marginBottom: T.s4 }}>
      <div style={{ display: 'grid',
                    gridTemplateColumns: 'repeat(4, minmax(0, 1fr))' }}>
        {cells.map((c, ci) => (
          <div key={c.k} style={{
            minWidth: 0, paddingLeft: ci ? 10 : 0, paddingRight: 6,
            borderLeft: ci ? `1px solid ${T.glassLine}` : 'none',
          }}>
            <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5,
                          letterSpacing: 1, textTransform: 'uppercase',
                          color: T.muted2, whiteSpace: 'nowrap',
                          overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.k}</div>
            {typeof c.n === 'number' ? (
              <Num value={c.n} decimals={c.dp ?? 0} prefix={c.pre || ''}
                   style={{ fontSize: 22, fontWeight: 800, lineHeight: 1.2,
                            letterSpacing: -0.8, color: c.tone || T.white,
                            display: 'block',
                            fontVariantNumeric: 'tabular-nums' }} />
            ) : (
              <div style={{ fontSize: 22, fontWeight: 800, lineHeight: 1.2,
                            letterSpacing: -0.8, color: c.tone || T.white,
                            fontVariantNumeric: 'tabular-nums' }}>{c.v}</div>
            )}
            {c.s ? (
              <div style={{ color: T.muted2, fontSize: 9.5, marginTop: 1,
                            whiteSpace: 'nowrap', overflow: 'hidden',
                            textOverflow: 'ellipsis' }}>{c.s}</div>
            ) : null}
          </div>
        ))}
      </div>
    </Card>
  )
}

// ── THE TOP OF THE BOARD, PULLED OUT ─────────────────────────────────────────
// A flat grid says every play on it is worth the same look, which is the one
// thing the model exists to deny. These are the three the model is most sure
// of, rendered at a size that says so — wide, ranked, and above the grid rather
// than hidden somewhere inside it.
//
// Ranked by CONVICTION TIER FIRST and edge only within a tier. Sorting by raw
// edge would put a wild number on a thin projection at the top of the page,
// which is exactly the play a reader should be least led towards.
export function TopPlays({ rows, onOpen, saved, onSave }) {
  const top = rows
    .filter(r => r._state === 'done' && r.edge != null)
    .sort((a, b) => (tier(b.confidence).weight - tier(a.confidence).weight)
                 || (Math.abs(b.edge) - Math.abs(a.edge)))
    .slice(0, 3)
  if (top.length < 2 || !tier(top[0].confidence).weight) return null

  return (
    <>
      <SectionLabel>Top plays</SectionLabel>
      <div style={{ display: 'grid', gap: T.s2, marginBottom: T.s4 }}>
        {top.map((r, i) => {
          const { side, tone, rgb } = sideTone(r.edge)
          const w = tier(r.confidence).weight
          return (
            <Card key={r.key} index={i} onClick={() => onOpen(r)} style={{
              padding: 0, overflow: 'hidden', position: 'relative',
              ...tierCardStyle(r.confidence, rgb),
            }}>
              <SideRail rgb={rgb} weight={w} />
              <div aria-hidden style={{
                position: 'absolute', inset: 0, pointerEvents: 'none',
                background: `radial-gradient(130% 100% at 0% 0%, rgba(${rgb},`
                          + `${0.03 + w * 0.022}), transparent 66%)`,
              }} />
              <div style={{ position: 'relative', display: 'flex',
                            alignItems: 'center', gap: T.s3, flexWrap: 'wrap',
                            padding: '15px 16px 15px 20px' }}>
                {/* The rank, as a numeral. A reader should be able to tell the
                    first card from the third without comparing two bars. */}
                <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 34,
                               lineHeight: 1, color: `rgba(${rgb},0.55)`,
                               minWidth: 26 }}>{i + 1}</span>

                <div style={{ minWidth: 132, flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7,
                                flexWrap: 'wrap' }}>
                    <span style={{ fontFamily: T.cond, fontWeight: 800,
                                   fontSize: 22, color: T.white,
                                   letterSpacing: 0.3 }}>{r.player}</span>
                    <TierBadge conf={r.confidence} tone={tone} rgb={rgb} />
                  </div>
                  <div style={{ color: T.muted, fontSize: 12, marginTop: 2 }}>
                    {shortProp(r.propType)} · vs {r.opponent}
                    {r.surface ? ` · ${r.surface}` : ''}
                  </div>
                </div>

                <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
                  <span style={{ fontFamily: T.cond, fontWeight: 800,
                                 fontSize: 26, color: tone,
                                 letterSpacing: 0.5 }}>{side}</span>
                  <span style={{ fontSize: 23, fontWeight: 800, color: T.white,
                                 letterSpacing: -0.5,
                                 fontVariantNumeric: 'tabular-nums' }}>
                    {fmt(r.line, Number.isInteger(r.line) ? 0 : 1)}
                  </span>
                </div>

                <BigStat tone={tone} rgb={rgb} proj={fmt(r.projection)}
                         value={`${r.edge > 0 ? '+' : ''}${fmt(r.edge)}`}
                         label="EDGE" />

                <div style={{ display: 'flex', alignItems: 'center', gap: 9,
                              minWidth: 132 }}>
                  <ConfBar conf={r.confidence} tone={tone} max={104} />
                  <Heart active={saved(r)} onClick={(e) => onSave(r, e)} />
                </div>
              </div>
            </Card>
          )
        })}
      </div>
    </>
  )
}

// EXPORTED so the NFL board renders this exact component. A second card that
// merely looks similar drifts the moment either is touched; there is one board
// card in this app and both sports use it.
//
// `footNote` overrides the bottom-left slot. Tennis puts a start time there;
// NFL has no per-play clock but does have a RESULT once the game is played,
// which is the same kind of information — the one thing about this play that
// is neither the pick nor the projection.
export function PropRow({ r, saved, onSave, onOpen, index = 0, footNote }) {
  const start = startTimeLabel(r.startTs)
  const hasProj = r._state === 'done'
  const { side, tone, rgb } = sideTone(hasProj ? r.edge : null)
  const conf = hasProj ? r.confidence : null
  const w = tier(conf).weight

  return (
    <Card onClick={onOpen} index={index} style={{
      padding: 0, marginBottom: T.s2, overflow: 'hidden', position: 'relative',
      ...tierCardStyle(conf, rgb),
    }}>
      <SideRail rgb={side ? rgb : null} weight={w} />

      {/* A WASH IN THE CARD'S OWN DIRECTION. The side rail says which way this
          play leans in four pixels at the edge; this carries that colour across
          the top of the card so the lean is legible from the whole surface
          rather than from a strip the eye has to find. Scaled by conviction —
          an ELITE card is visibly warmer than a thin one, which is what gives
          a scrolling board a top end instead of one flat texture. */}
      {side ? (
        <div aria-hidden style={{
          position: 'absolute', inset: 0, pointerEvents: 'none',
          background: `radial-gradient(120% 90% at 0% 0%, rgba(${rgb},`
                    + `${0.02 + w * 0.018}), transparent 62%)`,
        }} />
      ) : null}

      <div style={{ padding: '13px 13px 12px 18px', position: 'relative' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7,
                          flexWrap: 'wrap' }}>
              <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 20,
                             color: T.white, letterSpacing: 0.3 }}>{r.player}</span>
              <TierBadge conf={conf} tone={tone} rgb={rgb} />
            </div>
            <div style={{ color: T.muted, fontSize: 12.5, marginTop: 3 }}>
              vs {r.opponent}{r.surface ? ` · ${r.surface}` : ''}
              {r.tour ? ` · ${r.tour}` : ''}
            </div>
          </div>
          <Heart active={saved} onClick={onSave} />
        </div>

        {/* THE PROP, ON ITS OWN SHELF. It used to sit flush against the player
            name with nothing separating the identity of the play from the call
            being made on it, so a card read as one undifferentiated block. */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10, marginTop: 12,
          padding: '10px 11px', borderRadius: T.r2,
          background: 'rgba(255,255,255,0.03)',
          border: `1px solid ${T.glassLine}`,
        }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 11.5,
                          letterSpacing: 1.2, textTransform: 'uppercase',
                          color: T.muted2, marginBottom: 4 }}>
              {shortProp(r.propType)}
            </div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
              <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 22,
                             color: tone, letterSpacing: 0.5 }}>{side || '—'}</span>
              <span style={{ fontSize: 19, fontWeight: 800, color: T.white,
                             letterSpacing: -0.3,
                             fontVariantNumeric: 'tabular-nums' }}>
                {fmt(r.line, Number.isInteger(r.line) ? 0 : 1)}
              </span>
            </div>
          </div>

          {hasProj ? (
            <BigStat tone={tone} rgb={rgb}
              proj={fmt(r.projection)}
              value={`${r.edge > 0 ? '+' : ''}${fmt(r.edge)}`}
              label="EDGE" />
          ) : (
            <div style={{ minWidth: 86, textAlign: 'center' }}>
              {r._state === 'loading' ? <Spinner size={16} />
                : <span style={{ color: T.muted2, fontSize: 12 }}>—</span>}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center',
                      justifyContent: 'space-between', marginTop: 11, gap: 8 }}>
          <span style={{ color: T.muted2, fontSize: 11 }}>
            {footNote !== undefined ? footNote : (start ? `⏱ ${start}` : '')}
          </span>
          <ConfBar conf={conf} tone={tone} />
        </div>
      </div>
    </Card>
  )
}

