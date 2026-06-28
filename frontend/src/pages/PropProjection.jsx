import { useState, useCallback, useEffect, useMemo } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import NumberFlow from '@number-flow/react'
import {
  ScatterChart, Scatter, XAxis, YAxis, Tooltip, ResponsiveContainer,
  BarChart, Bar, CartesianGrid, Cell,
} from 'recharts'
import PlayerSearch from '../components/PlayerSearch'
import LoadingSpinner from '../components/LoadingSpinner'
import LeanBadge from '../components/LeanBadge'
import ConfidenceGauge from '../components/ConfidenceGauge'
import EnvironmentBanner from '../components/EnvironmentBanner'
import ExpectedSetsBanner from '../components/ExpectedSetsBanner'
import Last5Bars from '../components/Last5Bars'
import { calcProp, fetchStats } from '../utils/api'
import { TOURNAMENT_CONFIG, fmt, fmtPct, getSpeedTier, ST_YOY_THRESHOLD } from '../utils/constants'

const PROP_TYPES = ['Aces', 'Double Faults', 'Total Games', 'Break Points Won', 'Player Total Games Won']
const SURFACES   = ['Hard', 'Clay', 'Grass']

const PROP_STAT_KEY = {
  'Aces': 'aces',
  'Double Faults': 'double_faults',
  'Total Games': 'total_match_games',
  'Break Points Won': 'bp_converted_count',
  'Player Total Games Won': 'total_games_won',
}

// Animation variants — staggered reveal
const REVEAL_CONTAINER = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.1, delayChildren: 0.05 } },
}
const REVEAL_ITEM = {
  hidden: { opacity: 0, y: 24 },
  show: { opacity: 1, y: 0, transition: { type: 'spring', stiffness: 220, damping: 26 } },
}

// ── Stat color helper ─────────────────────────────────────────────
function statColor(label, value) {
  const v = parseFloat(value)
  if (isNaN(v)) return undefined
  const l = label.toLowerCase()
  if (l.includes('ace')) return v > 6 ? 'var(--green-bright)' : v >= 3 ? 'var(--amber)' : 'var(--red-bright)'
  if (l.includes('double') || l.includes('df')) return v < 1.5 ? 'var(--green-bright)' : v <= 2.5 ? 'var(--amber)' : 'var(--red-bright)'
  if (l.includes('1st serve %') || l.includes('1st in') || l.includes('first in')) return v > 65 ? 'var(--green-bright)' : v >= 55 ? 'var(--amber)' : 'var(--red-bright)'
  if (l.includes('1st') && l.includes('won')) return v > 78 ? 'var(--green-bright)' : v >= 68 ? 'var(--amber)' : 'var(--red-bright)'
  if (l.includes('2nd') && l.includes('won')) return v > 58 ? 'var(--green-bright)' : v >= 50 ? 'var(--amber)' : 'var(--red-bright)'
  if (l.includes('return') || l.includes('rpw')) return v > 42 ? 'var(--green-bright)' : v >= 35 ? 'var(--amber)' : 'var(--red-bright)'
  if (l.includes('bp conv') || (l.includes('break') && l.includes('conv'))) return v > 48 ? 'var(--green-bright)' : v >= 38 ? 'var(--amber)' : 'var(--red-bright)'
  if (l.includes('bp saved') || (l.includes('break') && l.includes('sav'))) return v > 68 ? 'var(--green-bright)' : v >= 58 ? 'var(--amber)' : 'var(--red-bright)'
  if (l.includes('win') || l.includes('win%')) return v > 65 ? 'var(--green-bright)' : v >= 50 ? 'var(--amber)' : 'var(--red-bright)'
  return undefined
}

// Section divider
function SectionDivider({ label }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16, margin: '24px 0 14px' }}>
      <div style={{ flex: 1, height: 1, background: 'linear-gradient(90deg, transparent, rgba(0, 230, 118, 0.2), transparent)' }} />
      <span style={{
        fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800,
        fontSize: 10, letterSpacing: '0.3em', textTransform: 'uppercase',
        color: 'var(--green-mid)', whiteSpace: 'nowrap',
      }}>{label}</span>
      <div style={{ flex: 1, height: 1, background: 'linear-gradient(90deg, transparent, rgba(0, 230, 118, 0.2), transparent)' }} />
    </div>
  )
}

// ── Build Last 5 data from match log ────────────────────────────────────
// Parse a player-first score string ("6-4 7-6") into [{p, o}] per set.
function parseSetScores(score) {
  if (!score) return []
  return score.trim().split(/\s+/).map(s => {
    const [a, b] = s.split('-')
    const p = parseInt(a, 10), o = parseInt(b, 10)   // tiebreak "7-6(5)" → parseInt drops "(5)"
    return (isNaN(p) || isNaN(o)) ? null : { p, o }
  }).filter(Boolean)
}

// Was a match Best of 5? The winner needed 3 sets (Grand Slam main draw).
function isBestOf5(score) {
  const sets = parseSetScores(score)
  if (!sets.length) return null
  let pw = 0, ow = 0
  for (const s of sets) { if (s.p > s.o) pw++; else if (s.o > s.p) ow++ }
  return Math.max(pw, ow) >= 3
}

function buildLast5Data(matches, statKey) {
  if (!matches || !statKey) return []
  const last5 = matches.slice(0, 5).reverse()
  return last5.map(m => {
    let v = m[statKey] ?? null
    if (v == null && m.score) {
      const sets = parseSetScores(m.score)
      if (sets.length) {
        // Combined total games = both players' games; player's games won = the
        // player's (first-listed) games summed across sets.
        if (statKey === 'total_match_games') v = sets.reduce((t, s) => t + s.p + s.o, 0)
        else if (statKey === 'total_games_won') v = sets.reduce((t, s) => t + s.p, 0)
      }
    }
    const isNA = v == null
    const dateStr = m.date || ''
    const opp = m.opponent_abbr || (m.opponent || '').split(' ').pop() || ''
    const label = dateStr && opp ? `${dateStr}\nvs ${opp}` : dateStr || opp || '?'
    return { label, val: isNA ? 0 : Math.round(v * 10) / 10, isNA, won: m.won, bo5: isBestOf5(m.score) }
  })
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
          <Scatter data={data} fill="var(--green-bright)" isAnimationActive />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  )
}

function ConfidenceBreakdown({ breakdown }) {
  const [open, setOpen] = useState(false)
  if (!breakdown) return null

  return (
    <div className="glass-card" style={{ overflow: 'hidden', borderRadius: 12, padding: 0 }}>
      <button onClick={() => setOpen(!open)} style={{
        width: '100%', padding: '14px 18px', background: 'transparent',
        border: 'none', color: 'var(--green-mid)', cursor: 'pointer',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        fontSize: 12, letterSpacing: 2, fontFamily: '"Barlow Condensed", sans-serif',
        fontWeight: 800, textTransform: 'uppercase',
      }}>
        <span>Confidence Breakdown</span>
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={{ duration: 0.25 }}>▼</motion.span>
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25 }}
            style={{ overflow: 'hidden' }}
          >
            <div style={{ padding: '0 18px 18px' }}>
              {Object.entries(breakdown).map(([key, info]) => {
                const score = info.score || 0
                const color = score > 0 ? 'var(--green-bright)' : score < 0 ? 'var(--red-bright)' : 'var(--muted)'
                return (
                  <div key={key} style={{ display: 'flex', justifyContent: 'space-between', padding: '9px 0', borderBottom: '1px solid rgba(13, 21, 16, 0.6)' }}>
                    <div>
                      <div style={{ fontSize: 12, color: 'var(--white)', fontWeight: 700, fontFamily: '"Barlow Condensed", sans-serif' }}>
                        {key.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase())}
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--muted)' }}>{info.label}</div>
                    </div>
                    <div style={{ fontSize: 14, fontWeight: 800, color, fontFamily: '"Barlow Condensed", sans-serif' }}>
                      {score >= 0 ? '+' : ''}{score}/{info.max}
                    </div>
                  </div>
                )
              })}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
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
      fontSize: 9, fontWeight: 800,
      padding: '2px 7px', borderRadius: 999,
      marginLeft: 8, letterSpacing: '.05em', verticalAlign: 'middle',
      background: isLeft ? 'var(--green-bright)' : 'rgba(255,255,255,0.05)',
      color: isLeft ? '#000' : 'var(--muted)',
      border: `1px solid ${isLeft ? 'var(--green-bright)' : 'rgba(255,255,255,0.12)'}`,
    }}>
      {isLeft ? 'L' : 'R'}
    </span>
  )
}

// Surface selector with colored dots
function SurfaceSelector({ value, onChange }) {
  const SURF_STYLES = {
    Hard:  { active: { background: 'linear-gradient(135deg, #001a40, #003070)', color: '#6b9fff', border: '1px solid #2a3d5a', glow: 'rgba(107, 159, 255, 0.3)' }, dot: '#6b9fff' },
    Clay:  { active: { background: 'linear-gradient(135deg, #2a0800, #5a1c00)', color: '#ff6b35', border: '1px solid #5a2010', glow: 'rgba(255, 107, 53, 0.3)' }, dot: '#ff6b35' },
    Grass: { active: { background: 'linear-gradient(135deg, #001a0b, #003a20)', color: '#00e676', border: '1px solid #1a4020', glow: 'rgba(0, 230, 118, 0.3)' }, dot: '#00e676' },
  }
  return (
    <div style={{ display: 'flex', gap: 10 }}>
      {SURFACES.map(s => {
        const isActive = value === s
        const ss = SURF_STYLES[s]
        return (
          <motion.button
            key={s}
            whileTap={{ scale: 0.95 }}
            onClick={() => onChange(s)}
            style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '11px 18px', borderRadius: 12, cursor: 'pointer',
              fontFamily: '"Barlow Condensed", sans-serif',
              fontWeight: 800, fontSize: 13, letterSpacing: 1.8,
              textTransform: 'uppercase',
              transition: 'all .2s',
              ...(isActive
                ? { ...ss.active, boxShadow: `0 0 18px ${ss.active.glow}` }
                : { background: 'rgba(255, 255, 255, 0.025)', color: 'var(--muted)', border: '1px solid var(--card-border)' }
              ),
            }}
          >
            <span style={{
              width: 9, height: 9, borderRadius: '50%',
              background: isActive ? ss.dot : 'rgba(255,255,255,0.1)',
              boxShadow: isActive ? `0 0 8px ${ss.dot}` : 'none',
              display: 'inline-block', flexShrink: 0,
            }} />
            {s}
          </motion.button>
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
  // ATP Grand Slam qualifying rounds are best-of-3 (main draw is best-of-5).
  const [qualifying, setQualifying] = useState(false)
  const [propType, setPropType] = useState('Aces')
  const [propLine, setPropLine] = useState(0)
  const [result,   setResult]   = useState(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState(null)
  const [ranFor,   setRanFor]   = useState(null)
  const [p1PrefetchStats, setP1PrefetchStats] = useState(null)
  const [p2PrefetchStats, setP2PrefetchStats] = useState(null)

  useEffect(() => {
    if (!p1 || !p2) return
    fetchStats(String(p1.id), tour, p1.name || '').then(s => setP1PrefetchStats(s)).catch(() => {})
    fetchStats(String(p2.id), tour, p2.name || '').then(s => setP2PrefetchStats(s)).catch(() => {})
  }, [p1?.id, p2?.id, tour])

  const tourKey = tour === 'WTA' ? 'WTA' : 'ATP'
  const courts = useMemo(() => {
    const list = TOURNAMENT_CONFIG[tourKey]?.[surface] || []
    return ['None', ...list.map(t => t.name)]
  }, [tourKey, surface])

  useEffect(() => { setCourt('None') }, [tour])

  // ATP Grand Slam → the Main Draw / Qualifying toggle is relevant (BO5 vs BO3).
  const ATP_GRAND_SLAMS = ['Australian Open', 'US Open', 'Roland Garros', 'Wimbledon']
  const isAtpGs = tourKey === 'ATP' && ATP_GRAND_SLAMS.includes(court)
  useEffect(() => { if (!isAtpGs) setQualifying(false) }, [isAtpGs])

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
      // ATP Grand Slam qualifying = best-of-3 (only applies for an ATP GS court)
      qualifying: isAtpGs ? qualifying : false,
      // Rankings feed the expected-sets win-prob estimator
      player_rank:   p1.currentRank || null,
      opponent_rank: p2.currentRank || null,
    }
    try {
      const data = await calcProp(payload)
      if (propType === 'Break Points Won') {
        // STEP 5 diagnostic — surface field names so any camelCase vs
        // snake_case mismatch between backend and frontend is visible.
        console.log('[BP] player_stats:', data?.player_stats)
        console.log('[BP] opponent_stats:', data?.opponent_stats)
        console.log('[BP] bp_* fields:', Object.fromEntries(
          Object.entries(data || {}).filter(([k]) => k.startsWith('bp_') || k.startsWith('opp_'))
        ))
      }
      setResult(data)
      setRanFor(currentPair)
    } catch(e) {
      setError(e.response?.data?.detail || e.message)
    } finally { setLoading(false) }
  }, [p1, p2, tour, surface, court, propType, propLine, qualifying, isAtpGs, currentPair])

  const statKey = PROP_STAT_KEY[propType]
  const hasProjection = result != null && result.model_projection != null
  const edge = hasProjection && propLine > 0 ? (result.model_projection - propLine) : null

  const p1SurfaceStats = result?.player_stats
    || p1PrefetchStats?.[surface]
    || p1PrefetchStats?.All
    || null
  const p2SurfaceStats = result?.opponent_stats
    || p2PrefetchStats?.[surface]
    || p2PrefetchStats?.All
    || null

  const inactivityWarnings = useMemo(() => {
    const warnings = []
    const check = (player, stats) => {
      if (!player || !stats?.all_matches?.length) return
      const ts = stats.all_matches[0]?.timestamp
      if (!ts) return
      const days = Math.floor((Date.now() - ts * 1000) / 86400000)
      // Feature 3 — injury/withdrawal flag: >21d amber, >45d red (+ the backend
      // also reduces confidence 15 pts for red, reflected in the gauge).
      if (days > 45) warnings.push({ name: player.name, days, level: 'red' })
      else if (days > 21) warnings.push({ name: player.name, days, level: 'amber' })
    }
    check(p1, p1PrefetchStats)
    check(p2, p2PrefetchStats)
    return warnings
  }, [p1, p2, p1PrefetchStats, p2PrefetchStats])

  const bookLineBorderColor = edge == null ? 'rgba(255, 255, 255, 0.1)'
    : edge > 0 ? 'var(--green-bright)'
    : edge < 0 ? 'var(--red-bright)'
    : 'rgba(255, 255, 255, 0.1)'

  const last5Data = buildLast5Data(
    result?.sofascore_surface_log || result?.player_surface_matches || [],
    statKey
  )
  if (result) {
    console.log('[Last5] sofascore_surface_log:', result?.sofascore_surface_log?.slice(0, 5))
    console.log('[Last5] last5Data:', last5Data)
  }

  return (
    <div>
      {/* Players */}
      <SectionDivider label="Players" />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14, marginBottom: 18 }}>
        <PlayerSearch tour={tour} label="Selected Player" selected={p1} onSelect={p => {
          setP1(p); setResult(null); setP1PrefetchStats(null)
        }} />
        <PlayerSearch tour={tour} label="Opponent" selected={p2} onSelect={p => {
          setP2(p); setResult(null); setP2PrefetchStats(null)
        }} />
      </div>

      {/* Inactivity warnings */}
      {inactivityWarnings.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          {inactivityWarnings.map((w, i) => {
            const isRed = w.level === 'red'
            const c = isRed ? 'var(--red-bright)' : 'var(--amber)'
            const rgb = isRed ? '255, 68, 68' : '255, 179, 0'
            return (
              <div key={i} className="glass-card" style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '10px 16px', marginBottom: 8, borderColor: `rgba(${rgb}, 0.35)`,
                background: `rgba(${rgb}, 0.06)`,
              }}>
                <span style={{ fontSize: 16, color: c }}>⚠</span>
                <span style={{
                  fontSize: 12, fontWeight: 700, color: c,
                  fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 0.5,
                }}>
                  {w.name} may be inactive or injured — last match was {w.days} days ago.
                  Data may not reflect current form.{isRed ? ' Confidence reduced 15 points.' : ''}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {/* Match Setup */}
      <SectionDivider label="Match Setup" />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14, marginBottom: 18 }}>
        <div>
          <label style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.1em', display: 'block', marginBottom: 8, fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700 }}>Surface</label>
          <SurfaceSelector value={surface} onChange={s => { setSurface(s); setCourt('None'); setResult(null) }} />
        </div>
        <div>
          <label style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.1em', display: 'block', marginBottom: 8, fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700 }}>Court / Tournament</label>
          <select value={court} onChange={e => setCourt(e.target.value)} style={{
            width: '100%', padding: '12px 14px',
            background: 'rgba(14, 24, 18, 0.55)',
            border: '1px solid var(--card-border)', borderRadius: 12,
            color: 'var(--white)',
            fontFamily: '"Barlow Condensed", sans-serif', fontSize: 14, fontWeight: 600,
            letterSpacing: 0.5, outline: 'none',
          }}>
            {courts.map(c => {
              // Find ST Pace Index for the option label
              const tourKey = tour === 'WTA' ? 'WTA' : 'ATP'
              const allTourneys = Object.values(TOURNAMENT_CONFIG[tourKey] || {}).flat()
              const entry = allTourneys.find(t => t.name === c)
              const cpr = entry?.cpr
              const tier = cpr != null ? getSpeedTier(cpr) : null
              const label = c === 'None'
                ? 'None'
                : cpr != null
                  ? `${c}  ·  ST ${cpr.toFixed(1)}  ·  ${tier}`
                  : c
              return (
                <option key={c} value={c} style={{ background: '#0a0f0c' }}>{label}</option>
              )
            })}
          </select>

          {/* Surface Speed Tier chip — shows when a specific court is selected */}
          {court && court !== 'None' && (() => {
            const tourKey = tour === 'WTA' ? 'WTA' : 'ATP'
            const allT = Object.values(TOURNAMENT_CONFIG[tourKey] || {}).flat()
            const entry = allT.find(t => t.name === court)
            if (!entry?.cpr) return null
            const tier = getSpeedTier(entry.cpr)
            const tierColor = {
              'Very Slow': '#ff6b35',
              'Slow':      '#ffb347',
              'Average':   'var(--green-mid)',
              'Fast':      'var(--hard-blue)',
              'Very Fast': '#aa66ff',
            }[tier] || 'var(--muted)'
            const hasYoY = entry.prev_cpr != null &&
              Math.abs(entry.cpr - entry.prev_cpr) >= ST_YOY_THRESHOLD
            const yoyDir = hasYoY && entry.cpr > entry.prev_cpr ? 'faster' : 'slower'
            return (
              <div style={{ marginTop: 8 }}>
                <div style={{
                  display: 'inline-flex', alignItems: 'center', gap: 10,
                  padding: '7px 14px', borderRadius: 10,
                  background: `${tierColor}14`,
                  border: `1px solid ${tierColor}44`,
                  flexWrap: 'wrap',
                }}>
                  <span style={{
                    fontFamily: '"Barlow Condensed", sans-serif',
                    fontWeight: 900, fontSize: 14, color: tierColor, letterSpacing: 1,
                  }}>{tier}</span>
                  <span style={{
                    fontFamily: '"Barlow Condensed", sans-serif',
                    fontWeight: 700, fontSize: 12, color: 'var(--muted)',
                  }}>ST Pace Index {entry.cpr.toFixed(1)}</span>
                  {hasYoY && (
                    <span style={{
                      fontFamily: '"Barlow Condensed", sans-serif',
                      fontWeight: 700, fontSize: 11,
                      color: yoyDir === 'faster' ? '#ff9f43' : 'var(--hard-blue)',
                    }}>
                      {entry.prev_year + 1}: {entry.cpr.toFixed(1)} vs {entry.prev_year}: {entry.prev_cpr.toFixed(1)} — significantly {yoyDir} this year
                    </span>
                  )}
                </div>
                <div style={{
                  marginTop: 4, fontSize: 10,
                  color: 'rgba(255,255,255,0.25)',
                  fontFamily: '"Barlow Condensed", sans-serif',
                  letterSpacing: 1,
                }}>Powered by String Tension · stringtension.com</div>
              </div>
            )
          })()}

          {/* ATP Grand Slam — Main Draw (BO5) vs Qualifying (BO3) toggle.
              Only shown for an ATP Grand Slam court (WTA GS and non-GS are
              always BO3, so no toggle there). Defaults to Main Draw. */}
          {isAtpGs && (
            <div style={{ marginTop: 12 }}>
              <div style={{
                fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800,
                fontSize: 11, letterSpacing: 2, textTransform: 'uppercase',
                color: 'var(--muted)', marginBottom: 6,
              }}>Round</div>
              <div style={{ display: 'inline-flex', gap: 8 }}>
                {[
                  { label: 'Main Draw · BO5', q: false },
                  { label: 'Qualifying · BO3', q: true },
                ].map(opt => {
                  const active = qualifying === opt.q
                  return (
                    <button
                      key={opt.label}
                      onClick={() => { setQualifying(opt.q); setResult(null) }}
                      style={{
                        padding: '8px 16px', borderRadius: 999, cursor: 'pointer',
                        fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800,
                        fontSize: 12, letterSpacing: 1,
                        background: active ? 'rgba(0, 230, 118, 0.15)' : 'rgba(14, 24, 18, 0.55)',
                        border: `1px solid ${active ? 'var(--green-bright)' : 'var(--card-border)'}`,
                        color: active ? 'var(--green-bright)' : 'var(--muted)',
                        outline: 'none', transition: 'all 150ms ease',
                      }}
                    >{opt.label}</button>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Prop type */}
      <SectionDivider label="Prop Type" />
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 18 }}>
        {PROP_TYPES.map(pt => {
          const active = propType === pt
          return (
            <motion.button
              key={pt}
              whileTap={{ scale: 0.95 }}
              onClick={() => { setPropType(pt); setResult(null) }}
              style={{
                padding: '11px 20px', borderRadius: 12, cursor: 'pointer',
                fontFamily: '"Barlow Condensed", sans-serif',
                fontWeight: 800, fontSize: 13, letterSpacing: 1.8,
                textTransform: 'uppercase',
                background: active ? 'var(--green-bright)' : 'rgba(255, 255, 255, 0.025)',
                color: active ? '#000' : 'var(--muted)',
                border: `1px solid ${active ? 'var(--green-bright)' : 'var(--card-border)'}`,
                boxShadow: active ? '0 0 20px rgba(0, 230, 118, 0.35)' : 'none',
                transition: 'all .2s',
              }}
            >{pt}</motion.button>
          )
        })}
      </div>

      {/* Prop line */}
      <SectionDivider label="Prop Line" />
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 22 }}>
        <div className="glass-card" style={{ display: 'flex', alignItems: 'center', padding: '10px 18px', borderRadius: 14 }}>
          <button onClick={() => setPropLine(l => Math.max(0, +(l - 0.5).toFixed(1)))} style={{
            width: 44, height: 44, borderRadius: 10,
            border: '1px solid var(--card-border)',
            background: 'rgba(0, 0, 0, 0.3)', color: 'var(--green-mid)', cursor: 'pointer', fontSize: 24,
            fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 900,
            transition: 'all .15s', display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>−</button>
          <div style={{
            fontSize: 64, fontWeight: 900, minWidth: 140, textAlign: 'center',
            fontFamily: '"Barlow Condensed", sans-serif', lineHeight: 1, color: '#fff',
            padding: '0 18px',
            textShadow: '0 0 18px rgba(0, 230, 118, 0.2)',
          }}>{propLine.toFixed(1)}</div>
          <button onClick={() => setPropLine(l => +(l + 0.5).toFixed(1))} style={{
            width: 44, height: 44, borderRadius: 10,
            border: '1px solid var(--card-border)',
            background: 'rgba(0, 0, 0, 0.3)', color: 'var(--green-mid)', cursor: 'pointer', fontSize: 24,
            fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 900,
            transition: 'all .15s', display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>+</button>
        </div>
        <input type="number" value={propLine} step={0.5} min={0}
          onChange={e => setPropLine(Math.max(0, parseFloat(e.target.value) || 0))}
          style={{
            width: 90, padding: '12px 14px',
            background: 'rgba(255, 255, 255, 0.025)',
            border: '1px solid var(--card-border)', borderRadius: 12,
            color: 'var(--white)', fontSize: 15, outline: 'none',
            fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700,
          }}
        />
      </div>

      {/* Match format badge */}
      {propType === 'Break Points Won' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 18 }}>
          {['Best of 3', 'Best of 5'].map(fmt => {
            const isGS = court !== 'None' && [
              'Australian Open','US Open','Roland Garros','Wimbledon',
            ].includes(court)
            const activeFormat = isGS && tour === 'ATP' ? 'Best of 5' : 'Best of 3'
            const isActive = fmt === activeFormat
            return (
              <div key={fmt} style={{
                padding: '6px 14px', borderRadius: 999,
                fontFamily: '"Barlow Condensed", sans-serif',
                fontWeight: 800, fontSize: 11, letterSpacing: 1.5,
                textTransform: 'uppercase',
                background: isActive
                  ? (fmt === 'Best of 5' ? 'rgba(107, 159, 255, 0.15)' : 'rgba(0, 230, 118, 0.1)')
                  : 'transparent',
                color: isActive
                  ? (fmt === 'Best of 5' ? 'var(--hard-blue)' : 'var(--green-mid)')
                  : 'var(--muted)',
                border: `1px solid ${isActive
                  ? (fmt === 'Best of 5' ? 'rgba(107, 159, 255, 0.4)' : 'rgba(0, 230, 118, 0.3)')
                  : 'rgba(13, 21, 16, 0.6)'}`,
              }}>
                {fmt}{isActive && fmt === 'Best of 5' && <span style={{ marginLeft: 6, fontSize: 9 }}>AUTO</span>}
              </div>
            )
          })}
          <span style={{ fontSize: 11, color: 'var(--muted)', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 600 }}>
            ATP Grand Slams auto-set to BO5
          </span>
        </div>
      )}

      {/* Run button — large with shimmer */}
      <motion.button
        whileHover={p1 && p2 && !loading ? { scale: 1.005 } : {}}
        whileTap={p1 && p2 && !loading ? { scale: 0.985 } : {}}
        onClick={run}
        disabled={!p1 || !p2 || loading}
        style={{
          position: 'relative',
          width: '100%', padding: '20px 24px', borderRadius: 16, fontSize: 16,
          fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 900, letterSpacing: 3,
          textTransform: 'uppercase', cursor: p1 && p2 && !loading ? 'pointer' : 'not-allowed',
          background: p1 && p2 && !loading
            ? 'linear-gradient(135deg, #00FF87 0%, #00A854 100%)'
            : 'rgba(255, 255, 255, 0.03)',
          color: p1 && p2 && !loading ? '#000' : 'var(--muted)',
          border: 'none',
          boxShadow: p1 && p2 && !loading
            ? '0 8px 28px rgba(0, 230, 118, 0.35), 0 0 0 1px rgba(0, 230, 118, 0.4) inset'
            : 'none',
          marginBottom: 24,
          transition: 'all .2s',
          overflow: 'hidden',
          textShadow: p1 && p2 && !loading ? '0 1px 2px rgba(0,0,0,0.2)' : 'none',
        }}
      >
        {/* Shimmer overlay */}
        {p1 && p2 && !loading && (
          <span style={{
            position: 'absolute', top: 0, left: 0, height: '100%', width: '40%',
            background: 'linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.4), transparent)',
            animation: 'shimmer 3s ease-in-out infinite',
            pointerEvents: 'none',
          }} />
        )}
        <span style={{ position: 'relative', zIndex: 1 }}>
          {loading ? 'Analyzing…' : 'Run Prop Estimate'}
        </span>
      </motion.button>

      {/* Results */}
      {loading && <LoadingSpinner />}
      {error   && (
        <div className="glass-card" style={{ color: 'var(--red-bright)', padding: '16px 20px', background: 'rgba(255, 68, 68, 0.06)', borderColor: 'rgba(255, 68, 68, 0.3)' }}>
          Error: {error}
        </div>
      )}

      <AnimatePresence>
        {result && !loading && (
          <motion.div key="results" variants={REVEAL_CONTAINER} initial="hidden" animate="show" exit={{ opacity: 0 }}>

            {/* ── Projection cards ── */}
            <motion.div variants={REVEAL_ITEM}>
              <SectionDivider label="PROJECTION" />

              {!hasProjection && result.note && (
                <div className="glass-card" style={{
                  padding: '14px 18px', background: 'rgba(255, 68, 68, 0.06)', borderColor: 'rgba(255, 68, 68, 0.3)',
                  marginBottom: 16, fontSize: 13,
                }}>
                  <span style={{ color: 'var(--amber)', fontWeight: 800 }}>Insufficient Data — </span>
                  <span style={{ color: 'var(--muted)' }}>{result.note}</span>
                </div>
              )}

              {hasProjection && result.data_stale && (
                <div className="glass-card" style={{
                  padding: '12px 16px', background: 'rgba(255, 179, 0, 0.06)', borderColor: 'rgba(255, 179, 0, 0.3)',
                  marginBottom: 14, fontSize: 12, display: 'flex', alignItems: 'center', gap: 10,
                }}>
                  <span style={{ color: 'var(--amber)', fontWeight: 800, fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 1 }}>⚠ STALE DATA</span>
                  <span style={{ color: 'rgba(255, 179, 0, 0.7)' }}>
                    Live Sofascore fetch unavailable — projection built from the most recent cached snapshot.
                  </span>
                </div>
              )}

              {hasProjection && (result.sanity_failed || result.player_limited_data || result.opponent_limited_data || result.conv_rate_fallback) && (
                <div className="glass-card" style={{
                  padding: '12px 16px', background: 'rgba(255, 179, 0, 0.06)', borderColor: 'rgba(255, 179, 0, 0.3)',
                  marginBottom: 14, fontSize: 12, display: 'flex', alignItems: 'center', gap: 10,
                }}>
                  <span style={{ color: 'var(--amber)', fontWeight: 800, fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 1 }}>⚠ LIMITED DATA</span>
                  <span style={{ color: 'rgba(255, 179, 0, 0.7)' }}>
                    {result.sanity_failed ? 'Projection outside normal bounds — confidence reduced. ' : ''}
                    {result.player_limited_data ? `${p1?.name || 'Player'} has limited surface data (${result.player_surface_n ?? 0} matches < 10). ` : ''}
                    {result.opponent_limited_data ? `${p2?.name || 'Opponent'} has limited surface data (${result.opponent_surface_n ?? 0} matches < 10). ` : ''}
                    {result.conv_rate_fallback ? 'Limited recent conversion data on this surface — career/tour-average fallback used.' : ''}
                  </span>
                </div>
              )}

              {/* Reality-check banner for unusually-high BP projections.
                  This is a warning only — the projection is not capped, the
                  bettor decides whether to trust it. */}
              {hasProjection && propType === 'Break Points Won' && result.bp_high_projection && (
                <div className="glass-card" style={{
                  padding: '12px 16px', background: 'rgba(255, 179, 0, 0.06)', borderColor: 'rgba(255, 179, 0, 0.3)',
                  marginBottom: 14, fontSize: 12, display: 'flex', alignItems: 'center', gap: 10,
                }}>
                  <span style={{ color: 'var(--amber)', fontWeight: 800, fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 1 }}>⚠ HIGH PROJECTION</span>
                  <span style={{ color: 'rgba(255, 179, 0, 0.75)' }}>
                    Model output {result.model_projection?.toFixed(1)} exceeds the
                    {result.bp_high_threshold === 9.0 ? ' BO5' : ' BO3'} reality threshold of
                    {' '}{result.bp_high_threshold?.toFixed(1)} — verify data quality before betting.
                    {result.bp_momentum_capped && ' (Momentum bonus hit its hard cap.)'}
                  </span>
                </div>
              )}

              {/* Three projection cards */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 14, marginBottom: 22 }}>

                {/* Model Projection */}
                <motion.div className="glass-card" variants={REVEAL_ITEM} style={{
                  padding: '22px 22px 20px',
                  borderColor: result.sanity_failed ? 'rgba(255, 179, 0, 0.35)' : 'rgba(0, 230, 118, 0.35)',
                  boxShadow: result.sanity_failed
                    ? '0 6px 28px rgba(255, 179, 0, 0.15)'
                    : '0 6px 28px rgba(0, 230, 118, 0.18)',
                }}>
                  <div style={{
                    fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, fontSize: 10,
                    letterSpacing: '.3em', textTransform: 'uppercase',
                    color: 'var(--green-mid)', marginBottom: 8,
                  }}>Model Projection</div>
                  {hasProjection ? (
                    <>
                      <div style={{
                        fontSize: 72, fontWeight: 900,
                        color: result.sanity_failed ? 'var(--amber)' : 'var(--green-bright)',
                        lineHeight: 1,
                        fontFamily: '"Barlow Condensed", sans-serif',
                        textShadow: result.sanity_failed
                          ? '0 0 22px rgba(255, 179, 0, 0.4)'
                          : '0 0 24px rgba(0, 255, 135, 0.55)',
                      }}>
                        <NumberFlow value={result.model_projection} format={{ minimumFractionDigits: 1, maximumFractionDigits: 1 }} />
                      </div>
                      <div style={{
                        fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 11,
                        letterSpacing: 2, color: 'var(--muted)', textTransform: 'uppercase', marginTop: 10,
                      }}>{p1?.name}</div>
                    </>
                  ) : (
                    <div style={{ fontSize: 16, color: 'var(--muted)', marginTop: 8 }}>N/A</div>
                  )}
                </motion.div>

                {/* Book Line */}
                <motion.div className="glass-card" variants={REVEAL_ITEM} style={{
                  padding: '22px 22px 20px',
                  borderColor: bookLineBorderColor,
                }}>
                  <div style={{
                    fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, fontSize: 10,
                    letterSpacing: '.3em', textTransform: 'uppercase',
                    color: 'var(--green-mid)', marginBottom: 8,
                  }}>Book Line</div>
                  <div style={{
                    fontSize: 72, fontWeight: 900, color: '#fff', lineHeight: 1,
                    fontFamily: '"Barlow Condensed", sans-serif',
                  }}>
                    {propLine > 0 ? propLine.toFixed(1) : '—'}
                  </div>
                  {edge != null && (
                    <div style={{
                      fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800,
                      fontSize: 16, letterSpacing: 1.5, marginTop: 10,
                      color: edge >= 0 ? 'var(--green-bright)' : 'var(--red-bright)',
                      display: 'inline-flex', alignItems: 'center', gap: 6,
                      textShadow: edge >= 0 ? '0 0 10px rgba(0, 230, 118, 0.4)' : '0 0 10px rgba(255, 68, 68, 0.4)',
                    }}>
                      <span>{edge >= 0 ? '▲' : '▼'}</span>
                      edge <NumberFlow value={edge} format={{ minimumFractionDigits: 1, signDisplay: 'always' }} />
                    </div>
                  )}
                </motion.div>

                {/* Lean */}
                <motion.div className="glass-card" variants={REVEAL_ITEM} style={{
                  padding: '22px 22px 20px',
                  display: 'flex', flexDirection: 'column', alignItems: 'center',
                  gap: 12,
                }}>
                  <div style={{
                    fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, fontSize: 10,
                    letterSpacing: '.3em', textTransform: 'uppercase',
                    color: 'var(--green-mid)', alignSelf: 'flex-start',
                  }}>Lean</div>
                  {hasProjection ? (
                    <LeanBadge lean={result.lean} />
                  ) : (
                    <div style={{ fontSize: 16, color: 'var(--muted)', marginTop: 8 }}>N/A</div>
                  )}
                </motion.div>
              </div>

              {/* Confidence Gauge */}
              {hasProjection && (
                <motion.div variants={REVEAL_ITEM} style={{ display: 'flex', justifyContent: 'center', marginBottom: 24 }}>
                  <div className="glass-card" style={{ padding: '24px 32px', display: 'flex', justifyContent: 'center' }}>
                    <ConfidenceGauge confidence={result.confidence || 0} size={170} />
                  </div>
                </motion.div>
              )}
            </motion.div>

            {/* ── Expected sets banner — match length drives volume ── */}
            {result.expected_sets != null && (
              <motion.div variants={REVEAL_ITEM}>
                <ExpectedSetsBanner
                  expectedSets={result.expected_sets}
                  competitiveness={result.competitiveness}
                  winProbGap={result.win_prob_gap}
                  p1Prob={result.p1_win_prob}
                  p2Prob={result.p2_win_prob}
                  p1Name={p1?.name}
                  p2Name={p2?.name}
                  isBo5={result.is_bo5}
                />
              </motion.div>
            )}

            {/* ── ST Pace Index result banner — shows dynamic value from backend ── */}
            {result.court_pace_index != null && result.court_speed_tier && (() => {
              const tierColor = {
                'Very Slow': '#ff6b35',
                'Slow':      '#ffb347',
                'Average':   'var(--green-mid)',
                'Fast':      'var(--hard-blue)',
                'Very Fast': '#aa66ff',
              }[result.court_speed_tier] || 'var(--muted)'
              return (
                <motion.div variants={REVEAL_ITEM} style={{
                  display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                  padding: '10px 16px',
                  background: `${tierColor}10`,
                  border: `1px solid ${tierColor}33`,
                  borderRadius: 10,
                  marginBottom: 14,
                }}>
                  <span style={{
                    fontFamily: '"Barlow Condensed", sans-serif',
                    fontWeight: 900, fontSize: 14, color: tierColor, letterSpacing: 1,
                  }}>{result.court_speed_tier}</span>
                  <span style={{
                    fontFamily: '"Barlow Condensed", sans-serif',
                    fontWeight: 700, fontSize: 12, color: 'var(--muted)',
                  }}>
                    {court && court !== 'None' ? court : surface} — ST Pace Index {result.court_pace_index.toFixed(1)}
                    {result.court_st_source === 'st_live' && (
                      <span style={{ marginLeft: 8, color: 'var(--green-mid)', fontSize: 10 }}>● 2026 Live</span>
                    )}
                    {result.court_st_source !== 'st_live' && (
                      <span style={{ marginLeft: 8, color: 'rgba(255,255,255,0.3)', fontSize: 10 }}>Historical</span>
                    )}
                  </span>
                  {result.court_yoy_note && (
                    <span style={{
                      fontFamily: '"Barlow Condensed", sans-serif',
                      fontWeight: 700, fontSize: 11, color: '#ff9f43',
                    }}>{result.court_yoy_note}</span>
                  )}
                  {/* Match format — confirm BO5 (ATP GS main draw) vs BO3 was applied */}
                  {result.match_format_label && (
                    <span style={{
                      padding: '3px 10px', borderRadius: 999,
                      background: result.match_format === 'best_of_5'
                        ? 'rgba(170, 102, 255, 0.15)' : 'rgba(107, 159, 255, 0.12)',
                      border: `1px solid ${result.match_format === 'best_of_5' ? '#aa66ff55' : '#6b9fff44'}`,
                      color: result.match_format === 'best_of_5' ? '#aa66ff' : 'var(--hard-blue)',
                      fontFamily: '"Barlow Condensed", sans-serif',
                      fontWeight: 800, fontSize: 11, letterSpacing: 0.5,
                    }}>📋 {result.match_format_label}</span>
                  )}
                  <span style={{
                    marginLeft: 'auto', fontSize: 9, color: 'rgba(255,255,255,0.2)',
                    fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 1,
                  }}>Powered by String Tension</span>
                </motion.div>
              )
            })()}

            {/* ── Environment banner (full-width) ── */}
            {result.environment && (
              <motion.div variants={REVEAL_ITEM}>
                <EnvironmentBanner
                  environment={result.environment}
                  environmentLabel={result.environment_label}
                />
              </motion.div>
            )}

            {/* ── Last 5 Matches bar chart ── */}
            {statKey && last5Data.length > 0 && (
              <motion.div variants={REVEAL_ITEM}>
                <SectionDivider label={`Last 5 Matches — ${propType} (${p1?.name})`} />
                <div className="glass-card" style={{ padding: '20px 18px 14px', marginBottom: 22 }}>
                  {/* Best-of-5 context flag: this projection is BO5 (ATP Grand
                      Slam main draw) but some of these last-5 matches were not. */}
                  {result?.is_bo5 && last5Data.some(d => d.bo5 === false) && (
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px',
                      marginBottom: 12, borderRadius: 8,
                      border: '1px solid rgba(255,179,0,0.35)', background: 'rgba(255,179,0,0.06)',
                    }}>
                      <span style={{ fontSize: 14, color: 'var(--amber)' }}>⚠</span>
                      <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--amber)',
                        fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 0.5 }}>
                        Projected as BEST OF 5 — {last5Data.filter(d => d.bo5 === false).length} of these
                        last 5 were Best of 3, which usually means fewer {propType.toLowerCase()}.
                      </span>
                    </div>
                  )}
                  {propLine > 0 && (
                    <div style={{ display: 'flex', gap: 18, marginBottom: 12, fontSize: 11, fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, letterSpacing: 1 }}>
                      <span style={{ color: 'var(--green-bright)' }}>● OVER {propLine.toFixed(1)}</span>
                      <span style={{ color: 'var(--red-bright)' }}>● UNDER {propLine.toFixed(1)}</span>
                      <span style={{ color: '#888' }}>● PUSH</span>
                    </div>
                  )}
                  <Last5Bars data={last5Data} propLine={propLine} playerName={p1?.name} />
                </div>
              </motion.div>
            )}

            {/* ── Break Points momentum breakdown ── */}
            {propType === 'Break Points Won' && result?.bp_base_proj != null && (
              <motion.div variants={REVEAL_ITEM}>
                <SectionDivider label="BREAK POINT BREAKDOWN" />
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12, marginBottom: 18 }}>
                  {[
                    ['Base Projection', result.bp_base_proj?.toFixed(2), null],
                    ['Opp Proj BP Won', result.bp_opp_projected?.toFixed(2), 'var(--amber)'],
                    [`Momentum Bonus (${result.match_format === 'best_of_5' ? 'BO5' : 'BO3'} ${surface})`,
                      result.bp_momentum_bonus != null ? `+${result.bp_momentum_bonus.toFixed(3)}` : '—',
                      'var(--green-bright)'],
                    ['Final Projection', result.model_projection?.toFixed(1), 'var(--green-bright)'],
                  ].map(([lbl, val, col]) => (
                    <div key={lbl} className="glass-card" style={{ padding: '14px 16px' }}>
                      <div style={{
                        fontFamily: '"Barlow Condensed", sans-serif', fontSize: 9, fontWeight: 800,
                        letterSpacing: 1.8, textTransform: 'uppercase', color: 'var(--green-mid)', marginBottom: 6,
                      }}>{lbl}</div>
                      <div style={{
                        fontFamily: '"Barlow Condensed", sans-serif', fontSize: 26, fontWeight: 900,
                        color: col || '#fff',
                        textShadow: col ? `0 0 14px ${col}55` : 'none',
                      }}>{val ?? '—'}</div>
                    </div>
                  ))}
                </div>
              </motion.div>
            )}

            {/* ── Stat comparison ── */}
            {(p1SurfaceStats || p2SurfaceStats) && (
              <motion.div variants={REVEAL_ITEM}>
                <SectionDivider label="SURFACE STATS" />

                {result?.handedness_edge && (
                  <div className="glass-card" style={{
                    display: 'flex', alignItems: 'center', gap: 12,
                    padding: '12px 16px', marginBottom: 14,
                    background: 'rgba(255, 179, 0, 0.06)', borderColor: 'rgba(255, 179, 0, 0.3)',
                    fontSize: 12,
                  }}>
                    <span style={{ color: 'var(--amber)', fontWeight: 800, fontFamily: '"Barlow Condensed", sans-serif', fontSize: 12, letterSpacing: 1.5, textTransform: 'uppercase' }}>⚡ Handedness Edge</span>
                    <span style={{ color: 'rgba(255, 179, 0, 0.7)' }}>
                      {p1?.name} ({result.player_handedness}H) vs {p2?.name} ({result.opponent_handedness}H)
                    </span>
                  </div>
                )}

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(290px, 1fr))', gap: 14, marginBottom: 22 }}>
                  {[
                    { s: p1SurfaceStats, name: p1?.name, hand: result?.player_handedness,
                      taM: result?.player_ta_matches, ssM: result?.player_ss_matches,
                      fallback: result?.player_surface_fallback, aceAgainst: null,
                      recentTier:    result?.player_ta_recent_tier,
                      recentMatches: result?.player_ta_recent_matches,
                      recentAllN:    result?.player_ta_recent_all_n,
                      penaltyKind:   result?.player_ta_penalty_kind },
                    { s: p2SurfaceStats, name: p2?.name, hand: result?.opponent_handedness,
                      taM: result?.opponent_ta_matches, ssM: result?.opponent_ss_matches,
                      fallback: result?.opponent_surface_fallback, aceAgainst: result?.opponent_ace_against,
                      recentTier:    result?.opponent_ta_recent_tier,
                      recentMatches: result?.opponent_ta_recent_matches,
                      recentAllN:    result?.opponent_ta_recent_all_n,
                      penaltyKind:   result?.opponent_ta_penalty_kind },
                  ].map(({ s, name, hand, taM, ssM, fallback, aceAgainst,
                          recentTier, recentMatches, recentAllN, penaltyKind }, idx) => {
                    const d = s || {}
                    const isBPProp = propType === 'Break Points Won'
                    const surfLabel = surface || 'Surf'
                    // Games-won cells, green/red vs tour average (Part 3 display)
                    const _sgwAvg = tour === 'WTA' ? 72 : 78
                    const _rgwAvg = tour === 'WTA' ? 23 : 18
                    const _gwCell = (val, avg) => val == null ? 'N/A'
                      : <span style={{ color: val >= avg ? 'var(--green-bright)' : 'var(--red-bright)', fontWeight: 700 }}>{val.toFixed(0)}%</span>
                    let rows
                    if (isBPProp && idx === 0) {
                      const surfConv    = result?.bp_surf_conv_pct
                      const overallConv = result?.bp_overall_conv_pct
                      const blendedConv = result?.bp_blended_conv_pct ?? result?.conv_rate_pct
                      const surfN       = result?.bp_surf_conv_sample ?? d.matches_played
                      const overallN    = result?.bp_overall_conv_sample
                      const surfOnlyFlag = result?.bp_surf_only_flag
                      rows = [
                        [`Conv (${surfLabel})`,
                          surfConv != null
                            ? <span style={{ color: surfOnlyFlag ? 'var(--amber)' : 'var(--white)' }}>
                                {surfConv.toFixed(0)}%
                                {surfN != null && <span style={{ fontSize: 9, color: 'var(--muted)', marginLeft: 4 }}>{surfN}m</span>}
                              </span>
                            : '—'],
                        ['Conv (Overall)',
                          overallConv != null
                            ? <span>{overallConv.toFixed(0)}%
                                {overallN != null && <span style={{ fontSize: 9, color: 'var(--muted)', marginLeft: 4 }}>{overallN}m</span>}
                              </span>
                            : '—'],
                        ['Conv (Blended)', blendedConv != null ? `${blendedConv.toFixed(0)}%` : 'N/A'],
                        // FIX: backend exposes these as bp_surf_opp_faced /
                        // bp_overall_opp_faced (the opponent's BP faced = this
                        // player's BP opportunities created). Reading the
                        // non-prefixed keys left these cells blank.
                        ['BP Generated', result?.bp_generated_per_match != null ? fmt(result.bp_generated_per_match) : 'N/A'],
                        ['BP Gen (Quality-Adj)', result?.bp_generated_quality_adj != null ? fmt(result.bp_generated_quality_adj) : 'N/A'],
                        [`BP Opps (${surfLabel})`, result?.bp_surf_opp_faced != null ? fmt(result.bp_surf_opp_faced) : 'N/A'],
                        ['Service Games Won', _gwCell(d.service_games_won_pct, _sgwAvg)],
                        ['Return Games Won', _gwCell(d.return_games_won_pct, _rgwAvg)],
                        ['Ret Pts Won (1st)', d.return_first_serve_pts_won != null ? fmtPct(d.return_first_serve_pts_won) : 'N/A'],
                        ['Win Rate', d.win_rate != null ? fmtPct(d.win_rate) : 'N/A'],
                        ['Matches', d.matches_played || 'N/A'],
                      ]
                    } else if (isBPProp && idx === 1) {
                      const serveTier = result?.opp_serve_tier
                      const serveTierColor = { Elite: 'var(--red-bright)', Good: 'var(--amber)', Weak: 'var(--green-bright)' }[serveTier] || 'var(--muted)'
                      const oppSurfN = result?.bp_opp_surf_sample ?? d.matches_played
                      rows = [
                        [`BP Faced (${surfLabel})`,
                          result?.bp_surf_opp_faced != null
                            ? <span>{fmt(result.bp_surf_opp_faced)}
                                {oppSurfN != null && <span style={{ fontSize: 9, color: 'var(--muted)', marginLeft: 4 }}>{oppSurfN}m</span>}
                              </span>
                            : (d.bp_faced_count != null ? fmt(d.bp_faced_count) : 'N/A')],
                        ['BP Faced (Overall)',
                          result?.bp_overall_opp_faced != null ? fmt(result.bp_overall_opp_faced) : 'N/A'],
                        // 'Opp BP Won (proj)' removed here — it is the reverse-direction
                        // projection (opponent's breaks against the selected player),
                        // which only exists to feed the momentum bonus. Shown in the
                        // opponent's serve-stat card it read as a contradiction of the
                        // headline projection. It still appears, correctly labelled, in
                        // the BREAK POINT BREAKDOWN panel as 'Opp Proj BP Won'.
                        ['Service Games Won', _gwCell(d.service_games_won_pct, _sgwAvg)],
                        ['Return Games Won', _gwCell(d.return_games_won_pct, _rgwAvg)],
                        ['Hold Rate (est)', result?.opp_hold_rate_pct != null ? `${result.opp_hold_rate_pct.toFixed(0)}%` : 'N/A'],
                        ['Server Quality', result?.opp_server_quality_tier
                          ? <span style={{ color: /Elite|Strong/.test(result.opp_server_quality_tier) ? 'var(--red-bright)' : /Weak/.test(result.opp_server_quality_tier) ? 'var(--green-bright)' : 'var(--amber)', fontWeight: 800 }}>{result.opp_server_quality_tier}</span>
                          : (serveTier ? <span style={{ color: serveTierColor, fontWeight: 800 }}>{serveTier}</span> : 'N/A')],
                        ['1st Srv Won', d.first_serve_pts_won != null ? fmtPct(d.first_serve_pts_won) : 'N/A'],
                        ['2nd Srv Won', d.second_serve_pts_won != null ? fmtPct(d.second_serve_pts_won) : 'N/A'],
                        ['Matches', d.matches_played || 'N/A'],
                      ]
                    } else {
                      rows = [
                        ['Aces/Match', fmt(d.aces)],
                        ['DFs/Match', fmt(d.double_faults)],
                        ['1st Srv Won', fmtPct(d.first_serve_pts_won)],
                        ['2nd Srv Won', fmtPct(d.second_serve_pts_won)],
                        ['Service Games Won', _gwCell(d.service_games_won_pct, _sgwAvg)],
                        ['Return Games Won', _gwCell(d.return_games_won_pct, _rgwAvg)],
                        ['BP Converted', fmtPct(d.bp_converted)],
                        ['Win Rate', fmtPct(d.win_rate)],
                        ['Matches', d.matches_played || '—'],
                      ]
                      if (idx === 1 && aceAgainst != null) {
                        rows.splice(1, 0, ['Aces Conceded/Match', fmt(aceAgainst)])
                      }
                    }
                    return (
                      <div key={idx} className="glass-card" style={{ padding: '20px 22px' }}>
                        <div style={{
                          fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 900, fontSize: 18,
                          color: idx === 0 ? 'var(--white)' : 'rgba(255, 255, 255, 0.75)',
                          marginBottom: 8, display: 'flex', alignItems: 'center',
                          letterSpacing: 0.5,
                        }}>
                          {name}<HandBadge hand={hand} />
                        </div>
                        <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
                          {/* Recent-window chip — replaces TA CAREER */}
                          {recentTier === '52w' && recentMatches > 0 && (
                            <span style={{ fontSize: 9, fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, letterSpacing: 1.5, padding: '3px 8px', borderRadius: 999, background: 'rgba(0, 230, 118, 0.08)', color: 'var(--green-mid)', border: '1px solid rgba(0, 230, 118, 0.2)' }}>
                              LAST 52 WEEKS: {recentMatches} MATCHES
                            </span>
                          )}
                          {recentTier === '2yr' && recentMatches > 0 && (
                            <span style={{ fontSize: 9, fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, letterSpacing: 1.5, padding: '3px 8px', borderRadius: 999, background: 'rgba(255, 179, 0, 0.08)', color: 'var(--amber)', border: '1px solid rgba(255, 179, 0, 0.3)' }}>
                              LAST 2 YEARS: {recentMatches} MATCHES
                            </span>
                          )}
                          {ssM != null && (
                            <span style={{ fontSize: 9, fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, letterSpacing: 1.5, padding: '3px 8px', borderRadius: 999, background: 'rgba(0, 230, 118, 0.08)', color: 'var(--green-mid)', border: '1px solid rgba(0, 230, 118, 0.2)' }}>
                              SS {ssM} RECENT
                            </span>
                          )}
                          {fallback && (
                            <span style={{ fontSize: 9, fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, letterSpacing: 1.5, padding: '3px 8px', borderRadius: 999, background: 'rgba(255, 179, 0, 0.08)', color: 'var(--amber)', border: '1px solid rgba(255, 179, 0, 0.3)' }}>
                              ALL-SURFACE AVG
                            </span>
                          )}
                        </div>

                        {/* Recent-data warnings — three tiers based on penalty_kind */}
                        {penaltyKind === 'specialist' && (
                          <div style={{
                            fontSize: 11, color: 'var(--hard-blue)',
                            fontFamily: '"Barlow Condensed", sans-serif',
                            fontWeight: 700, letterSpacing: 0.5,
                            padding: '6px 10px', borderRadius: 8,
                            background: 'rgba(107, 159, 255, 0.06)',
                            border: '1px solid rgba(107, 159, 255, 0.3)',
                            marginBottom: 10,
                          }}>
                            ◐ Surface specialist — limited data on this surface
                            {recentAllN != null && ` (${recentAllN} matches across all surfaces in last 52w)`}, but overall form is strong
                          </div>
                        )}
                        {penaltyKind === 'limited' && (
                          <div style={{
                            fontSize: 11, color: 'var(--amber)',
                            fontFamily: '"Barlow Condensed", sans-serif',
                            fontWeight: 700, letterSpacing: 0.5,
                            padding: '6px 10px', borderRadius: 8,
                            background: 'rgba(255, 179, 0, 0.06)',
                            border: '1px solid rgba(255, 179, 0, 0.3)',
                            marginBottom: 10,
                          }}>
                            ⚠ Limited recent activity — fewer than 5 matches on surface and under 20 total in last 52 weeks
                          </div>
                        )}
                        {penaltyKind === 'insufficient' && (
                          <div style={{
                            fontSize: 11, color: 'var(--red-bright)',
                            fontFamily: '"Barlow Condensed", sans-serif',
                            fontWeight: 700, letterSpacing: 0.5,
                            padding: '6px 10px', borderRadius: 8,
                            background: 'rgba(255, 68, 68, 0.06)',
                            border: '1px solid rgba(255, 68, 68, 0.3)',
                            marginBottom: 10,
                          }}>
                            ⚠ Insufficient recent data — fewer than 10 total matches in last 52 weeks
                          </div>
                        )}
                        {rows.map(([lbl, val], rowIdx) => {
                          const isNode = val != null && typeof val === 'object' && val.$$typeof
                          return (
                            <div key={lbl} style={{
                              display: 'flex', justifyContent: 'space-between',
                              padding: '8px 0',
                              borderBottom: '1px solid rgba(13, 21, 16, 0.6)', fontSize: 13,
                              background: rowIdx % 2 === 1 ? 'rgba(0, 230, 118, 0.015)' : 'transparent',
                              marginLeft: -8, paddingLeft: 8, marginRight: -8, paddingRight: 8,
                            }}>
                              <span style={{ color: 'var(--muted)', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, letterSpacing: 0.5 }}>{lbl}</span>
                              {isNode
                                ? <span style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, fontSize: 14 }}>{val}</span>
                                : <span style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, fontSize: 14, color: statColor(lbl, val) || '#fff' }}>{val}</span>
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
              <motion.div variants={REVEAL_ITEM}>
                <SectionDivider label="H2H CONTEXT" />
                <div className="glass-card" style={{ padding: '22px 24px', marginBottom: 18 }}>
                  {result.h2h_context.total === 0 ? (
                    <div style={{ fontSize: 13, color: 'var(--muted)' }}>No H2H data available</div>
                  ) : (
                    <>
                      <div style={{ display: 'flex', gap: 28, alignItems: 'center', marginBottom: 14, justifyContent: 'center' }}>
                        <div style={{ textAlign: 'center' }}>
                          <div style={{ fontSize: 11, color: 'var(--muted)', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase' }}>{p1?.name}</div>
                          <div style={{ fontSize: 56, fontWeight: 900, color: 'var(--green-bright)', fontFamily: '"Barlow Condensed", sans-serif', textShadow: '0 0 20px rgba(0, 230, 118, 0.4)' }}>{result.h2h_context.p1_wins}</div>
                        </div>
                        <div style={{ fontSize: 24, color: 'var(--muted)', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, letterSpacing: 2 }}>VS</div>
                        <div style={{ textAlign: 'center' }}>
                          <div style={{ fontSize: 11, color: 'var(--muted)', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase' }}>{p2?.name}</div>
                          <div style={{ fontSize: 56, fontWeight: 900, color: 'var(--red-bright)', fontFamily: '"Barlow Condensed", sans-serif', textShadow: '0 0 20px rgba(255, 68, 68, 0.4)' }}>{result.h2h_context.p2_wins}</div>
                        </div>
                      </div>
                      <div style={{ fontSize: 12, color: 'var(--muted)', textAlign: 'center', fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 1 }}>
                        {result.h2h_context.total} {result.h2h_context.total !== 1 ? 'meetings' : 'meeting'}
                        {result.h2h_context.surface_matches > 0 && ` · ${result.h2h_context.surface_matches} on ${surface}`}
                        {result.h2h_context.date_range && ` · ${result.h2h_context.date_range}`}
                      </div>
                    </>
                  )}
                </div>
              </motion.div>
            )}

            {/* ── Model explanation ── */}
            {result.plain_english_explanation && (
              <motion.div variants={REVEAL_ITEM}>
                <SectionDivider label="MODEL EXPLANATION" />
                <div className="glass-card" style={{
                  borderLeft: '3px solid var(--green-bright)',
                  padding: '20px 22px',
                  marginBottom: 18,
                }}>
                  <div style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, fontSize: 10, letterSpacing: '0.3em', textTransform: 'uppercase', color: 'var(--green-bright)', marginBottom: 12 }}>
                    Model Logic
                  </div>
                  <p style={{ fontSize: 14, color: 'rgba(255,255,255,0.75)', lineHeight: 1.8, margin: 0 }}>
                    {result.plain_english_explanation}
                  </p>
                </div>
              </motion.div>
            )}

            {/* ── AI scouting report ── */}
            {result.ai_writeup && (
              <motion.div variants={REVEAL_ITEM}>
                <SectionDivider label="AI SCOUTING REPORT" />
                <div className="glass-card" style={{
                  padding: '24px 26px', position: 'relative', marginBottom: 18,
                }}>
                  <span style={{
                    position: 'absolute', top: 16, right: 18,
                    fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 900, fontSize: 9, letterSpacing: '0.25em',
                    textTransform: 'uppercase', color: 'var(--green-bright)',
                    background: 'rgba(0, 230, 118, 0.08)',
                    border: '1px solid rgba(0, 230, 118, 0.4)',
                    padding: '4px 10px', borderRadius: 999,
                  }}>BASELINE AI</span>
                  <div style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, fontSize: 10, letterSpacing: '0.3em', textTransform: 'uppercase', color: 'var(--green-mid)', marginBottom: 14 }}>
                    AI Scouting Report
                  </div>
                  <p style={{ fontSize: 14, color: 'rgba(255,255,255,0.78)', lineHeight: 1.85, margin: 0, paddingRight: 80 }}>
                    {result.ai_writeup}
                  </p>
                </div>
              </motion.div>
            )}

            {/* ── Confidence breakdown ── */}
            <motion.div variants={REVEAL_ITEM}>
              <ConfidenceBreakdown breakdown={result.confidence_breakdown} />
            </motion.div>

          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
