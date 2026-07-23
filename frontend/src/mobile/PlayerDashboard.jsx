import { useState, useEffect, useMemo } from 'react'
import { T, SAFE_TOP } from './theme'
import { Card, Heart, Spinner, Empty, SectionLabel, Delta } from './bits'
import PlayerPhoto from './PlayerPhoto'
import ConfidenceGauge from '../components/ConfidenceGauge'
import Last5Bars from '../components/Last5Bars'
import { fetchForm, fetchStats, fetchNextMatch, fetchHistory } from '../utils/api'
import { normName, hitStrip, shortProp, fmt, prettyDate, startTimeLabel, PROP_TYPES } from './data'
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

export default function PlayerDashboard({ player, board, onClose, onOpenPlayer }) {
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

  const boardRows = useMemo(
    () => (board?.rows || []).filter(r => normName(r.player) === normName(player.name)),
    [board, player.name])
  const lineByProp = useMemo(() => {
    const m = {}
    for (const r of boardRows) if (r.line != null) m[r.propType] = r.line
    return m
  }, [boardRows])

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
      const c = cachedProjection(r.key)
      if (c !== undefined) { setPropProj(m => (r.key in m ? m : { ...m, [r.key]: c || { failed: true } })); return }
      setPropProj(m => (m[r.key]?.loading ? m : { ...m, [r.key]: { loading: true } }))
      projectRow(r, resolvedTour).then(res => { if (alive) setPropProj(m => ({ ...m, [r.key]: res || { failed: true } })) })
    })
    return () => { alive = false }
  }, [boardRows, resolvedTour])

  const hand = stats?.ta_stats?.handedness
  const handLabel = hand === 'R' ? 'Right-handed' : hand === 'L' ? 'Left-handed' : null

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 1500, background: T.bg, overflowY: 'auto', animation: 'fade-in 160ms ease' }} className="no-scrollbar">
      {/* Top bar */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 5, background: 'rgba(10,10,10,0.96)',
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

            {/* Live PrizePicks props for this player, projected on the fly */}
            {boardRows.length > 0 && (
              <section style={{ marginBottom: 22 }}>
                <SectionLabel>Live Props · PrizePicks</SectionLabel>
                {boardRows.map(r => {
                  const p = propProj[r.key] || {}
                  const done = !p.loading && !p.failed && p.projection != null
                  return (
                    <Card key={r.key} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 14px', marginBottom: 10 }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 16, color: T.white, letterSpacing: 0.3 }}>{shortProp(r.propType)}</div>
                        <div style={{ display: 'flex', gap: 14, marginTop: 8, alignItems: 'center' }}>
                          <Mini label="Line" value={fmt(r.line, Number.isInteger(r.line) ? 0 : 1)} />
                          <Mini label="Proj" value={done ? fmt(p.projection) : '—'} accent={done} />
                          <div style={{ textAlign: 'center' }}>
                            {done ? <Delta value={p.edge} size={16} />
                              : p.loading ? <Spinner size={14} />
                              : <span style={{ color: T.muted2, fontSize: 13 }}>—</span>}
                            <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5, letterSpacing: 1, textTransform: 'uppercase', color: T.muted2 }}>vs line</div>
                          </div>
                        </div>
                      </div>
                      {done && p.confidence != null && (
                        <div style={{ flex: '0 0 auto' }}>
                          <ConfidenceGauge confidence={Math.round(p.confidence)} size={76} showLabel={false} />
                        </div>
                      )}
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
              {HISTORY_PROPS.map(p => (
                <HitStrip key={p.key} prop={p} state={histories[p.key]} refLine={lineByProp[p.key]} playerName={player.name} />
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

function HitStrip({ prop, state, refLine, playerName }) {
  if (!state || state.loading) return (
    <Card style={{ padding: 14, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 10 }}>
      <Spinner size={16} /><span style={{ color: T.muted, fontSize: 13 }}>{prop.short}…</span>
    </Card>
  )
  if (state.err || !state.data) return null
  const s = hitStrip(state.data, refLine)
  if (!s.l10.n) return (
    <Card style={{ padding: '12px 14px', marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 15, color: T.white }}>{prop.short}</span>
        <span style={{ color: T.muted2, fontSize: 12 }}>no log on surface</span>
      </div>
    </Card>
  )
  // Newest-first values → oldest→newest for the bar chart; last 5.
  const barData = [...(s.values || [])].slice(0, 5).reverse().map(v => ({ label: '', val: v, isNA: v == null }))
  const ref = s.ref
  return (
    <Card style={{ padding: '12px 14px 6px', marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 16, color: T.white, letterSpacing: 0.3 }}>{prop.short}</span>
        <span style={{ color: T.muted, fontSize: 11.5 }}>
          {refLine != null ? `vs line ${fmt(ref, Number.isInteger(ref) ? 0 : 1)}` : `vs avg ${fmt(ref)}`}
        </span>
      </div>
      <div style={{ display: 'flex', gap: 16, marginTop: 8 }}>
        <Split label="Last 5" o={s.l5.o} u={s.l5.u} pu={s.l5.pu} />
        <Split label="Last 10" o={s.l10.o} u={s.l10.u} pu={s.l10.pu} />
        <div style={{ marginLeft: 'auto', color: T.muted2, fontSize: 10.5, alignSelf: 'center' }}>{s.sample} on {'surface'}</div>
      </div>
      {ref != null && <Last5Bars data={barData} propLine={ref} playerName={playerName} maxBarHeight={70} />}
    </Card>
  )
}

function Split({ label, o, u, pu }) {
  return (
    <div>
      <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5, letterSpacing: 1, textTransform: 'uppercase', color: T.muted2, marginBottom: 3 }}>{label}</div>
      <div style={{ display: 'flex', gap: 6, fontFamily: T.cond, fontWeight: 800, fontSize: 14 }}>
        <span style={{ color: T.green }}>{o}<span style={{ fontSize: 10, color: T.muted2 }}>O</span></span>
        <span style={{ color: T.red }}>{u}<span style={{ fontSize: 10, color: T.muted2 }}>U</span></span>
        {pu > 0 && <span style={{ color: T.amber }}>{pu}<span style={{ fontSize: 10, color: T.muted2 }}>P</span></span>}
      </div>
    </div>
  )
}

const SURF_ROWS = [
  ['matches_played', 'Matches', 0],
  ['win_rate', 'Win %', 0, '%'],
  ['aces', 'Aces/M', 1],
  ['double_faults', 'DF/M', 1],
  ['bp_generated_per_match', 'BP/M', 1],
]
function SurfaceSplits({ stats }) {
  if (!stats) return null
  const surfaces = ['Hard', 'Clay', 'Grass'].filter(s => (stats[s]?.matches_played || 0) > 0)
  if (!surfaces.length) return null
  return (
    <section style={{ marginBottom: 8 }}>
      <SectionLabel>Surface Splits</SectionLabel>
      <Card style={{ padding: 4, overflowX: 'auto' }} className="no-scrollbar">
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={thStyle}></th>
              {surfaces.map(s => <th key={s} style={{ ...thStyle, textAlign: 'right', color: T.green }}>{s}</th>)}
            </tr>
          </thead>
          <tbody>
            {SURF_ROWS.map(([key, label, dp, suf]) => (
              <tr key={key}>
                <td style={{ ...tdStyle, color: T.muted }}>{label}</td>
                {surfaces.map(s => (
                  <td key={s} style={{ ...tdStyle, textAlign: 'right', color: T.white, fontFamily: T.cond, fontWeight: 700 }}>
                    {stats[s]?.[key] != null ? `${fmt(stats[s][key], dp)}${suf || ''}` : '—'}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </section>
  )
}

const thStyle = { fontFamily: T.cond, fontWeight: 800, fontSize: 11, letterSpacing: 1, textTransform: 'uppercase', color: T.muted, padding: '8px 12px', textAlign: 'left' }
const tdStyle = { fontSize: 13.5, padding: '9px 12px', borderTop: `1px solid ${T.border}` }

function Badge({ children }) {
  return <span style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 12, letterSpacing: 0.8, textTransform: 'uppercase', color: T.muted, background: T.card, border: `1px solid ${T.border}`, borderRadius: 7, padding: '3px 9px' }}>{children}</span>
}
function Mini({ label, value, accent }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 16, color: accent ? T.green : T.white }}>{value}</div>
      <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5, letterSpacing: 1, textTransform: 'uppercase', color: T.muted2 }}>{label}</div>
    </div>
  )
}
