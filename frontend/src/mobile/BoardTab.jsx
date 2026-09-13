import { useMemo, useState, useEffect } from 'react'
import { T, useIsWide } from './theme'
import { Card, Heart, Spinner, Empty, tier, sideTone, SideRail, TierBadge,
         ConfBar, tierCardStyle, PageTitle, Pill, GlassTabs,
         SectionLabel, Pager, PAGE_SIZE } from './bits'
import FilterSheet from './FilterSheet'
import { shortProp, startTimeLabel, fmt, calibratedConfidence } from './data'
import { projectRow, cachedProjection } from './project'
import { useBookmarks, propBookmarkId } from './useBookmarks'
import NflBoard from './NflBoard'
import { Num, Reveal, Tap, EdgeScale } from './motion'
import PlayerPhoto from './PlayerPhoto'
import { TeamMark } from './nflviz'

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
  const [openKey, setOpenKey] = useState(null)
  // Which page of the board is rendered. Reset whenever the view changes, or a
  // reader on page 5 of tennis lands past the end of a shorter NFL board.
  const [page, setPage] = useState(0)
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
  useEffect(() => { setPage(0) }, [sport, book, filters])

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
  // Displayed confidence is CALIBRATED across the visible board — see
  // data.calibratedConfidence. The raw score gates; it does not describe.
  const calib = useMemo(() => calibratedConfidence(rows), [rows])

  // ONE CARD PER PLAYER, built on the CALIBRATED confidence so a group's
  // ordering matches the number the board shows. Declared after calib for that
  // reason — and pages must come after this, not before it: the previous
  // ordering read `groups` above its own declaration, which is a temporal dead
  // zone error and crashed the whole app on load.
  const groups = useMemo(
    () => groupByPlayer(rows.map(
      r => ({ ...r, confidence: calib.get(r.key) ?? r.confidence }))),
    [rows, calib])

  // ONE PAGE IS WHAT RENDERS. Slicing here rather than in the JSX keeps the
  // page maths in one place and out of the render path.
  const pages = Math.max(1, Math.ceil(groups.length / PAGE_SIZE))
  const pageGroups = useMemo(
    () => groups.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE),
    [groups, page])
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
            {groups.length} players · {rows.length} lines
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
        {!loading && !error && pageGroups.map((g, i) => (
          <PlayerGroup key={g.key} g={g} index={i}
            open={openKey === g.key}
            onToggle={() => setOpenKey(k => (k === g.key ? null : g.key))}
            saved={has(propBookmarkId(g.rows[0]))}
            onSave={() => toggle({ id: propBookmarkId(g.rows[0]),
                                   kind: 'prop', ...g.rows[0] })}
            onOpen={() => onOpenPlayer({ name: g.player,
                                         tour: g.rows[0].tour })} />
        ))}
      </div>

      {!loading && !error && (
        <Pager page={page} pages={pages} onPage={setPage}
               total={groups.length} label="players" />
      )}

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

// ── THE TOP OF THE BOARD ─────────────────────────────────────────────────────
// This is the FIRST SCREEN a trial gets, and it had two minutes to make its
// case with type alone — no faces, no crests, and the edge expressed as
// "31.3 | −19.2", two numbers a reader has to hold and subtract.
//
// So: the people are on it, and the edge is drawn as what it actually is — the
// distance between the book's number and ours, with the gap between them lit.
// That gap IS the product, and it is now the largest thing on the card.
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
      <div style={{ display: 'grid', gap: T.s2, marginBottom: T.s5 }}>
        {top.map((r, i) => {
          const { side, tone, rgb } = sideTone(r.edge)
          const w = tier(r.confidence).weight
          const isNfl = r.tour === 'NFL'
          const hero = i === 0
          return (
            <Reveal key={r.key} i={i}>
              <Tap onClick={() => onOpen(r)} style={{
                position: 'relative', overflow: 'hidden', borderRadius: T.r3,
                background: T.glass,
                border: `1px solid ${w >= 3 ? `rgba(${rgb},0.42)`
                                   : `rgba(${rgb},0.20)`}`,
                boxShadow: w >= 3
                  ? `0 0 0 1px rgba(${rgb},0.16), 0 14px 34px rgba(0,0,0,0.5)`
                  : '0 10px 28px rgba(0,0,0,0.45)',
              }}>
                <SideRail rgb={rgb} weight={w} />
                <div aria-hidden style={{
                  position: 'absolute', inset: 0, pointerEvents: 'none',
                  background: `radial-gradient(120% 90% at 0% 0%, rgba(${rgb},`
                            + `${hero ? 0.10 : 0.05}), transparent 62%)`,
                }} />

                {/* The rank, set large and low-contrast BEHIND the content —
                    legible as order, never competing with the numbers. */}
                <span aria-hidden style={{
                  position: 'absolute', right: 12, bottom: -14,
                  fontFamily: T.cond, fontWeight: 800,
                  fontSize: hero ? 108 : 84, lineHeight: 1,
                  color: `rgba(${rgb},0.07)`, pointerEvents: 'none',
                }}>{i + 1}</span>

                <div style={{ position: 'relative',
                              padding: hero ? '16px 16px 14px 20px'
                                            : '13px 14px 12px 18px' }}>
                  <div style={{ display: 'flex', alignItems: 'center',
                                gap: T.s2 }}>
                    {/* THE FACE. The board carried no imagery at all, which is
                        most of why it read as a spreadsheet of a market rather
                        than a card about a person. */}
                    {isNfl
                      ? <TeamMark abbr={r._pick?.team} size={hero ? 46 : 38} />
                      : <PlayerPhoto name={r.player} size={hero ? 46 : 38} />}

                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center',
                                    gap: 7, flexWrap: 'wrap' }}>
                        <span style={{ fontFamily: T.cond, fontWeight: 800,
                                       fontSize: hero ? 23 : 20, color: T.white,
                                       letterSpacing: 0.3,
                                       lineHeight: 1.05 }}>{r.player}</span>
                        <TierBadge conf={r.confidence} tone={tone} rgb={rgb} />
                      </div>
                      <div style={{ color: T.muted, fontSize: 12, marginTop: 2 }}>
                        {shortProp(r.propType)} · vs {r.opponent}
                      </div>
                    </div>

                    <Heart active={saved(r)} onClick={(e) => onSave(r, e)} />
                  </div>

                  {/* THE CALL, then the distance behind it. */}
                  <div style={{ display: 'flex', alignItems: 'center',
                                gap: T.s3, marginTop: hero ? 12 : 10 }}>
                    <div style={{ flexShrink: 0 }}>
                      <div style={{ fontFamily: T.cond, fontWeight: 800,
                                    fontSize: hero ? 30 : 25, color: tone,
                                    letterSpacing: 0.5, lineHeight: 1 }}>
                        {side}
                      </div>
                      <div style={{ fontSize: hero ? 26 : 22, fontWeight: 800,
                                    color: T.white, letterSpacing: -0.8,
                                    lineHeight: 1.1,
                                    fontVariantNumeric: 'tabular-nums' }}>
                        {fmt(r.line, Number.isInteger(r.line) ? 0 : 1)}
                      </div>
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <EdgeScale line={r.line} proj={r.projection}
                                 tone={tone} rgb={rgb} />
                    </div>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: 10,
                                marginTop: 10, paddingTop: 9,
                                borderTop: `1px solid ${T.glassLine}` }}>
                    <span style={{ fontFamily: T.cond, fontWeight: 700,
                                   fontSize: 9.5, letterSpacing: 1.2,
                                   color: T.muted2 }}>EDGE</span>
                    <Num value={Math.abs(r.edge)} decimals={1} style={{
                      fontSize: 15, fontWeight: 800, color: tone,
                      fontVariantNumeric: 'tabular-nums' }} />
                    <div style={{ flex: 1 }} />
                    <ConfBar conf={r.confidence} tone={tone} max={118} />
                  </div>
                </div>
              </Tap>
            </Reveal>
          )
        })}
      </div>
    </>
  )
}

// ── ONE CARD PER PLAYER ──────────────────────────────────────────────────────
// The board was one card per PROP, so a player the model priced four ways got
// four cards — the same face, the same matchup and the same opponent repeated
// down the page with one line different each time. Ilia Simakin appeared twice
// in a single screenful; Alexander Zverev twice in the top three.
//
// Grouping by player fixes three things at once:
//   • The duplication. A reader looking at Simakin wants his card, not three of
//     them scattered among other people's.
//   • The comparison. His fantasy score and his total games are the same bet on
//     the same match from two angles, and they were never side by side.
//   • The cost. 168 NFL props are about 60 players, and a card is the expensive
//     unit — this is a bigger saving than every paint optimisation together,
//     and unlike them it makes the board better rather than merely cheaper.
//
// The closed card carries the player's BEST play, because that is what decides
// whether the card is worth opening. The rest are one tap down.
export function PlayerGroup({ g, saved, onSave, onOpen, index = 0,
                             open, onToggle, footNoteFor }) {
  const wide = useIsWide()
  const best = g.rows[0]
  const { side, tone, rgb } = sideTone(best._state === 'done' ? best.edge : null)
  const conf = best._state === 'done' ? best.confidence : null
  const w = tier(conf).weight
  const isNfl = best.tour === 'NFL'

  return (
    <Card index={index} style={{
      padding: 0, marginBottom: T.s2, position: 'relative',
      overflow: wide && open ? 'visible' : 'hidden',
      zIndex: open ? 20 : undefined,
      // content-visibility applies `contain: paint`, which clips the hung panel
      // to the card's box. The open card opts out — see the note in PropRow.
      ...(wide && open ? { contentVisibility: 'visible', contain: 'none' } : null),
      ...tierCardStyle(conf, rgb),
    }}>
      <SideRail rgb={side ? rgb : null} weight={w} />
      {side && (!wide || w >= 2) ? (
        <div aria-hidden style={{
          position: 'absolute', inset: 0, pointerEvents: 'none',
          background: wide
            ? `linear-gradient(100deg, rgba(${rgb},${0.03 + w * 0.02}),`
              + ' transparent 52%)'
            : `radial-gradient(120% 90% at 0% 0%, rgba(${rgb},`
              + `${0.02 + w * 0.018}), transparent 62%)`,
        }} />
      ) : null}

      <Tap onClick={onToggle} plain style={{
        position: 'relative', display: 'flex', alignItems: 'center', gap: 10,
        padding: '11px 12px 11px 17px',
      }}>
        {isNfl ? <TeamMark abbr={best._pick?.team} size={34} />
               : <PlayerPhoto name={g.player} size={34} ring={false} />}

        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 17,
                           color: T.white, letterSpacing: 0.2, lineHeight: 1.1,
                           whiteSpace: 'nowrap', overflow: 'hidden',
                           textOverflow: 'ellipsis' }}>{g.player}</span>
            {w >= 3 ? <TierBadge conf={conf} tone={tone} rgb={rgb} /> : null}
          </div>
          <div style={{ color: T.muted2, fontSize: 11.5, marginTop: 1,
                        whiteSpace: 'nowrap', overflow: 'hidden',
                        textOverflow: 'ellipsis' }}>
            vs {g.opponent}
            {' · '}
            <span style={{ color: T.muted }}>
              {g.rows.length} prop{g.rows.length === 1 ? '' : 's'}
            </span>
          </div>
        </div>

        {/* The BEST play on this player, because that is what decides whether
            the card is worth opening. */}
        <div style={{ textAlign: 'right', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 5,
                        justifyContent: 'flex-end' }}>
            <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 15,
                           color: tone, letterSpacing: 0.4 }}>{side || '—'}</span>
            <span style={{ fontSize: 15, fontWeight: 800, color: T.white,
                           fontVariantNumeric: 'tabular-nums' }}>
              {fmt(best.line, Number.isInteger(best.line) ? 0 : 1)}
            </span>
          </div>
          {best._state === 'done' ? (
            <div style={{ fontSize: 11.5, color: T.muted2, marginTop: 1,
                          whiteSpace: 'nowrap' }}>
              {shortProp(best.propType)}
              <b style={{ color: tone, marginLeft: 5 }}>
                {best.edge > 0 ? '+' : ''}{fmt(best.edge)}
              </b>
            </div>
          ) : (
            <div style={{ marginTop: 3 }}>
              {best._state === 'loading'
                ? <Spinner size={13} />
                : <span style={{ color: T.muted2, fontSize: 11 }}>—</span>}
            </div>
          )}
        </div>

        <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
             stroke={open ? tone : T.muted2} strokeWidth="2.5"
             strokeLinecap="round" strokeLinejoin="round"
             style={{ flexShrink: 0, transition: 'transform 220ms ease',
                      transform: open ? 'rotate(180deg)' : 'none' }}>
          <path d="M6 9l6 6 6-6" />
        </svg>
      </Tap>

      {open ? (
        <div style={{
          animation: 'fade-in 160ms ease',
          ...(wide ? {
            position: 'absolute', top: '100%', left: -1, right: -1, zIndex: 20,
            background: T.bgElev,
            border: `1px solid ${T.glassLine}`, borderTop: 'none',
            borderRadius: `0 0 ${T.r3}px ${T.r3}px`,
            boxShadow: '0 16px 34px rgba(0,0,0,0.6)',
          } : null),
        }}>
          <div style={{ padding: '2px 14px 12px 17px' }}>
            {/* ── WHAT THE TRACK IS SHOWING ──────────────────────────────────
                The compact scale drops the per-marker labels, because
                repeating "BOOK LINE" and "BASELINE" under each of four props is
                three-quarters noise — but stripped bare it left two dots
                meaning nothing. The key belongs here: said ONCE for the card,
                and it reads as a key rather than as a caption repeated. */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 14,
                          paddingTop: 9, color: T.muted2, fontSize: 10 }}>
              <span style={{ display: 'inline-flex', alignItems: 'center',
                             gap: 5 }}>
                <span style={{ width: 7, height: 7, borderRadius: 4,
                               background: T.muted2, flexShrink: 0 }} />
                Book line
              </span>
              <span style={{ display: 'inline-flex', alignItems: 'center',
                             gap: 5 }}>
                {/* The card's own colour, not a fixed one — each row's
                    projection dot carries ITS lean, so a white swatch here
                    would contradict every dot it is meant to explain. */}
                <span style={{ width: 9, height: 9, borderRadius: 5,
                               background: tone, flexShrink: 0 }} />
                Baseline projection
              </span>
              <span style={{ marginLeft: 'auto', whiteSpace: 'nowrap' }}>
                gap = edge
              </span>
            </div>

            {g.rows.map((r, i) => {
              const st = sideTone(r._state === 'done' ? r.edge : null)
              return (
                <div key={r.key} style={{
                  paddingTop: 10, marginTop: i ? 10 : 0,
                  borderTop: `1px solid ${T.glassLine}`,
                }}>
                  <div style={{ display: 'flex', alignItems: 'baseline',
                                gap: 8 }}>
                    <span style={{ fontFamily: T.cond, fontWeight: 800,
                                   fontSize: 12, letterSpacing: 1,
                                   textTransform: 'uppercase', color: T.muted,
                                   flex: 1, minWidth: 0, whiteSpace: 'nowrap',
                                   overflow: 'hidden',
                                   textOverflow: 'ellipsis' }}>
                      {shortProp(r.propType)}
                    </span>
                    <span style={{ fontFamily: T.cond, fontWeight: 800,
                                   fontSize: 15, color: st.tone,
                                   letterSpacing: 0.4 }}>{st.side || '—'}</span>
                    <span style={{ fontSize: 15, fontWeight: 800,
                                   color: T.white,
                                   fontVariantNumeric: 'tabular-nums' }}>
                      {fmt(r.line, Number.isInteger(r.line) ? 0 : 1)}
                    </span>
                  </div>

                  {r._state === 'done' ? (
                    <>
                      <div style={{ display: 'flex', alignItems: 'baseline',
                                    gap: 8, marginTop: 3 }}>
                        <span style={{ color: T.muted2, fontSize: 11 }}>
                          line <b style={{ color: T.muted, fontSize: 12.5 }}>
                            {fmt(r.line, Number.isInteger(r.line) ? 0 : 1)}</b>
                        </span>
                        <span style={{ color: T.muted2, fontSize: 11 }}>
                          proj <b style={{ color: st.tone, fontSize: 13 }}>
                            {fmt(r.projection)}</b>
                        </span>
                        <span style={{ color: st.tone, fontSize: 11.5,
                                       fontWeight: 800 }}>
                          {r.edge > 0 ? '+' : ''}{fmt(r.edge)} edge
                        </span>
                        <div style={{ flex: 1 }} />
                        <ConfBar conf={r.confidence} tone={st.tone} max={104} />
                      </div>
                      <div style={{ marginTop: 5 }}>
                        <EdgeScale compact line={r.line} proj={r.projection}
                                   tone={st.tone} rgb={st.rgb} />
                      </div>
                    </>
                  ) : (
                    <div style={{ color: T.muted2, fontSize: 11.5,
                                  marginTop: 3 }}>
                      {r._state === 'loading' ? 'Projecting…'
                        : 'No projection for this line yet.'}
                    </div>
                  )}

                  {footNoteFor ? (
                    <div style={{ color: T.muted2, fontSize: 10.5,
                                  marginTop: 4 }}>{footNoteFor(r)}</div>
                  ) : null}
                </div>
              )
            })}

            <div style={{ display: 'flex', alignItems: 'center', gap: 10,
                          marginTop: 12, paddingTop: 10,
                          borderTop: `1px solid ${T.glassLine}` }}>
              <span style={{ color: T.muted2, fontSize: 11, flex: 1,
                             minWidth: 0, whiteSpace: 'nowrap',
                             overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {startTimeLabel(best.startTs) ? `⏱ ${startTimeLabel(best.startTs)}` : ''}
              </span>
              <Heart active={saved} onClick={onSave} />
              <button onClick={(e) => { e.stopPropagation(); onOpen?.() }}
                      style={{
                minHeight: 36, padding: '0 14px', borderRadius: T.r1,
                cursor: 'pointer', background: 'rgba(255,255,255,0.05)',
                border: `1px solid ${T.glassLine}`, color: T.white,
                fontFamily: T.cond, fontWeight: 800, fontSize: 12,
                letterSpacing: 1, textTransform: 'uppercase',
                WebkitTapHighlightColor: 'transparent', flexShrink: 0,
              }}>Player →</button>
            </div>
          </div>
        </div>
      ) : null}
    </Card>
  )
}

// Group priced rows by the PERSON AND THE MATCH. Keyed on both because a name
// alone would merge a player's two fixtures into one card, and on an NFL board
// that spans two slates it would put Sunday's line under Saturday's game.
//
// Within a group the best play leads — conviction tier first, then edge — so
// the closed card shows the reason to open it. Groups are ordered by their own
// best play, so the board stays sorted by exactly what it sorted by before.
export function groupByPlayer(rows) {
  const m = new Map()
  for (const r of rows) {
    const k = `${r.player}|${r.opponent}|${r.slate_date || ''}`
    if (!m.has(k)) m.set(k, { key: k, player: r.player, opponent: r.opponent,
                              rows: [] })
    m.get(k).rows.push(r)
  }
  const rank = (a, b) => (tier(b.confidence).weight - tier(a.confidence).weight)
    || (Math.abs(b.edge ?? -1) - Math.abs(a.edge ?? -1))
  const out = [...m.values()]
  for (const g of out) g.rows.sort(rank)
  out.sort((a, b) => rank(a.rows[0], b.rows[0]))
  return out
}


