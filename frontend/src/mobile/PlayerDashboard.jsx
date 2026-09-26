import { useState, useEffect, useMemo } from 'react'
import { T, SAFE_TOP, SURFACE_TINT } from './theme'
import { Card, Heart, Spinner, Empty, SectionLabel, tier, sideTone, SideRail,
         TierBadge, ConfBar, BigStat, tierCardStyle } from './bits'
import PlayerPhoto from './PlayerPhoto'
import { GameLogChart, HitRing, FormStrip } from './viz'
import { fetchForm, fetchStats, fetchNextMatch, fetchHistory } from '../utils/api'
import { normName, hitStrip, shortProp, fmt, prettyDate, startTimeLabel, PROP_TYPES, mergedBoardRows } from './data'
import { projectRow, cachedProjection, resolvePlayer } from './project'
import { useBookmarks, playerBookmarkId } from './useBookmarks'

const HISTORY_PROPS = PROP_TYPES.filter(p => p.history)

function mostPlayedSurface(stats) {
  if (!stats) return null
  let best = null, n = -1
  for (const s of ['Hard', 'Clay', 'Grass']) {
    const m = stats[s]?.matches_played || 0
    if (m > n) { n = m; best = s }
  }
  return n > 0 ? best : null
}

export default function PlayerDashboard({ player, boards, onClose, onOpenPlayer, onProject }) {
  // The tour is authoritative only once resolved (a board tap carries no tour).
  const [resolvedTour, setResolvedTour] = useState(player.tour || 'ATP')
  const [pid, setPid] = useState(player.id ? String(player.id) : null)
  const [rank, setRank] = useState(player.currentRank ?? null)
  const [form, setForm] = useState(null)
  const [stats, setStats] = useState(null)
  const [next, setNext] = useState(null)
  const [coreLoading, setCoreLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [histories, setHistories] = useState({})
  const [propProj, setPropProj] = useState({})

  const { has, toggle } = useBookmarks()
  const bmId = playerBookmarkId(player.name)

  // BOTH books. This took boards[book] — whichever the Boards tab happened to
  // be showing — so a player's Underdog props simply did not exist here unless
  // you had toggled that first.
  // THE PROP THAT WAS TAPPED LEADS. Opening this from a board row used to carry
  // only the player, so a reader who picked one prop out of several landed on a
  // list in board order and had to find it again. `player.prop` is set by the
  // board row; everything else keeps its existing order behind it.
  const boardRows = useMemo(() => {
    const rows = mergedBoardRows(boards)
      .filter(r => normName(r.player) === normName(player.name))
    if (!player.prop) return rows
    return [...rows].sort((a, b) =>
      (b.propType === player.prop ? 1 : 0) - (a.propType === player.prop ? 1 : 0))
  }, [boards, player.name, player.prop])
  const lineByProp = useMemo(() => {
    const m = {}
    for (const r of boardRows) if (r.line != null) m[r.propType] = r.line
    return m
  }, [boardRows])
  // WHICH SIDE WE ARE ON, per prop — so a hit strip reads as "our side landed"
  // instead of a bare over-count the reader has to invert on an UNDER. Comes
  // from the projection, which arrives after the board row, so a prop with no
  // projection yet simply has no side and the strip says "over rate".
  const leanByProp = useMemo(() => {
    const m = {}
    for (const r of boardRows) {
      const e = propProj[r.key]?.edge
      if (typeof e === 'number' && e !== 0) {
        m[r.propType] = e > 0 ? 'OVER' : 'UNDER'
      }
    }
    return m
  }, [boardRows, propProj])

  // Resolve id + CORRECT tour, then load core data. From search/bookmarks we
  // already have an id (its tour is gender-derived, reliable); from a board tap
  // we have only a name, so resolve across both tours by exact name — otherwise
  // a WTA name can lock onto a fuzzy male ATP match.
  useEffect(() => {
    let alive = true
    setCoreLoading(true); setNotFound(false)
    ;(async () => {
      const p = player.id
        ? { id: String(player.id), tour: player.tour || 'ATP', rank: player.currentRank ?? null }
        : await resolvePlayer(player.name, player.tour)
      if (!alive) return
      if (!p || !p.id) { setNotFound(true); setCoreLoading(false); return }
      setPid(p.id); setRank(p.rank ?? player.currentRank ?? null); setResolvedTour(p.tour)
      const [f, s, n] = await Promise.all([
        fetchForm(p.id, p.tour).catch(() => null),
        fetchStats(p.id, p.tour, player.name).catch(() => null),
        fetchNextMatch(p.id, p.tour).catch(() => null),
      ])
      if (!alive) return
      setForm(f); setStats(s); setNext(n); setCoreLoading(false)
    })()
    return () => { alive = false }
  }, [player.name, player.id, player.tour])

  const primarySurface = next?.surface || boardRows[0]?.surface || mostPlayedSurface(stats) || 'Hard'

  // Per-prop match logs (for the hit strips) — lazy, once id + surface known.
  useEffect(() => {
    if (!pid) return
    let alive = true
    HISTORY_PROPS.forEach(async (p) => {
      setHistories(h => ({ ...h, [p.key]: { loading: true } }))
      try {
        const data = await fetchHistory(pid, resolvedTour, p.key, primarySurface, 0)
        if (alive) setHistories(h => ({ ...h, [p.key]: { loading: false, data } }))
      } catch { if (alive) setHistories(h => ({ ...h, [p.key]: { loading: false, err: true } })) }
    })
    return () => { alive = false }
  }, [pid, primarySurface, resolvedTour])

  // Project this player's live PrizePicks props (shared cache with the Board).
  useEffect(() => {
    let alive = true
    boardRows.forEach(r => {
      const c = cachedProjection(r)
      if (c !== undefined) { setPropProj(m => (r.key in m ? m : { ...m, [r.key]: c || { failed: true } })); return }
      setPropProj(m => (m[r.key]?.loading ? m : { ...m, [r.key]: { loading: true } }))
      // priority: this is the page the reader is on, and the board
      // underneath has up to 120 speculative projections queued.
      projectRow(r, resolvedTour, { priority: true }).then(res => { if (alive) setPropProj(m => ({ ...m, [r.key]: res || { failed: true } })) })
    })
    return () => { alive = false }
  }, [boardRows, resolvedTour])

  const hand = stats?.ta_stats?.handedness
  const handLabel = hand === 'R' ? 'Right-handed' : hand === 'L' ? 'Left-handed' : null

  return (
    <div className="no-scrollbar" style={{
      position: 'fixed', inset: 0, zIndex: 1500, overflowY: 'auto',
      overflowX: 'hidden', animation: 'fade-in 160ms ease',
      background: T.ground,
      backgroundImage:
        `radial-gradient(900px 480px at 12% -6%, ${T.green}14, transparent 62%),`
        + `radial-gradient(760px 440px at 96% 6%, ${T.blue}0F, transparent 66%)`,
    }}>
      {/* Top bar */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 5, background: 'rgba(7,7,7,0.92)',
        borderBottom: `1px solid ${T.border}`, paddingTop: SAFE_TOP,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: `calc(6px + ${SAFE_TOP}) 8px 6px`,
      }}>
        <button onClick={onClose} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, minHeight: 44, padding: '0 8px', background: 'transparent', border: 'none', color: T.white, cursor: 'pointer', fontFamily: T.cond, fontWeight: 700, fontSize: 15 }}>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke={T.green} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 6l-6 6 6 6" /></svg>
          Back
        </button>
        <Heart active={has(bmId)} onClick={() => toggle({ id: bmId, kind: 'player', player: player.name, playerId: pid, tour: resolvedTour, currentRank: rank })} />
      </div>

      <div style={{ padding: '16px 16px 96px', maxWidth: 640, margin: '0 auto' }}>
        {/* Header */}
        <div style={{ display: 'flex', gap: 14, alignItems: 'center', marginBottom: 18 }}>
          <PlayerPhoto id={pid} name={player.name} size={78} />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 25, color: T.white, letterSpacing: 0.3, lineHeight: 1.05 }}>{player.name}</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
              <Badge>{resolvedTour}</Badge>
              {rank != null && <Badge>#{rank}</Badge>}
              {handLabel && <Badge>{handLabel}</Badge>}
            </div>
          </div>
        </div>

        {coreLoading && (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner size={28} /></div>
        )}

        {!coreLoading && notFound && (
          <Empty icon="🔍" title="Couldn't find this player" hint="The data source didn't return a match for this name." />
        )}

        {!coreLoading && !notFound && (
          <>
            {/* Next match */}
            {next?.opponent_name && (
              <Card style={{ padding: 14, marginBottom: 18 }}>
                <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 11, letterSpacing: 2, textTransform: 'uppercase', color: T.green, marginBottom: 6 }}>Next Match</div>
                <div style={{ color: T.white, fontSize: 15.5, fontWeight: 600 }}>vs {next.opponent_name}</div>
                <div style={{ color: T.muted, fontSize: 12.5, marginTop: 3 }}>
                  {next.tournament || ''}{next.surface ? ` · ${next.surface}` : ''}{startTimeLabel(next.start_timestamp) ? ` · ${startTimeLabel(next.start_timestamp)}` : ''}
                </div>
              </Card>
            )}

            {/* Live props for this player across BOTH books, projected on the fly */}
            {boardRows.length > 0 && (
              <section style={{ marginBottom: 22 }}>
                <SectionLabel>Live Props</SectionLabel>
                {boardRows.map((r, i) => {
                  const p = propProj[r.key] || {}
                  const done = !p.loading && !p.failed && p.projection != null
                  const s = sideTone(done ? p.edge : null)
                  // STRAIGHT TO THE FULL PROJECTION. This is the end of the
                  // road otherwise: a reader who came from the board, found the
                  // prop they cared about and wanted the breakdown had to go to
                  // the Project tab and retype the matchup they were already
                  // looking at. The ids are whatever this card already resolved
                  // for its own number, so the projection opens pre-filled and
                  // runs without a second search.
                  const cached = cachedProjection(r) || {}
                  const goProject = onProject ? () => onProject({
                    sport: 'tennis',
                    player: player.name,
                    opponent: r.opponent,
                    playerId: cached.playerId || pid,
                    opponentId: cached.opponentId,
                    tour: cached.tour || resolvedTour,
                    surface: cached.surface || r.surface,
                    court: r.tournament || '',
                    prop: r.propType,
                    line: r.line,
                  }) : undefined
                  return (
                    <Card key={r.key} index={i} onClick={goProject} style={{
                      padding: 0, marginBottom: 10,
                      position: 'relative', overflow: 'hidden',
                      ...tierCardStyle(done ? p.confidence : null, s.rgb),
                    }}>
                      <SideRail rgb={done ? s.rgb : null} weight={tier(done ? p.confidence : null).weight} />
                      <div style={{ padding: '13px 13px 13px 18px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
                          <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 17,
                                         color: T.white, letterSpacing: 0.4 }}>
                            {shortProp(r.propType)}
                          </span>
                          {(r.books || []).map(b => (
                            <span key={b} style={{
                              fontSize: 9.5, fontWeight: 800, letterSpacing: 0.4, padding: '2px 5px', borderRadius: 4,
                              color: b === 'prizepicks' ? T.green : '#42A5F5',
                              background: b === 'prizepicks' ? 'rgba(0,230,118,0.10)' : 'rgba(66,165,245,0.10)',
                              border: `1px solid ${b === 'prizepicks' ? 'rgba(0,230,118,0.25)' : 'rgba(66,165,245,0.25)'}`,
                            }}>{b === 'prizepicks' ? 'PP' : 'UD'}</span>
                          ))}
                          {done && <TierBadge conf={p.confidence} tone={s.tone} rgb={s.rgb} />}
                        </div>

                        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 11 }}>
                          <div style={{ minWidth: 0, flex: 1 }}>
                            {/* The CALL, same as the board row this was opened from. */}
                            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                              <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 21,
                                             color: s.tone, letterSpacing: 0.5 }}>
                                {s.side || '—'}
                              </span>
                              <span style={{ fontSize: 18, fontWeight: 800, color: T.white,
                                             fontVariantNumeric: 'tabular-nums' }}>
                                {fmt(r.line, Number.isInteger(r.line) ? 0 : 1)}
                              </span>
                            </div>
                            <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 10,
                                          letterSpacing: 1.1, color: T.muted2, marginTop: 3 }}>
                              LINE {fmt(r.line, Number.isInteger(r.line) ? 0 : 1)}
                            </div>
                          </div>

                          {/* THE EDGE IS THE HEADLINE, not the confidence ring.
                              A gauge takes the most space on the card and is the
                              least actionable thing on it — you still have to
                              read the number to learn anything. The edge is why
                              this row is worth a look, so it gets the size. */}
                          {done ? (
                            <BigStat tone={s.tone} rgb={s.rgb}
                              proj={fmt(p.projection)}
                              value={`${p.edge > 0 ? '+' : ''}${fmt(p.edge)}`}
                              label="EDGE" />
                          ) : (
                            <div style={{ minWidth: 86, textAlign: 'center' }}>
                              {p.loading ? <Spinner size={16} />
                                : <span style={{ color: T.muted2, fontSize: 13 }}>—</span>}
                            </div>
                          )}
                        </div>

                        {((done && p.confidence != null) || goProject) && (
                          <div style={{ display: 'flex', alignItems: 'center',
                                        justifyContent: 'space-between', gap: 10,
                                        marginTop: 10 }}>
                            {/* The card is tappable and nothing said so, which
                                is the same as it not being tappable. */}
                            {goProject ? (
                              <span style={{ fontFamily: T.cond, fontWeight: 800,
                                             fontSize: 10, letterSpacing: 1.1,
                                             color: T.muted2, whiteSpace: 'nowrap' }}>
                                FULL PROJECTION →
                              </span>
                            ) : <span />}
                            {done && p.confidence != null
                              ? <ConfBar conf={p.confidence} tone={s.tone} max={150} />
                              : null}
                          </div>
                        )}
                      </div>
                    </Card>
                  )
                })}
              </section>
            )}

            {/* Recent form */}
            <section style={{ marginBottom: 22 }}>
              <SectionLabel right={form?.streak_len ? <span style={{ color: form.streak_type === 'W' ? T.green : T.red, fontFamily: T.cond, fontWeight: 800, fontSize: 13 }}>{form.streak_type}{form.streak_len} streak</span> : null}>
                Last 10
              </SectionLabel>
              {Array.isArray(form?.last10) && form.last10.length ? (
                <>
                  <div className="no-scrollbar" style={{ display: 'flex', gap: 8, overflowX: 'auto', paddingBottom: 4 }}>
                    {form.last10.map((m, i) => (
                      <div key={i} style={{ flex: '0 0 auto', width: 76, background: T.card, border: `1px solid ${T.border}`, borderRadius: 10, padding: '10px 6px', textAlign: 'center' }}>
                        <div style={{ width: 26, height: 26, borderRadius: '50%', margin: '0 auto 6px', display: 'flex', alignItems: 'center', justifyContent: 'center', background: m.won ? 'rgba(0,230,118,0.15)' : 'rgba(255,68,68,0.15)', color: m.won ? T.green : T.red, fontFamily: T.cond, fontWeight: 800, fontSize: 14 }}>{m.won ? 'W' : 'L'}</div>
                        <div style={{ color: T.white, fontSize: 10.5, lineHeight: 1.2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{m.opponent || '—'}</div>
                        <div style={{ color: T.muted2, fontSize: 9.5, marginTop: 2 }}>{prettyDate(m.date) || m.surface || ''}</div>
                      </div>
                    ))}
                  </div>
                  <div style={{ color: T.muted2, fontSize: 10.5, marginTop: 8 }}>Per-match scores aren't exposed by the data source.</div>
                </>
              ) : <Empty title="No recent matches" />}
            </section>

            {/* Per-prop hit strips */}
            <section style={{ marginBottom: 22 }}>
              <SectionLabel right={<span style={{ color: T.muted2, fontSize: 11 }}>{primarySurface} · L10</span>}>Prop Hit Rates</SectionLabel>
              {/* Same ordering rule as the props list above: whichever prop was
                  tapped on the board is the one the reader came here to read,
                  so its strip goes first rather than sitting at a fixed index. */}
              {(player.prop
                ? [...HISTORY_PROPS].sort((a, b) =>
                    (b.key === player.prop ? 1 : 0) - (a.key === player.prop ? 1 : 0))
                : HISTORY_PROPS).map(p => (
                <HitStrip key={p.key} prop={p} state={histories[p.key]}
                          refLine={lineByProp[p.key]}
                          lean={leanByProp[p.key]} surface={primarySurface} />
              ))}
            </section>

            {/* Surface splits */}
            <SurfaceSplits stats={stats} />
          </>
        )}
      </div>
    </div>
  )
}

// ── ONE PROP'S HISTORY, IN DETAIL ────────────────────────────────────────────
// The same treatment the NFL card got, from the same components in viz.jsx —
// not a tennis lookalike. This was a pair of "3O 2U" counts and a five-bar
// sparkline, which is a table cell with a picture next to it.
//
// WHAT CHANGED BEYOND THE LOOK: the counts were always over/under, so on a prop
// we lean UNDER the reader had to invert them in their head. Everything here is
// stated as OUR SIDE — hit rate, form strip, bar colours — once a line and a
// side are known. With no board line there is no side, so it falls back to the
// player's average and says so.
function HitStrip({ prop, state, refLine, lean, surface }) {
  if (!state || state.loading) return (
    <Card style={{ padding: 14, marginBottom: 10, display: 'flex',
                   alignItems: 'center', gap: 10 }}>
      <Spinner size={16} />
      <span style={{ color: T.muted, fontSize: 13 }}>{prop.short}…</span>
    </Card>
  )
  if (state.err || !state.data) return null
  const s = hitStrip(state.data, refLine)
  if (!s.l10.n) return (
    <Card style={{ padding: '12px 14px', marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 15,
                       color: T.white }}>{prop.short}</span>
        <span style={{ color: T.muted2, fontSize: 12 }}>no log on surface</span>
      </div>
    </Card>
  )

  const ref = s.ref
  const dp = Number.isInteger(ref) ? 0 : 1
  // values arrive NEWEST-FIRST from the API; every chart here reads
  // oldest→newest, which is the direction a reader expects time to run.
  const games = [...(s.values || [])].reverse()
    .map((v, i) => ({ v, wk: i + 1 }))
    .filter(g => typeof g.v === 'number')

  // No lean means no side, and a hit rate needs one. OVER is the honest default
  // only when we are actually on a prop; against a bare average it would be an
  // invented position, so that case shows the over-rate and labels it that way.
  const side = (lean || '').toUpperCase()
  const haveSide = side === 'OVER' || side === 'UNDER'
  const over = side !== 'UNDER'
  const hits10 = over ? s.l10.o : s.l10.u
  const tone = haveSide ? (over ? T.green : T.red) : T.amber

  return (
    <Card style={{ padding: '13px 14px 14px', marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 16,
                       color: T.white, letterSpacing: 0.3, flex: 1 }}>
          {prop.short}
        </span>
        {haveSide ? (
          <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 12,
                         letterSpacing: 1, color: tone, padding: '2px 8px',
                         borderRadius: 6, background: `${tone}1C`,
                         border: `1px solid ${tone}55` }}>{side}</span>
        ) : null}
        <span style={{ color: T.muted, fontSize: 11.5 }}>
          {refLine != null ? `line ${fmt(ref, dp)}` : `avg ${fmt(ref)}`}
        </span>
      </div>

      {ref != null && games.length ? (
        <div style={{ marginTop: 11 }}>
          <GameLogChart games={games} line={ref} over={over}
                        accent={haveSide ? tone : T.amber} height={96} />
        </div>
      ) : null}

      <div style={{ display: 'flex', alignItems: 'center', gap: 14,
                    marginTop: 10 }}>
        <HitRing hits={hits10} n={s.l10.n}
                 label={haveSide ? 'HIT RATE' : 'OVER RATE'} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5,
                        letterSpacing: 1.1, color: T.muted2, marginBottom: 5 }}>
            LAST {Math.min(8, games.length)}
          </div>
          <FormStrip games={games} line={ref} over={over} />
          <div style={{ marginTop: 9, fontSize: 12, color: T.muted }}>
            {s.l5.n ? (
              <>Last 5 <b style={{ color: T.white }}>
                {over ? s.l5.o : s.l5.u}/{s.l5.n}</b> · </>
            ) : null}
            {typeof s.average === 'number' ? (
              <>averages <b style={{ color: T.white, fontSize: 13.5 }}>
                {fmt(s.average)}</b></>
            ) : null}
          </div>
        </div>
      </div>

      <div style={{ color: T.muted2, fontSize: 10.5, marginTop: 10,
                    paddingTop: 8, borderTop: `1px solid ${T.border}` }}>
        {s.sample} match{s.sample === 1 ? '' : 'es'} on {surface}
      </div>
    </Card>
  )
}


// ── SURFACE SPLITS ───────────────────────────────────────────────────────────
// This was an actual <table> — the single most spreadsheet-like thing in the
// app, and the one place a reader had to cross-reference a row against a column
// header to learn anything.
//
// A card per surface instead, in the surface's own colour. The three numbers a
// tennis prop actually turns on — aces, double faults, break points — sit
// beside each other WITH A BAR SCALED ACROSS THE SURFACES, so "12.4 aces on
// grass" is visibly bigger than "6.1 on clay" before either is read. Scaling
// each row against its own maximum is what makes the comparison work; scaling
// everything against one global maximum would flatten the small numbers to
// nothing.
const SURF_STATS = [
  ['aces', 'Aces / match', 1],
  ['double_faults', 'DF / match', 1],
  ['bp_generated_per_match', 'BP won / match', 1],
]

function SurfaceSplits({ stats }) {
  if (!stats) return null
  const surfaces = ['Hard', 'Clay', 'Grass']
    .filter(s => (stats[s]?.matches_played || 0) > 0)
  if (!surfaces.length) return null
  // Per-stat maximum ACROSS surfaces, so each bar is read against its peers.
  const peak = {}
  for (const [k] of SURF_STATS) {
    peak[k] = Math.max(...surfaces.map(s => stats[s]?.[k] || 0), 0.0001)
  }
  return (
    <section style={{ marginBottom: 8 }}>
      <SectionLabel>Surface splits</SectionLabel>
      <div style={{ display: 'grid', gap: 9,
                    gridTemplateColumns: 'repeat(auto-fit, minmax(232px, 1fr))' }}>
        {surfaces.map((s, i) => {
          const d = stats[s] || {}
          const c = SURFACE_TINT[s] || T.green
          const wr = typeof d.win_rate === 'number' ? d.win_rate : null
          return (
            <Card key={s} index={i} style={{
              padding: '13px 14px', position: 'relative', overflow: 'hidden',
              border: `1px solid ${c}3D`,
            }}>
              <div style={{
                position: 'absolute', inset: 0, pointerEvents: 'none',
                background: `radial-gradient(120% 80% at 0% 0%, ${c}1A, transparent 62%)`,
              }} />
              <div style={{ position: 'relative' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ width: 9, height: 9, borderRadius: 5,
                                 background: c, boxShadow: `0 0 10px ${c}` }} />
                  <span style={{ fontFamily: T.cond, fontWeight: 800,
                                 fontSize: 17, letterSpacing: 0.6,
                                 color: T.white, flex: 1 }}>{s}</span>
                  <span style={{ color: T.muted2, fontSize: 11 }}>
                    {d.matches_played} match{d.matches_played === 1 ? '' : 'es'}
                  </span>
                </div>

                {wr != null ? (
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 7,
                                marginTop: 9 }}>
                    <span style={{ fontSize: 27, fontWeight: 800, lineHeight: 1,
                                   letterSpacing: -0.6,
                                   color: wr >= 50 ? T.green : T.red,
                                   fontVariantNumeric: 'tabular-nums' }}>
                      {fmt(wr, 0)}%
                    </span>
                    <span style={{ fontFamily: T.cond, fontWeight: 700,
                                   fontSize: 10, letterSpacing: 1.1,
                                   color: T.muted2 }}>WIN RATE</span>
                  </div>
                ) : null}

                <div style={{ marginTop: 11 }}>
                  {SURF_STATS.map(([k, label, dp]) => {
                    const v = d[k]
                    return (
                      <div key={k} style={{ marginTop: 8 }}>
                        <div style={{ display: 'flex', alignItems: 'baseline',
                                      gap: 8, marginBottom: 3 }}>
                          <span style={{ color: T.muted, fontSize: 11.5,
                                         flex: 1 }}>{label}</span>
                          <b style={{ color: T.white, fontSize: 13.5,
                                      fontVariantNumeric: 'tabular-nums' }}>
                            {v != null ? fmt(v, dp) : '—'}
                          </b>
                        </div>
                        <div style={{ height: 5, borderRadius: 3,
                                      background: '#151515', overflow: 'hidden' }}>
                          <div style={{
                            width: `${Math.min(100, ((v || 0) / peak[k]) * 100)}%`,
                            height: '100%', borderRadius: 3, background: c,
                            opacity: 0.85,
                          }} />
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            </Card>
          )
        })}
      </div>
    </section>
  )
}

function Badge({ children }) {
  return <span style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 12, letterSpacing: 0.8, textTransform: 'uppercase', color: T.muted, background: T.card, border: `1px solid ${T.border}`, borderRadius: 7, padding: '3px 9px' }}>{children}</span>
}
