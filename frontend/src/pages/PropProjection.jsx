import { useState, useCallback, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
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
import { COURTS_BY_SURFACE, fmt, fmtPct } from '../utils/constants'

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
  background: 'var(--card)',
  border: '1px solid var(--border)',
  borderRadius: 10,
  padding: '16px 18px',
}
const STATIC_LABEL_STYLE = {
  fontSize: 11, color: 'var(--muted)',
  textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 6,
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
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '8px 0' }}>
      <span style={{
        fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700,
        fontSize: 10, letterSpacing: '0.2em', textTransform: 'uppercase',
        color: '#2a3540', whiteSpace: 'nowrap',
      }}>{label}</span>
      <div style={{ flex: 1, height: 1, background: '#1a1f26' }} />
    </div>
  )
}

// ── Last 5 Matches Bar Chart ────────────────────────────────────────────────
function Last5Chart({ matches, statKey, propLine, playerName }) {
  if (!matches || !statKey) return null

  const last5 = matches.slice(0, 5)
  if (!last5.length) return (
    <div style={{ color: 'var(--muted)', fontSize: 13, padding: '16px 0' }}>
      No recent {statKey} data on this surface
    </div>
  )

  // For Total Games: fall back to parsing the score string when total_match_games is absent
  const resolveVal = (m) => {
    let v = m[statKey] ?? null
    if (v == null && statKey === 'total_match_games' && m.score) {
      // e.g. "6-3 6-4" → (6+3) + (6+4) = 19
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

  const data = last5.map(m => {
    const val = resolveVal(m)
    let fill = '#555'
    if (val != null && propLine > 0) {
      if (val > propLine) fill = 'var(--green)'
      else if (val < propLine) fill = 'var(--red)'
      // exactly equal → gray #555
    }
    // Format date: "2025-04-28" → "Apr 28"
    const raw = m.date || m.match_date || ''
    let label = raw
    try {
      const d = new Date(raw)
      if (!isNaN(d)) label = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    } catch {}
    return { label, val: val != null ? Math.round(val * 10) / 10 : null, fill }
  }).filter(d => d.val != null)

  if (!data.length) return (
    <div style={{ color: 'var(--muted)', fontSize: 13, padding: '16px 0' }}>
      No match stat data available for this surface
    </div>
  )

  // Custom bar with colored fill per entry
  const CustomBar = (props) => {
    const { x, y, width, height, index } = props
    return <rect x={x} y={y} width={width} height={height} fill={data[index]?.fill || '#555'} rx={3} />
  }

  // Custom label above bar
  const CustomLabel = (props) => {
    const { x, y, width, value } = props
    return (
      <text x={x + width / 2} y={y - 5} textAnchor="middle" fill="var(--white)" fontSize={11} fontWeight={700}>
        {value}
      </text>
    )
  }

  return (
    <div style={{ height: 160 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} barSize={40} margin={{ top: 20, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e1e1e" vertical={false} />
          <XAxis dataKey="label" tick={{ fill: 'var(--muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
          <YAxis tick={{ fill: 'var(--muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
          <Tooltip
            contentStyle={{ background: '#1a1a1a', border: '1px solid var(--border)', borderRadius: 6 }}
            formatter={(v) => [v, playerName]}
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
  return (
    <div style={{ display: 'flex', gap: 8 }}>
      {SURFACES.map(s => {
        const isActive = value === s
        const dotColor = SURFACE_DOT_COLORS[s]
        return (
          <button key={s} onClick={() => onChange(s)} style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '8px 14px', borderRadius: 8, cursor: 'pointer', fontSize: 13, fontWeight: 600,
            background: isActive ? 'var(--card)' : 'transparent',
            color: isActive ? 'var(--white)' : 'var(--muted)',
            border: isActive ? `1px solid ${dotColor}` : '1px solid var(--border)',
            transition: 'all .15s',
          }}>
            <span style={{
              width: 8, height: 8, borderRadius: '50%',
              background: dotColor,
              display: 'inline-block',
              flexShrink: 0,
            }} />
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

  const courts = ['None', ...(COURTS_BY_SURFACE[surface] || [])]

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
    <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.08em', marginBottom: 12, marginTop: 24, paddingBottom: 6, borderBottom: '1px solid var(--border)' }}>
      {title}
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
            width: '100%', padding: '10px 12px', background: 'var(--card)',
            border: '1px solid var(--border)', borderRadius: 8, color: 'var(--white)', fontSize: 14,
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
            padding: '8px 16px', borderRadius: 20, cursor: 'pointer',
            fontSize: 13,
            fontFamily: '"Barlow Condensed", sans-serif',
            fontWeight: propType === pt ? 800 : 600,
            background: propType === pt ? 'var(--green)' : 'var(--card)',
            color: propType === pt ? '#000' : 'var(--muted)',
            border: `1px solid ${propType === pt ? 'var(--green)' : 'var(--border)'}`,
          }}>{pt}</button>
        ))}
      </div>

      {/* Prop line */}
      {section('Prop Line')}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <button onClick={() => setPropLine(l => Math.max(0, +(l - 0.5).toFixed(1)))} style={{
          width: 36, height: 36, borderRadius: 8, border: '1px solid var(--border)',
          background: 'var(--card)', color: 'var(--white)', cursor: 'pointer', fontSize: 18,
        }}>−</button>
        <div style={{
          fontSize: 28, fontWeight: 800, minWidth: 60, textAlign: 'center',
          fontFamily: '"Barlow Condensed", sans-serif',
        }}>{propLine.toFixed(1)}</div>
        <button onClick={() => setPropLine(l => +(l + 0.5).toFixed(1))} style={{
          width: 36, height: 36, borderRadius: 8, border: '1px solid var(--border)',
          background: 'var(--card)', color: 'var(--white)', cursor: 'pointer', fontSize: 18,
        }}>+</button>
        <input type="number" value={propLine} step={0.5} min={0}
          onChange={e => setPropLine(Math.max(0, parseFloat(e.target.value) || 0))}
          style={{ width: 80, padding: '8px 12px', background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--white)', fontSize: 14 }}
        />
      </div>

      {/* Run button */}
      <button onClick={run} disabled={!p1 || !p2 || loading} style={{
        width: '100%', padding: '14px 24px', borderRadius: 10, fontSize: 15, fontWeight: 700,
        cursor: p1 && p2 && !loading ? 'pointer' : 'not-allowed',
        background: p1 && p2 && !loading ? 'var(--green)' : '#1a1a1a',
        color: p1 && p2 && !loading ? '#000' : 'var(--muted)',
        border: 'none', transition: 'all .2s',
        marginBottom: 24,
        fontFamily: '"Barlow Condensed", sans-serif',
      }}>
        {loading ? 'Analyzing…' : 'Run Prop Estimate'}
      </button>

      {/* Results */}
      {loading && <LoadingSpinner message="Analyzing matchup…" />}
      {error   && <div style={{ color: 'var(--red)', padding: 16, background: '#FF444411', borderRadius: 8 }}>Error: {error}</div>}

      {result && !loading && (
        <AnimatePresence>
          <motion.div key="results" initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>

            {/* ── Change 8: Section divider before projection ── */}
            <SectionDivider label="PROJECTION" />

            {/* Null-projection banner — shown when backend has insufficient data */}
            {!hasProjection && result.note && (
              <div style={{ padding: '12px 16px', background: '#FF440011', border: '1px solid #FF440033', borderRadius: 8, marginBottom: 16, fontSize: 13 }}>
                <span style={{ color: 'var(--amber)', fontWeight: 700 }}>Insufficient Data — </span>
                <span style={{ color: 'var(--muted)' }}>{result.note}</span>
              </div>
            )}

            {/* Three static (non-interactive) projection cards */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 20 }}>

              {/* ── Change 2: Model Projection — green top border ── */}
              <div style={{ ...STATIC_CARD_STYLE, borderTop: '2px solid #00e676' }}>
                <div style={STATIC_LABEL_STYLE}>Model Projection</div>
                {hasProjection ? (
                  <>
                    <div style={{
                      fontSize: 36, fontWeight: 900, color: 'var(--green)', lineHeight: 1,
                      fontFamily: '"Barlow Condensed", sans-serif',
                    }}>
                      {result.model_projection.toFixed(1)}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>{p1?.name}</div>
                  </>
                ) : (
                  <div style={{ fontSize: 14, color: 'var(--muted)', marginTop: 8 }}>N/A</div>
                )}
              </div>

              {/* ── Change 2: Book Line — edge-based top border ── */}
              <div style={{ ...STATIC_CARD_STYLE, borderTop: `2px solid ${bookLineBorderColor}` }}>
                <div style={STATIC_LABEL_STYLE}>Book Line</div>
                <div style={{
                  fontSize: 36, fontWeight: 900, color: 'var(--muted)', lineHeight: 1,
                  fontFamily: '"Barlow Condensed", sans-serif',
                }}>
                  {propLine > 0 ? propLine.toFixed(1) : '—'}
                </div>
                {edge != null && (
                  <div style={{ fontSize: 13, color: edge >= 0 ? 'var(--green)' : 'var(--red)', marginTop: 4 }}>
                    edge {edge >= 0 ? '+' : ''}{edge.toFixed(1)}
                  </div>
                )}
              </div>

              {/* ── Change 2: Lean — amber top border ── */}
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

            {/* ── ISSUE 1: Last 5 Matches bar chart ── */}
            {result.player_surface_matches?.length > 0 && statKey && (
              <>
                {section(`Last 5 ${surface} Matches — ${propType} (${p1?.name})`)}
                <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 10, padding: '16px 16px 8px', marginBottom: 20 }}>
                  {propLine > 0 && (
                    <div style={{ display: 'flex', gap: 16, marginBottom: 12, fontSize: 11 }}>
                      <span style={{ color: 'var(--green)' }}>■ Over {propLine.toFixed(1)}</span>
                      <span style={{ color: 'var(--red)' }}>■ Under {propLine.toFixed(1)}</span>
                      <span style={{ color: '#555' }}>■ Push</span>
                    </div>
                  )}
                  <Last5Chart
                    matches={result.player_surface_matches}
                    statKey={statKey}
                    propLine={propLine}
                    playerName={p1?.name}
                  />
                </div>
              </>
            )}

            {/* Environment badge */}
            {result.environment && (
              <div style={{ marginBottom: 16 }}>
                {/* ── Change 11: Pulse keyframes injected via style tag ── */}
                <style>{`@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }`}</style>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '14px 16px', background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 10 }}>
                  <span style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.06em' }}>Match Environment</span>
                  <span style={{
                    display: 'inline-flex', alignItems: 'center',
                    padding: '4px 12px', borderRadius: 14, fontSize: 11, fontWeight: 700,
                    background: (ENV_COLORS[result.environment] || '#888') + '22',
                    color: ENV_COLORS[result.environment] || '#888',
                    border: `1px solid ${(ENV_COLORS[result.environment] || '#888')}55`,
                    fontFamily: '"Barlow Condensed", sans-serif',
                  }}>
                    {/* ── Change 11: Pulsing dot ── */}
                    <span style={{
                      display: 'inline-block', width: 6, height: 6,
                      borderRadius: '50%', background: '#ffb300',
                      animation: 'pulse 2s infinite', marginRight: 8,
                    }} />
                    {result.environment_label || result.environment}
                  </span>
                </div>
              </div>
            )}

            {/* Stat comparison — uses result payload if present, prefetched stats as fallback */}
            {(p1SurfaceStats || p2SurfaceStats) && (
              <>
                {/* ── Change 8: Section divider before stats ── */}
                <SectionDivider label="SURFACE STATS" />

                {/* Handedness edge indicator — shown when matchup is cross-handed */}
                {result?.player_handedness && result?.opponent_handedness &&
                  result.player_handedness !== result.opponent_handedness && (
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 14px', marginBottom: 12,
                    background: '#00E67608', border: '1px solid #00E67622', borderRadius: 8,
                    fontSize: 12,
                  }}>
                    <span style={{ color: 'var(--green)', fontWeight: 700 }}>⚡ Handedness Edge</span>
                    <span style={{ color: 'var(--muted)' }}>
                      {p1?.name} ({result.player_handedness}) vs {p2?.name} ({result.opponent_handedness})
                      {' — cross-handed matchup affects ace angles and serve patterns'}
                    </span>
                  </div>
                )}

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 20 }}>
                  {[
                    [p1SurfaceStats, p1?.name, 'var(--white)', result?.player_handedness, null],
                    [p2SurfaceStats, p2?.name, 'var(--muted)', result?.opponent_handedness, result?.opponent_ace_against],
                  ].map(([s, name, nc, hand, aceAgainst], idx) => {
                    const d = s || {}
                    const rows = [
                      ['Aces/Match', fmt(d.aces)],
                      ['DFs/Match', fmt(d.double_faults)],
                      ['1st Srv Won', fmtPct(d.first_serve_pts_won)],
                      ['2nd Srv Won', fmtPct(d.second_serve_pts_won)],
                      ['Ret Pts Won (1st)', fmtPct(d.return_first_serve_pts_won)],
                      ['BP Converted', fmtPct(d.bp_converted)],
                      ['Win Rate', fmtPct(d.win_rate)],
                      ['Matches', d.matches_played || '—'],
                    ]
                    // Add aces-conceded row to opponent card (idx === 1) if TA data available
                    if (idx === 1 && aceAgainst != null) {
                      rows.splice(1, 0, ['Aces Conceded/Match', fmt(aceAgainst)])
                    }
                    return (
                      <div key={idx} style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 10, padding: 16 }}>
                        <div style={{ fontWeight: 700, color: nc, marginBottom: 12, fontSize: 13, display: 'flex', alignItems: 'center' }}>
                          🎾 {name}<HandBadge hand={hand} />
                        </div>
                        {/* ── Change 3: color-coded stat values ── */}
                        {rows.map(([lbl, val]) => (
                          <div key={lbl} style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', borderBottom: '1px solid #151515', fontSize: 12 }}>
                            <span style={{ color: 'var(--muted)' }}>{lbl}</span>
                            <span style={{ fontWeight: 600, color: statColor(lbl, val) || 'var(--white)' }}>{val}</span>
                          </div>
                        ))}
                      </div>
                    )
                  })}
                </div>
              </>
            )}

            {/* H2H context */}
            {result.h2h_context?.total > 0 && (
              <>
                {section(`H2H Context`)}
                <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 10, padding: 16, marginBottom: 16 }}>
                  <div style={{ display: 'flex', gap: 24, alignItems: 'center', marginBottom: 12 }}>
                    <div style={{ textAlign: 'center' }}>
                      <div style={{ fontSize: 11, color: 'var(--muted)' }}>{p1?.name}</div>
                      <div style={{ fontSize: 36, fontWeight: 900, color: 'var(--green)', fontFamily: '"Barlow Condensed", sans-serif' }}>{result.h2h_context.p1_wins}</div>
                    </div>
                    <div style={{ fontSize: 20, color: 'var(--border)' }}>–</div>
                    <div style={{ textAlign: 'center' }}>
                      <div style={{ fontSize: 11, color: 'var(--muted)' }}>{p2?.name}</div>
                      <div style={{ fontSize: 36, fontWeight: 900, color: 'var(--muted)', fontFamily: '"Barlow Condensed", sans-serif' }}>{result.h2h_context.p2_wins}</div>
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--muted)', marginLeft: 8 }}>
                      {result.h2h_context.total} meetings
                      {result.h2h_context.surface_matches > 0 && ` · ${result.h2h_context.surface_matches} on ${surface}`}
                    </div>
                  </div>
                  {result.h2h_context.ace_avg != null && (
                    <div style={{ fontSize: 12, color: 'var(--muted)' }}>Avg aces by {p1?.name} in H2H: {fmt(result.h2h_context.ace_avg)}</div>
                  )}
                </div>
              </>
            )}

            {/* Explanation */}
            {result.plain_english_explanation && (
              <>
                {/* ── Change 8: Section divider before model explanation ── */}
                <SectionDivider label="MODEL EXPLANATION" />
                {/* ── Change 6: Green left border styling ── */}
                <div style={{
                  borderLeft: '4px solid #00e676',
                  borderRadius: '0 8px 8px 0',
                  background: '#0d1117',
                  padding: '16px',
                  fontSize: 13,
                  color: '#667788',
                  lineHeight: 1.6,
                  marginBottom: 16,
                }}>
                  {result.plain_english_explanation}
                </div>
              </>
            )}

            {/* AI writeup */}
            {result.ai_writeup && (
              <>
                {/* ── Change 8: Section divider before AI report ── */}
                <SectionDivider label="AI SCOUTING REPORT" />
                {/* ── Change 7: BASELINE AI badge ── */}
                <div style={{ padding: '16px', background: '#00E67608', border: '1px solid #00E67622', borderRadius: 10, marginBottom: 16, position: 'relative' }}>
                  <span style={{
                    position: 'absolute', top: 8, right: 12,
                    color: '#00e676', background: '#001a0b',
                    border: '1px solid #00e676',
                    fontSize: 8, fontFamily: '"Barlow Condensed", sans-serif',
                    fontWeight: 800, letterSpacing: '0.15em',
                    textTransform: 'uppercase', padding: '2px 8px',
                    borderRadius: 4,
                  }}>BASELINE AI</span>
                  <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.7 }}>{result.ai_writeup}</div>
                </div>
              </>
            )}

            {/* Confidence breakdown */}
            <ConfidenceBreakdown breakdown={result.confidence_breakdown} />

          </motion.div>
        </AnimatePresence>
      )}
    </div>
  )
}
