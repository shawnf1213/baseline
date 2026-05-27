import { useState, useCallback, useEffect, useMemo } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import NumberFlow from '@number-flow/react'
import {
  ScatterChart, Scatter, XAxis, YAxis, Tooltip, ResponsiveContainer,
  BarChart, Bar, CartesianGrid, Cell,
  LabelList,
} from 'recharts'
import PlayerSearch from '../components/PlayerSearch'
import StatCard from '../components/StatCard'
import LoadingSpinner from '../components/LoadingSpinner'
import LeanBadge from '../components/LeanBadge'
import ConfidenceGauge from '../components/ConfidenceGauge'
import SurfaceBadge from '../components/SurfaceBadge'
import { calcProp, fetchStats } from '../utils/api'
import { TOURNAMENT_CONFIG, fmt, fmtPct } from '../utils/constants'

const PROP_TYPES = ['Aces', 'Double Faults', 'Total Games', 'Break Points Won']
const SURFACES   = ['Hard', 'Clay', 'Grass']

const PROP_STAT_KEY = {
  'Aces': 'aces',
  'Double Faults': 'double_faults',
  'Total Games': 'total_match_games',
  'Break Points Won': 'bp_converted_count',
}

const ENV_COLORS = {
  HIGH_BREAK: '#FF9800', SERVE_DOM: '#42A5F5',
  RET_EDGE: '#00E676', WEAK_SERVE: '#EF5350', STANDARD: '#888',
}

// Surface color dots for selector
const SURFACE_DOT_COLORS = {
  Hard:  '#6b9fff',
  Clay:  '#ff6b35',
  Grass: '#00e676',
}

// Shared card style for static (non-interactive) projection cards
const STATIC_CARD_STYLE = {
  background: '#0a0f0c',
  border: '1px solid #1a2520',
  borderRadius: 12,
  padding: '20px 22px',
}
const STATIC_LABEL_STYLE = {
  fontFamily: '"Barlow Condensed", sans-serif',
  fontWeight: 700,
  fontSize: 9,
  letterSpacing: '0.3em',
  textTransform: 'uppercase',
  color: '#1a3a25',
  marginBottom: 8,
}

// Animation variants — defined at module level so Vite/ESBuild can tree-shake safely
const ANIMATION_CONTAINER = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.08 } },
}
const ANIMATION_ITEM = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0, transition: { duration: 0.3, ease: 'easeOut' } },
}

// ── Change 3: stat color helper ─────────────────────────────────────────────
function statColor(label, value) {
  const v = parseFloat(value)
  if (isNaN(v)) return undefined
  const l = label.toLowerCase()
  if (l.includes('ace')) return v > 6 ? '#00e676' : v >= 3 ? '#ffb300' : '#ff4444'
  if (l.includes('double') || l.includes('df')) return v < 1.5 ? '#00e676' : v <= 2.5 ? '#ffb300' : '#ff4444'
  if (l.includes('1st serve %') || l.includes('1st in') || l.includes('first in')) return v > 65 ? '#00e676' : v >= 55 ? '#ffb300' : '#ff4444'
  if (l.includes('1st') && l.includes('won')) return v > 78 ? '#00e676' : v >= 68 ? '#ffb300' : '#ff4444'
  if (l.includes('2nd') && l.includes('won')) return v > 58 ? '#00e676' : v >= 50 ? '#ffb300' : '#ff4444'
  if (l.includes('return') || l.includes('rpw')) return v > 42 ? '#00e676' : v >= 35 ? '#ffb300' : '#ff4444'
  if (l.includes('bp conv') || (l.includes('break') && l.includes('conv'))) return v > 48 ? '#00e676' : v >= 38 ? '#ffb300' : '#ff4444'
  if (l.includes('bp saved') || (l.includes('break') && l.includes('sav'))) return v > 68 ? '#00e676' : v >= 58 ? '#ffb300' : '#ff4444'
  if (l.includes('win') || l.includes('win%')) return v > 65 ? '#00e676' : v >= 50 ? '#ffb300' : '#ff4444'
  return undefined
}

// ── Change 8: Section divider with label ────────────────────────────────────
function SectionDivider({ label }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16, margin: '20px 0 12px' }}>
      <div style={{ flex: 1, height: 1, background: '#0d1510' }} />
      <span style={{
        fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700,
        fontSize: 9, letterSpacing: '0.3em', textTransform: 'uppercase',
        color: '#1a2a1e', whiteSpace: 'nowrap',
      }}>{label}</span>
      <div style={{ flex: 1, height: 1, background: '#0d1510' }} />
    </div>
  )
}

// ── Last 5 Matches Bar Chart ────────────────────────────────────────────────
function Last5Chart({ matches, statKey, propLine, playerName, surface, chartSource }) {
  if (!matches || !statKey) return null

  // Backend sends newest-first; reverse to chronological (oldest left → newest right)
  const last5 = matches.slice(0, 5).reverse()
  if (!last5.length) return (
    <div style={{ color: 'var(--muted)', fontSize: 13, padding: '16px 0' }}>
      No recent match data found in history
    </div>
  )

  // Map prop stat key to sofascore_surface_log field
  const resolveVal = (m) => {
    let v = m[statKey] ?? null
    // For Total Games: fall back to parsing the score string if total_match_games absent
    if (v == null && statKey === 'total_match_games' && m.score) {
      const sets = m.score.trim().split(/\s+/)
      let total = 0, ok = true
      for (const s of sets) {
        const parts = s.split('-').map(Number)
        if (parts.length !== 2 || parts.some(isNaN)) { ok = false; break }
        total += parts[0] + parts[1]
      }
      if (ok && total > 0) v = total
    }
    return v
  }

  // Build data including NA entries (challenger matches with no stats)
  const data = last5.map(m => {
    const val = resolveVal(m)
    const isNA = val == null
    let fill = '#555'
    if (!isNA && propLine > 0) {
      fill = val > propLine ? 'var(--green)' : val < propLine ? 'var(--red)' : '#555'
    }
    const dateStr = m.date || ''
    const opp = m.opponent_abbr || (m.opponent || '').split(' ').pop() || ''
    const label = dateStr && opp ? `${dateStr}\nvs ${opp}` : dateStr || opp || '?'
    // NA bars use a tiny stub value so Recharts renders them; real display is via CustomBar
    return { label, val: isNA ? 0 : Math.round(val * 10) / 10, fill, isNA }
  })

  const hasAnyStat  = data.some(d => !d.isNA)
  const totalFound  = matches.length
  const isAllSurf   = chartSource === 'sofascore_all'
  const isSackmann  = chartSource === 'sackmann'

  const countNote = totalFound < 5 ? (
    <div style={{ color: 'var(--amber)', fontSize: 11, marginBottom: 8, fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 600 }}>
      Only {totalFound} match{totalFound !== 1 ? 'es' : ''} found in recent history
    </div>
  ) : null

  const sourceNote = isSackmann ? (
    <div style={{ color: 'var(--amber)', fontSize: 11, marginBottom: 8, fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 600 }}>
      Historical data (2015–2020) — Sofascore not available
    </div>
  ) : null

  // Custom bar: NA entries render a small gray stub
  const CustomBar = (props) => {
    const { x, y, width, height, index } = props
    const d = data[index]
    if (d?.isNA) {
      const stubH = 6
      return <rect x={x} y={y - stubH} width={width} height={stubH} fill="#1e2e24" rx={3} />
    }
    return <rect x={x} y={y} width={width} height={height} fill={d?.fill || '#555'} rx={3} />
  }

  // Custom label: show "N/A" for NA entries, numeric value otherwise
  const CustomLabel = (props) => {
    const { x, y, width, index } = props
    const d = data[index]
    if (d?.isNA) {
      return (
        <text x={x + width / 2} y={y - 14} textAnchor="middle" fill="#2a3a30" fontSize={10} fontWeight={700}>
          N/A
        </text>
      )
    }
    return (
      <text x={x + width / 2} y={y - 5} textAnchor="middle" fill="var(--white)" fontSize={11} fontWeight={700}>
        {d?.val}
      </text>
    )
  }

  // Custom X tick: split "Apr 13\nvs Darderi" onto two lines
  const CustomTick = ({ x, y, payload }) => {
    const parts = (payload.value || '').split('\n')
    return (
      <g transform={`translate(${x},${y})`}>
        <text x={0} y={0} dy={12} textAnchor="middle" fill="var(--muted)" fontSize={10}>{parts[0]}</text>
        {parts[1] && <text x={0} y={0} dy={24} textAnchor="middle" fill="#2a3a30" fontSize={9}>{parts[1]}</text>}
      </g>
    )
  }

  if (!hasAnyStat) {
    // All entries are NA — show matches list with W/L results but no bar values
    return (
      <div>
        {sourceNote || countNote}
        <div style={{ color: 'var(--muted)', fontSize: 12, padding: '8px 0' }}>
          Match stats not available for these events — results only:
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {last5.map((m, i) => {
            const won = m.won
            const dateStr = m.date || ''
            const opp = m.opponent_abbr || (m.opponent || '').split(' ').pop() || '?'
            const score = m.score || ''
            const surf = m.surface || ''
            return (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--muted)' }}>
                <span style={{ fontWeight: 700, color: won ? 'var(--green)' : 'var(--red)', minWidth: 14 }}>
                  {won ? 'W' : 'L'}
                </span>
                <span>{dateStr} vs {opp}</span>
                {score && <span style={{ color: '#2a3a30' }}>{score}</span>}
                {surf && surf !== surface && <span style={{ color: '#2a3a30', fontSize: 10 }}>({surf})</span>}
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <div>
      {sourceNote || countNote}
      <div style={{ height: 170 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} barSize={40} margin={{ top: 20, right: 10, left: 0, bottom: 28 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e1e1e" vertical={false} />
            <XAxis dataKey="label" tick={<CustomTick />} axisLine={false} tickLine={false} interval={0} />
            <YAxis tick={{ fill: 'var(--muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
            <Tooltip
              contentStyle={{ background: '#1a1a1a', border: '1px solid var(--border)', borderRadius: 6 }}
              formatter={(v, _, { payload }) => [payload?.isNA ? 'N/A' : v, playerName]}
            />
            {propLine > 0 && (
              <CartesianGrid
                horizontal={false} vertical={false}
                horizontalPoints={[propLine]}
                stroke="var(--amber)" strokeDasharray="6 3"
              />
            )}
            <Bar dataKey="val" radius={[3, 3, 0, 0]} isAnimationActive shape={<CustomBar />}>
              <LabelList dataKey="val" content={<CustomLabel />} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function DotPlot({ matches, statKey }) {
  if (!matches || !statKey) return null
  const data = matches.slice(0, 20).map((m, i) => ({ i, v: m[statKey] ?? null })).filter(d => d.v != null)
  if (!data.length) return <div style={{ color: 'var(--muted)', fontSize: 13, padding: '12px 0' }}>No stat data available</div>

  return (
    <div style={{ height: 100 }}>
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart>
          <XAxis dataKey="i" hide />
          <YAxis dataKey="v" tick={{ fill: 'var(--muted)', fontSize: 11 }} />
          <Tooltip cursor={false} contentStyle={{ background: '#1a1a1a', border: '1px solid var(--border)', borderRadius: 6 }} formatter={v => [fmt(v), statKey]} />
          <Scatter data={data} fill="var(--green)" isAnimationActive />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  )
}

function Histogram({ matches, statKey, propLine }) {
  if (!matches || !statKey) return null
  const vals = matches.slice(0, 30).map(m => m[statKey]).filter(v => v != null)
  if (vals.length < 3) return null

  const min = Math.floor(Math.min(...vals))
  const max = Math.ceil(Math.max(...vals))
  const bins = Math.max(5, Math.min(10, max - min + 1))
  const width = (max - min) / bins
  const data = Array.from({ length: bins }, (_, i) => {
    const lo = min + i * width, hi = lo + width
    return { range: `${lo.toFixed(0)}-${hi.toFixed(0)}`, count: vals.filter(v => v >= lo && v < hi).length, lo }
  })

  return (
    <div style={{ height: 140 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e1e1e" />
          <XAxis dataKey="range" tick={{ fill: 'var(--muted)', fontSize: 10 }} />
          <YAxis tick={{ fill: 'var(--muted)', fontSize: 11 }} allowDecimals={false} />
          <Tooltip contentStyle={{ background: '#1a1a1a', border: '1px solid var(--border)', borderRadius: 6 }} />
          <Bar dataKey="count" radius={[3,3,0,0]} isAnimationActive>
            {data.map((d, i) => (
              <Cell key={i} fill={propLine > 0 && d.lo >= propLine ? 'var(--green)' : '#42A5F5'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function ConfidenceBreakdown({ breakdown }) {
  const [open, setOpen] = useState(false)
  if (!breakdown) return null

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
      <button onClick={() => setOpen(!open)} style={{
        width: '100%', padding: '12px 16px', background: 'var(--card)',
        border: 'none', color: 'var(--muted)', cursor: 'pointer',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 13,
      }}>
        <span>Confidence Breakdown</span>
        <span>{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div style={{ padding: '8px 16px 16px', background: 'var(--card)' }}>
          {Object.entries(breakdown).map(([key, info]) => {
            const score = info.score || 0
            const color = score > 0 ? 'var(--green)' : score < 0 ? 'var(--red)' : 'var(--muted)'
            return (
              <div key={key} style={{ display: 'flex', justifyContent: 'space-between', padding: '7px 0', borderBottom: '1px solid #151515' }}>
                <div>
                  <div style={{ fontSize: 12, color: 'var(--white)', fontWeight: 600 }}>{key.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase())}</div>
                  <div style={{ fontSize: 11, color: 'var(--muted)' }}>{info.label}</div>
                </div>
                <div style={{ fontSize: 13, fontWeight: 700, color }}>{score >= 0 ? '+' : ''}{score}/{info.max}</div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function StatMini({ label, v1, v2, n1, n2 }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid #151515' }}>
      <div style={{ fontSize: 11, color: 'var(--muted)', flex: 1 }}>{label}</div>
      <div style={{ fontWeight: 700, color: 'var(--white)', width: 60, textAlign: 'right' }}>{v1}</div>
      <div style={{ fontWeight: 700, color: 'var(--muted)', width: 60, textAlign: 'right' }}>{v2}</div>
    </div>
  )
}

// Handedness badge — R=gray, L=green
function HandBadge({ hand }) {
  if (!hand) return null
  const isLeft = hand === 'L'
  return (
    <span style={{
      display: 'inline-block',
      fontSize: 9,
      fontWeight: 800,
      padding: '1px 6px',
      borderRadius: 4,
      marginLeft: 7,
      letterSpacing: '.05em',
      verticalAlign: 'middle',
      background: isLeft ? 'var(--green)' : '#3a3a3a',
      color: isLeft ? '#000' : '#888',
      border: `1px solid ${isLeft ? 'var(--green)' : '#555'}`,
    }}>
      {isLeft ? 'L' : 'R'}
    </span>
  )
}

// ── Change 10: Custom surface selector ──────────────────────────────────────
function SurfaceSelector({ value, onChange }) {
  const SURF_STYLES = {
    Hard:  { active: { background: '#001a40', color: '#6b9fff', border: '1px solid #2a3d5a' }, dot: '#6b9fff' },
    Clay:  { active: { background: '#2a0800', color: '#ff6b35', border: '1px solid #5a2010' }, dot: '#ff6b35' },
    Grass: { active: { background: '#001a0b', color: '#00e676', border: '1px solid #1a4020' }, dot: '#00e676' },
  }
  return (
    <div style={{ display: 'flex', gap: 8 }}>
      {SURFACES.map(s => {
        const isActive = value === s
        const ss = SURF_STYLES[s]
        return (
          <button key={s} onClick={() => onChange(s)} style={{
            display: 'flex', alignItems: 'center', gap: 7,
            padding: '9px 16px', borderRadius: 10, cursor: 'pointer',
            fontFamily: '"Barlow Condensed", sans-serif',
            fontWeight: 700, fontSize: 12, letterSpacing: 1.5,
            textTransform: 'uppercase',
            transition: 'all .15s',
            ...(isActive ? ss.active : { background: '#0a0f0c', color: '#2a3a30', border: '1px solid #1a2520' }),
          }}>
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: isActive ? ss.dot : '#1a2520', display: 'inline-block', flexShrink: 0 }} />
            {s}
          </button>
        )
      })}
    </div>
  )
}

export default function PropProjection({ tour }) {
  const [p1, setP1] = useState(null)
  const [p2, setP2] = useState(null)
  const [surface,  setSurface]  = useState('Hard')
  const [court,    setCourt]    = useState('None')
  const [propType, setPropType] = useState('Aces')
  const [propLine, setPropLine] = useState(0)
  const [result,   setResult]   = useState(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState(null)
  const [ranFor,   setRanFor]   = useState(null)
  // Prefetched stats — stored in state so Surface Stats Comparison always has data
  // even when the null-projection backend path omits player_stats/opponent_stats
  const [p1PrefetchStats, setP1PrefetchStats] = useState(null)
  const [p2PrefetchStats, setP2PrefetchStats] = useState(null)

  // Warm backend cache only when BOTH players are selected
  useEffect(() => {
    if (!p1 || !p2) return
    fetchStats(String(p1.id), tour, p1.name || '').then(s => setP1PrefetchStats(s)).catch(() => {})
    fetchStats(String(p2.id), tour, p2.name || '').then(s => setP2PrefetchStats(s)).catch(() => {})
  }, [p1?.id, p2?.id, tour])

  // Tour-aware + surface-aware tournament list
  const tourKey = tour === 'WTA' ? 'WTA' : 'ATP'
  const courts = useMemo(() => {
    const list = TOURNAMENT_CONFIG[tourKey]?.[surface] || []
    return ['None', ...list.map(t => t.name)]
  }, [tourKey, surface])

  // Reset court when tour changes (surface change already resets in onChange handler)
  useEffect(() => {
    setCourt('None')
  }, [tour])

  const currentPair = p1 && p2 ? `${p1.id}-${p2.id}` : null
  const hasNewPair  = currentPair !== ranFor

  const run = useCallback(async () => {
    if (!p1 || !p2) return
    setLoading(true); setError(null); setResult(null)
    const payload = {
      player_id:     String(p1.id),
      opponent_id:   String(p2.id),
      player_name:   p1.name || '',
      opponent_name: p2.name || '',
      tour, surface, court: court === 'None' ? '' : court,
      prop_type: propType, prop_line: propLine,
    }
    console.log('[BASELINE] ▶ Request payload:', JSON.stringify(payload))
    try {
      const data = await calcProp(payload)
      console.log('[BASELINE] ✅ Top-level keys:', JSON.stringify(Object.keys(data)))
      console.log('[BASELINE] model_projection:', data.model_projection, '| type:', typeof data.model_projection)
      console.log('[BASELINE] confidence:', data.confidence, '| type:', typeof data.confidence)
      console.log('[BASELINE] player_stats:', JSON.stringify(data.player_stats))
      console.log('[BASELINE] opponent_stats keys:', data.opponent_stats ? JSON.stringify(Object.keys(data.opponent_stats)) : 'NULL')
      console.log('[BASELINE] player_surface_matches count:', data.player_surface_matches?.length)
      setResult(data)
      setRanFor(currentPair)
    } catch(e) {
      console.error('[BASELINE] ❌ Error:', e.message)
      setError(e.response?.data?.detail || e.message)
    } finally { setLoading(false) }
  }, [p1, p2, tour, surface, court, propType, propLine, currentPair])

  const section = (title) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16, margin: '20px 0 12px' }}>
      <div style={{ flex: 1, height: 1, background: '#0d1510' }} />
      <span style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 9, letterSpacing: '0.3em', textTransform: 'uppercase', color: '#1a2a1e', whiteSpace: 'nowrap' }}>{title}</span>
      <div style={{ flex: 1, height: 1, background: '#0d1510' }} />
    </div>
  )

  const statKey = PROP_STAT_KEY[propType]
  // Only compute edge when projection is a real number (not null)
  const hasProjection = result != null && result.model_projection != null
  const edge = hasProjection && propLine > 0 ? (result.model_projection - propLine) : null

  // Resolve surface stats: prefer result payload, fall back to prefetched data
  const p1SurfaceStats = result?.player_stats
    || p1PrefetchStats?.[surface]
    || p1PrefetchStats?.All
    || null
  const p2SurfaceStats = result?.opponent_stats
    || p2PrefetchStats?.[surface]
    || p2PrefetchStats?.All
    || null

  // Env color — computed at component level (used in results block)
  const envColor = ENV_COLORS[result?.environment] || '#888'

  // Inactivity warnings — computed at component level (not in IIFE inside JSX)
  const inactivityWarnings = useMemo(() => {
    const warnings = []
    const check = (player, stats) => {
      if (!player || !stats?.all_matches?.length) return
      const ts = stats.all_matches[0]?.timestamp
      if (!ts) return
      const days = Math.floor((Date.now() - ts * 1000) / 86400000)
      if (days > 21) warnings.push({ name: player.name, days })
    }
    check(p1, p1PrefetchStats)
    check(p2, p2PrefetchStats)
    return warnings
  }, [p1, p2, p1PrefetchStats, p2PrefetchStats])

  // Render-time diagnostics
  if (result) {
    console.log('[BASELINE] 🎨 Render — projection:', result.model_projection,
      '| confidence:', result.confidence,
      '| p1SurfaceStats.aces:', p1SurfaceStats?.aces,
      '| p1SurfaceStats.win_rate:', p1SurfaceStats?.win_rate)
  }

  // ── Change 2: edge-based border color for Book Line card ──
  const bookLineBorderColor = edge == null ? '#444' : edge > 0 ? '#00e676' : edge < 0 ? '#ff4444' : '#444'

  return (
    <div>
      {/* Players */}
      {section('Players')}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
        <PlayerSearch tour={tour} label="Selected Player" selected={p1} onSelect={p => {
          setP1(p); setResult(null); setP1PrefetchStats(null)
        }} />
        <PlayerSearch tour={tour} label="Opponent" selected={p2} onSelect={p => {
          setP2(p); setResult(null); setP2PrefetchStats(null)
        }} />
      </div>

      {/* Inactivity warnings — shown when a selected player hasn't played in > 21 days */}
      {inactivityWarnings.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          {inactivityWarnings.map((w, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 8,
              background: '#1a1000', border: '1px solid #5a3800',
              borderRadius: 8, padding: '7px 14px', marginBottom: 6,
            }}>
              <span style={{ fontSize: 13 }}>⚠</span>
              <span style={{
                fontSize: 11, fontWeight: 700, color: '#f5a623',
                fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 0.5,
              }}>
                {w.name} may be inactive or injured — last match was {w.days} days ago
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Match Setup */}
      {section('Match Setup')}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
        {/* ── Change 10: Surface custom selector with colored dots ── */}
        <div>
          <label style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.05em', display: 'block', marginBottom: 6 }}>Surface</label>
          <SurfaceSelector value={surface} onChange={s => { setSurface(s); setCourt('None'); setResult(null) }} />
        </div>
        <div>
          <label style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.05em', display: 'block', marginBottom: 6 }}>Court / Tournament</label>
          <select value={court} onChange={e => setCourt(e.target.value)} style={{
            width: '100%', padding: '11px 14px', background: '#0a0f0c',
            border: '1px solid #1a2520', borderRadius: 10, color: '#4a6a50',
            fontFamily: '"Barlow Condensed", sans-serif', fontSize: 13, fontWeight: 600,
            letterSpacing: 0.5,
          }}>
            {courts.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
      </div>

      {/* Prop type */}
      {section('Prop Type')}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16 }}>
        {PROP_TYPES.map(pt => (
          <button key={pt} onClick={() => { setPropType(pt); setResult(null) }} style={{
            padding: '10px 20px', borderRadius: 10, cursor: 'pointer',
            fontFamily: '"Barlow Condensed", sans-serif',
            fontWeight: 700, fontSize: 12, letterSpacing: 1.5,
            textTransform: 'uppercase',
            background: propType === pt ? '#00e676' : '#0a0f0c',
            color: propType === pt ? '#000' : '#2a3a30',
            border: `1px solid ${propType === pt ? '#00e676' : '#1a2520'}`,
            boxShadow: propType === pt ? '0 0 20px #00e67630' : 'none',
            transition: 'all .15s',
          }}>{pt}</button>
        ))}
      </div>

      {/* Prop line */}
      {section('Prop Line')}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 0, background: '#0a0f0c', border: '1px solid #1a2520', borderRadius: 12, padding: '8px 16px' }}>
          <button onClick={() => setPropLine(l => Math.max(0, +(l - 0.5).toFixed(1)))} style={{
            width: 40, height: 40, borderRadius: 8, border: '1px solid #1a2520',
            background: '#111a14', color: '#3a5045', cursor: 'pointer', fontSize: 22,
            fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 900,
            transition: 'all .15s', display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>−</button>
          <div style={{
            fontSize: 64, fontWeight: 900, minWidth: 120, textAlign: 'center',
            fontFamily: '"Barlow Condensed", sans-serif', lineHeight: 1, color: '#fff',
            padding: '0 16px',
          }}>{propLine.toFixed(1)}</div>
          <button onClick={() => setPropLine(l => +(l + 0.5).toFixed(1))} style={{
            width: 40, height: 40, borderRadius: 8, border: '1px solid #1a2520',
            background: '#111a14', color: '#3a5045', cursor: 'pointer', fontSize: 22,
            fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 900,
            transition: 'all .15s', display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>+</button>
        </div>
        <input type="number" value={propLine} step={0.5} min={0}
          onChange={e => setPropLine(Math.max(0, parseFloat(e.target.value) || 0))}
          style={{ width: 80, padding: '10px 12px', background: '#0a0f0c', border: '1px solid #1a2520', borderRadius: 10, color: '#4a6a50', fontSize: 14 }}
        />
      </div>

      {/* Match Format badge — auto-set for ATP Grand Slams, display only */}
      {propType === 'Break Points Won' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
          {['Best of 3', 'Best of 5'].map(fmt => {
            const isGS = court !== 'None' && [
              'Australian Open','US Open','Roland Garros','Wimbledon',
            ].includes(court)
            const activeFormat = isGS && tour === 'ATP' ? 'Best of 5' : 'Best of 3'
            const isActive = fmt === activeFormat
            return (
              <div key={fmt} style={{
                padding: '5px 14px', borderRadius: 8,
                fontFamily: '"Barlow Condensed", sans-serif',
                fontWeight: 800, fontSize: 11, letterSpacing: 1.5,
                textTransform: 'uppercase',
                background: isActive ? (fmt === 'Best of 5' ? '#001a40' : '#0a0f0c') : 'transparent',
                color: isActive ? (fmt === 'Best of 5' ? '#6b9fff' : '#4a6a50') : '#1a2520',
                border: `1px solid ${isActive ? (fmt === 'Best of 5' ? '#2a3d5a' : '#1a2520') : '#0d1510'}`,
              }}>{fmt}{isActive && fmt === 'Best of 5' && <span style={{ marginLeft: 6, fontSize: 9, color: '#6b9fff' }}>AUTO</span>}</div>
            )
          })}
          <span style={{ fontSize: 10, color: '#1a3a25', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 600 }}>
            ATP Grand Slams auto-set to BO5 · affects projection scale
          </span>
        </div>
      )}

      {/* Run button — Phase 9: motion button with scale effects */}
      <motion.button
        whileHover={p1 && p2 && !loading ? { scale: 1.01 } : {}}
        whileTap={p1 && p2 && !loading ? { scale: 0.98 } : {}}
        onClick={run}
        disabled={!p1 || !p2 || loading}
        style={{
          width: '100%', padding: '16px 24px', borderRadius: 12, fontSize: 14,
          fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, letterSpacing: 2,
          textTransform: 'uppercase', cursor: p1 && p2 && !loading ? 'pointer' : 'not-allowed',
          background: p1 && p2 && !loading ? '#00e676' : '#0a0f0c',
          color: p1 && p2 && !loading ? '#000' : '#1a2520',
          border: `1px solid ${p1 && p2 && !loading ? '#00e676' : '#1a2520'}`,
          boxShadow: p1 && p2 && !loading ? '0 0 30px #00e67625' : 'none',
          marginBottom: 24,
          transition: 'all .15s',
        }}
      >
        {loading ? (
          <motion.span
            animate={{ opacity: [1, 0.4, 1] }}
            transition={{ duration: 0.8, repeat: Infinity }}
          >
            Analyzing…
          </motion.span>
        ) : 'Run Prop Estimate'}
      </motion.button>

      {/* Results */}
      {loading && <LoadingSpinner message="Analyzing matchup…" />}
      {error   && <div style={{ color: 'var(--red)', padding: 16, background: '#FF444411', borderRadius: 8 }}>Error: {error}</div>}

      {/* Phase 2: stagger variants */}
      <AnimatePresence>
        {result && !loading && (
            <motion.div key="results" variants={ANIMATION_CONTAINER} initial="hidden" animate="show" exit={{ opacity: 0 }}>

              {/* ── Projection cards ── */}
              <motion.div variants={ANIMATION_ITEM}>
                <SectionDivider label="PROJECTION" />

                {/* Null-projection banner */}
                {!hasProjection && result.note && (
                  <div style={{ padding: '12px 16px', background: '#FF440011', border: '1px solid #FF440033', borderRadius: 8, marginBottom: 16, fontSize: 13 }}>
                    <span style={{ color: 'var(--amber)', fontWeight: 700 }}>Insufficient Data — </span>
                    <span style={{ color: 'var(--muted)' }}>{result.note}</span>
                  </div>
                )}

                {/* Sanity-failure / tour-average fallback warning */}
                {hasProjection && (result.sanity_failed || result.used_opp_tour_avg) && (
                  <div style={{ padding: '10px 14px', background: '#FFB30011', border: '1px solid #FFB30044', borderRadius: 8, marginBottom: 14, fontSize: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ color: '#FFB300', fontWeight: 800, fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 1 }}>⚠ LIMITED DATA</span>
                    <span style={{ color: '#6a5a30' }}>
                      {result.sanity_failed
                        ? 'Projection outside normal bounds — confidence reduced. '
                        : ''}
                      {result.used_opp_tour_avg
                        ? `Opponent has limited surface data — tour average used for BP faced per match.`
                        : ''}
                    </span>
                  </div>
                )}

                {/* Three static projection cards */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 20 }}>

                  {/* Model Projection */}
                  <div style={{ ...STATIC_CARD_STYLE, borderTop: `2px solid ${result.sanity_failed ? '#FFB300' : '#00e676'}` }}>
                    <div style={STATIC_LABEL_STYLE}>Model Projection</div>
                    {hasProjection ? (
                      <>
                        <div style={{
                          fontSize: 64, fontWeight: 900,
                          color: result.sanity_failed ? '#FFB300' : '#00e676',
                          lineHeight: 1,
                          fontFamily: '"Barlow Condensed", sans-serif',
                        }}>
                          <NumberFlow value={result.model_projection} format={{ minimumFractionDigits: 1, maximumFractionDigits: 1 }} />
                        </div>
                        <div style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 10, letterSpacing: 2, color: '#1a3a25', textTransform: 'uppercase', marginTop: 8 }}>{p1?.name}</div>
                      </>
                    ) : (
                      <div style={{ fontSize: 14, color: 'var(--muted)', marginTop: 8 }}>N/A</div>
                    )}
                  </div>

                  {/* Book Line */}
                  <div style={{ ...STATIC_CARD_STYLE, borderTop: `2px solid ${bookLineBorderColor}` }}>
                    <div style={STATIC_LABEL_STYLE}>Book Line</div>
                    <div style={{
                      fontSize: 64, fontWeight: 900, color: '#3a5040', lineHeight: 1,
                      fontFamily: '"Barlow Condensed", sans-serif',
                    }}>
                      {propLine > 0 ? propLine.toFixed(1) : '—'}
                    </div>
                    {edge != null && (
                      <div style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 13, letterSpacing: 1, marginTop: 8, color: edge >= 0 ? '#00e676' : '#ff4444' }}>
                        edge <NumberFlow value={edge} format={{ minimumFractionDigits: 1, signDisplay: 'always' }} />
                      </div>
                    )}
                  </div>

                  {/* Lean */}
                  <div style={{ ...STATIC_CARD_STYLE, borderTop: '2px solid #ffb300' }}>
                    <div style={STATIC_LABEL_STYLE}>Lean</div>
                    {hasProjection ? (
                      <>
                        <div style={{ margin: '8px 0' }}>
                          <LeanBadge lean={result.lean} />
                        </div>
                        <ConfidenceGauge confidence={result.confidence || 0} />
                      </>
                    ) : (
                      <div style={{ fontSize: 14, color: 'var(--muted)', marginTop: 8 }}>N/A</div>
                    )}
                  </div>
                </div>
              </motion.div>

              {/* ── Last 5 Matches bar chart (any surface) ── */}
              {statKey && (
                <motion.div variants={ANIMATION_ITEM}>
                  {section(`Last 5 Matches — ${propType} (${p1?.name})`)}
                  <div style={{ background: '#0a0f0c', border: '1px solid #1a2520', borderRadius: 12, padding: '18px 18px 10px', marginBottom: 20 }}>
                    {propLine > 0 && (
                      <div style={{ display: 'flex', gap: 16, marginBottom: 12, fontSize: 11 }}>
                        <span style={{ color: 'var(--green)' }}>■ Over {propLine.toFixed(1)}</span>
                        <span style={{ color: 'var(--red)' }}>■ Under {propLine.toFixed(1)}</span>
                        <span style={{ color: '#555' }}>■ Push</span>
                      </div>
                    )}
                    <Last5Chart
                      matches={result.sofascore_surface_log || result.player_surface_matches || []}
                      statKey={statKey}
                      propLine={propLine}
                      playerName={p1?.name}
                      surface={surface}
                      chartSource={result.chart_source || 'sofascore'}
                    />
                  </div>
                </motion.div>
              )}

              {/* ── Environment badge — Phase 8: motion pulse dot ── */}
              {result.environment && (
                <motion.div variants={ANIMATION_ITEM} style={{ marginBottom: 16 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '14px 18px', background: '#0a0f0c', border: '1px solid #1a2520', borderRadius: 12 }}>
                    <span style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 9, letterSpacing: 2, textTransform: 'uppercase', color: '#2a3a30' }}>Match Environment</span>
                    <span style={{
                      display: 'inline-flex', alignItems: 'center',
                      padding: '4px 12px', borderRadius: 14, fontSize: 11, fontWeight: 700,
                      background: envColor + '22',
                      color: envColor,
                      border: `1px solid ${envColor}55`,
                      fontFamily: '"Barlow Condensed", sans-serif',
                    }}>
                      <motion.span
                        animate={{ scale: [1, 1.4, 1], opacity: [1, 0.4, 1] }}
                        transition={{ duration: 1.5, repeat: Infinity, ease: 'easeInOut' }}
                        style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: envColor, marginRight: 6 }}
                      />
                      {result.environment_label || result.environment}
                    </span>
                  </div>
                </motion.div>
              )}

              {/* ── Stat comparison ── */}
              {(p1SurfaceStats || p2SurfaceStats) && (
                <motion.div variants={ANIMATION_ITEM}>
                  <SectionDivider label="SURFACE STATS" />

                  {/* HANDEDNESS EDGE badge — amber when a cross-handed matchup exists */}
                  {result?.handedness_edge && (
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: 10,
                      padding: '10px 14px', marginBottom: 12,
                      background: '#FFB30011', border: '1px solid #FFB30044', borderRadius: 8,
                      fontSize: 12,
                    }}>
                      <span style={{ color: 'var(--amber)', fontWeight: 800, fontFamily: '"Barlow Condensed", sans-serif', fontSize: 12, letterSpacing: 1, textTransform: 'uppercase' }}>⚡ Handedness Edge</span>
                      <span style={{ color: '#6a5a30' }}>
                        {p1?.name} ({result.player_handedness}H) vs {p2?.name} ({result.opponent_handedness}H)
                        {' — cross-handed matchup shifts ace angles and serve geometry'}
                      </span>
                    </div>
                  )}

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 20 }}>
                    {[
                      { s: p1SurfaceStats, name: p1?.name, nc: 'var(--white)', hand: result?.player_handedness,
                        taM: result?.player_ta_matches, ssM: result?.player_ss_matches,
                        fallback: result?.player_surface_fallback, aceAgainst: null },
                      { s: p2SurfaceStats, name: p2?.name, nc: 'var(--muted)', hand: result?.opponent_handedness,
                        taM: result?.opponent_ta_matches, ssM: result?.opponent_ss_matches,
                        fallback: result?.opponent_surface_fallback, aceAgainst: result?.opponent_ace_against },
                    ].map(({ s, name, nc, hand, taM, ssM, fallback, aceAgainst }, idx) => {
                      const d = s || {}
                      // ── BP-specific bidirectional stats (Step 8) ──────────
                      const isBPProp = propType === 'Break Points Won'
                      let rows
                      if (isBPProp && idx === 0) {
                        // Selected player — show returner view
                        rows = [
                          ['BP Conv Rate', result?.conv_rate_pct != null ? `${result.conv_rate_pct.toFixed(0)}%` : fmtPct(d.bp_converted)],
                          ['BP Opps Created/Match', result?.player_bp_opps_per_match != null ? fmt(result.player_bp_opps_per_match) : '—'],
                          ['BP Won/Match (est)', result?.player_bp_won_per_match != null ? fmt(result.player_bp_won_per_match) : '—'],
                          ['Ret Pts Won (1st)', fmtPct(d.return_first_serve_pts_won)],
                          ['Ret Pts Won (2nd)', fmtPct(d.return_second_serve_pts_won)],
                          ['Win Rate', fmtPct(d.win_rate)],
                          ['Matches', d.matches_played || '—'],
                        ]
                      } else if (isBPProp && idx === 1) {
                        // Opponent — show server view
                        const serveTier = result?.opp_serve_tier
                        const serveTierColor = { Elite: '#ff4444', Good: '#f5a623', Weak: '#00e676' }[serveTier] || '#4a6a50'
                        rows = [
                          ['BP Faced/Match', result?.opp_bp_faced != null ? fmt(result.opp_bp_faced) : fmt(d.bp_faced_count)],
                          ['Hold Rate (est)', result?.opp_hold_rate_pct != null ? `${result.opp_hold_rate_pct.toFixed(0)}%` : '—'],
                          ['Serve Quality', serveTier
                            ? <span style={{ color: serveTierColor, fontWeight: 800 }}>{serveTier}</span>
                            : '—'],
                          ['1st Srv Won', fmtPct(d.first_serve_pts_won)],
                          ['2nd Srv Won', fmtPct(d.second_serve_pts_won)],
                          ['BP Saved', fmtPct(d.bp_saved)],
                          ['Matches', d.matches_played || '—'],
                        ]
                      } else {
                        rows = [
                          ['Aces/Match', fmt(d.aces)],
                          ['DFs/Match', fmt(d.double_faults)],
                          ['1st Srv Won', fmtPct(d.first_serve_pts_won)],
                          ['2nd Srv Won', fmtPct(d.second_serve_pts_won)],
                          ['Ret Pts Won (1st)', fmtPct(d.return_first_serve_pts_won)],
                          ['BP Converted', fmtPct(d.bp_converted)],
                          ['Win Rate', fmtPct(d.win_rate)],
                          ['Matches', d.matches_played || '—'],
                        ]
                        if (idx === 1 && aceAgainst != null) {
                          rows.splice(1, 0, ['Aces Conceded/Match', fmt(aceAgainst)])
                        }
                      }
                      return (
                        <div key={idx} style={{ background: '#0a0f0c', border: '1px solid #1a2520', borderRadius: 12, padding: '18px 20px' }}>
                          <div style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, fontSize: 16, color: nc, marginBottom: 6, display: 'flex', alignItems: 'center' }}>
                            {name}<HandBadge hand={hand} />
                          </div>
                          {/* Data source indicators */}
                          <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
                            {taM != null && (
                              <span style={{ fontSize: 9, fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, letterSpacing: 1, padding: '2px 6px', borderRadius: 4, background: '#0d1a10', color: '#2a4a30', border: '1px solid #1a2a1e' }}>
                                TA {taM} career
                              </span>
                            )}
                            {ssM != null && (
                              <span style={{ fontSize: 9, fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, letterSpacing: 1, padding: '2px 6px', borderRadius: 4, background: '#0d1a10', color: '#2a4a30', border: '1px solid #1a2a1e' }}>
                                SS {ssM} recent
                              </span>
                            )}
                            {fallback && (
                              <span style={{ fontSize: 9, fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, letterSpacing: 1, padding: '2px 6px', borderRadius: 4, background: '#1a0f00', color: '#FFB300', border: '1px solid #3a2800' }}>
                                All-surface avg
                              </span>
                            )}
                          </div>
                          {fallback && (
                            <div style={{ fontSize: 10, color: '#6a5020', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 600, marginBottom: 8, letterSpacing: 0.5 }}>
                              ⚠ Limited surface data — using all-surface average
                            </div>
                          )}
                          {rows.map(([lbl, val]) => {
                            const isNode = val != null && typeof val === 'object' && val.$$typeof
                            return (
                              <div key={lbl} style={{ display: 'flex', justifyContent: 'space-between', padding: '7px 0', borderBottom: '1px solid #0d1510', fontSize: 12 }}>
                                <span style={{ color: '#2a3a30', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 600, letterSpacing: 0.5 }}>{lbl}</span>
                                {isNode
                                  ? <span style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 14 }}>{val}</span>
                                  : <span style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 14, color: statColor(lbl, val) || '#4a6a50' }}>{val}</span>
                                }
                              </div>
                            )
                          })}
                        </div>
                      )
                    })}
                  </div>
                </motion.div>
              )}

              {/* ── H2H context ── */}
              {result.h2h_context && (result.h2h_context.total > 0 || result.h2h_context.total === 0) && (
                <motion.div variants={ANIMATION_ITEM}>
                  {section(`H2H Context`)}
                  <div style={{ background: '#0a0f0c', border: '1px solid #1a2520', borderRadius: 12, padding: '18px 20px', marginBottom: 16 }}>
                    {result.h2h_context.total === 0 ? (
                      <div style={{ fontSize: 13, color: 'var(--muted)' }}>No H2H data available</div>
                    ) : (
                      <>
                        <div style={{ display: 'flex', gap: 24, alignItems: 'center', marginBottom: 12 }}>
                          <div style={{ textAlign: 'center' }}>
                            <div style={{ fontSize: 11, color: 'var(--muted)' }}>{p1?.name}</div>
                            <div style={{ fontSize: 44, fontWeight: 900, color: '#00e676', fontFamily: '"Barlow Condensed", sans-serif' }}>{result.h2h_context.p1_wins}</div>
                          </div>
                          <div style={{ fontSize: 20, color: 'var(--border)' }}>—</div>
                          <div style={{ textAlign: 'center' }}>
                            <div style={{ fontSize: 11, color: 'var(--muted)' }}>{p2?.name}</div>
                            <div style={{ fontSize: 44, fontWeight: 900, color: '#ff4444', fontFamily: '"Barlow Condensed", sans-serif' }}>{result.h2h_context.p2_wins}</div>
                          </div>
                          <div style={{ fontSize: 12, color: 'var(--muted)', marginLeft: 8 }}>
                            <div>{result.h2h_context.total} {result.h2h_context.total !== 1 ? 'meetings' : 'meeting'}</div>
                            {result.h2h_context.surface_matches > 0 && (
                              <div style={{ marginTop: 2 }}>{result.h2h_context.surface_matches} on {surface}</div>
                            )}
                            {result.h2h_context.date_range && (
                              <div style={{ marginTop: 2, fontSize: 11 }}>{result.h2h_context.date_range}</div>
                            )}
                            {result.h2h_context.surface_breakdown &&
                              Object.keys(result.h2h_context.surface_breakdown).length > 0 &&
                              result.h2h_context.total >= 3 && (
                              <div style={{ marginTop: 4, fontSize: 11 }}>
                                {Object.entries(result.h2h_context.surface_breakdown).map(([s, n]) => `${n} ${s}`).join(' · ')}
                              </div>
                            )}
                          </div>
                        </div>
                        {result.h2h_context.ace_avg != null && (
                          <div style={{ fontSize: 12, color: 'var(--muted)' }}>Avg aces by {p1?.name} in H2H: {fmt(result.h2h_context.ace_avg)}</div>
                        )}
                      </>
                    )}
                  </div>
                </motion.div>
              )}

              {/* ── Model explanation ── */}
              {result.plain_english_explanation && (
                <motion.div variants={ANIMATION_ITEM}>
                  <SectionDivider label="MODEL EXPLANATION" />
                  <div style={{
                    borderLeft: '3px solid #00e676',
                    borderRadius: '0 10px 10px 0',
                    background: '#080d09',
                    padding: '18px 20px',
                    marginBottom: 16,
                  }}>
                    <div style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 9, letterSpacing: '0.3em', textTransform: 'uppercase', color: '#00e676', marginBottom: 10 }}>
                      Model Logic
                    </div>
                    <p style={{ fontSize: 13, color: '#3a5040', lineHeight: 1.8, margin: 0 }}>
                      {result.plain_english_explanation}
                    </p>
                  </div>
                </motion.div>
              )}

              {/* ── AI scouting report ── */}
              {result.ai_writeup && (
                <motion.div variants={ANIMATION_ITEM}>
                  <SectionDivider label="AI SCOUTING REPORT" />
                  <div style={{ background: '#080d09', border: '1px solid #1a2520', borderRadius: 12, padding: '22px 24px', position: 'relative', marginBottom: 16 }}>
                    <span style={{
                      position: 'absolute', top: 14, right: 16,
                      fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 900, fontSize: 8, letterSpacing: '0.2em',
                      textTransform: 'uppercase', color: '#00e676', background: '#001a0b',
                      border: '1px solid #00e676', padding: '3px 10px', borderRadius: 3,
                    }}>BASELINE AI</span>
                    <div style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 9, letterSpacing: '0.3em', textTransform: 'uppercase', color: '#1a3a25', marginBottom: 14 }}>
                      AI Scouting Report
                    </div>
                    <p style={{ fontSize: 14, color: '#5a7a68', lineHeight: 1.85, margin: 0, paddingRight: 60 }}>
                      {result.ai_writeup}
                    </p>
                  </div>
                </motion.div>
              )}

              {/* ── Confidence breakdown ── */}
              <motion.div variants={ANIMATION_ITEM}>
                <ConfidenceBreakdown breakdown={result.confidence_breakdown} />
              </motion.div>

            </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
