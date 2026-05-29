import { useState } from 'react'
import { motion } from 'motion/react'
import NumberFlow from '@number-flow/react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  LineChart, Line, CartesianGrid,
} from 'recharts'
import PlayerSearch from '../components/PlayerSearch'
import LoadingSpinner from '../components/LoadingSpinner'
import SurfaceBadge from '../components/SurfaceBadge'
import ResultPill from '../components/ResultPill'
import { usePlayerStats } from '../hooks/usePlayerStats'
import { STAT_LABELS, ATP_AVERAGES, WTA_AVERAGES, fmt, fmtPct } from '../utils/constants'

const SURFACES = ['All', 'Hard', 'Clay', 'Grass']

const STAT_KEYS = [
  'aces','double_faults','first_serve_pct','first_serve_pts_won',
  'second_serve_pts_won','return_first_serve_pts_won',
  'return_second_serve_pts_won','bp_converted','bp_saved',
]

const SURFACE_HEADER_COLORS = {
  All:   '#aaa',
  Hard:  '#6b9fff',
  Clay:  '#ff6b35',
  Grass: '#00e676',
}

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

function surfaceTabStyle(surface, isActive) {
  if (!isActive) return { background: 'rgba(255, 255, 255, 0.025)', color: 'var(--muted)', border: '1px solid var(--card-border)' }
  const styles = {
    Hard:  { background: 'linear-gradient(135deg, #001a40, #003070)', color: '#6b9fff', border: '1px solid #2a3d5a', glow: '0 0 14px rgba(107, 159, 255, 0.3)' },
    Clay:  { background: 'linear-gradient(135deg, #2a0800, #5a1c00)', color: '#ff6b35', border: '1px solid #5a2010', glow: '0 0 14px rgba(255, 107, 53, 0.3)' },
    Grass: { background: 'linear-gradient(135deg, #001a0b, #003a20)', color: '#00e676', border: '1px solid #1a4020', glow: '0 0 14px rgba(0, 230, 118, 0.3)' },
    All:   { background: 'rgba(0, 230, 118, 0.1)', color: 'var(--green-bright)', border: '1px solid var(--green-bright)', glow: '0 0 14px rgba(0, 230, 118, 0.3)' },
  }
  const s = styles[surface] || styles.All
  return { background: s.background, color: s.color, border: s.border, boxShadow: s.glow }
}

// Archetype style and icon
const ARCHETYPE_STYLES = {
  'Big Server':            { bg: 'linear-gradient(135deg, #5a0a14, #ff3b5c)', glow: 'rgba(255, 59, 92, 0.4)', icon: '⚡' },
  'Precision Baseliner':   { bg: 'linear-gradient(135deg, #0a1a4a, #6b9fff)', glow: 'rgba(107, 159, 255, 0.4)', icon: '◎' },
  'Counterpuncher':        { bg: 'linear-gradient(135deg, #5a3800, #FFB300)', glow: 'rgba(255, 179, 0, 0.4)', icon: '🛡' },
  'All-Court Player':      { bg: 'linear-gradient(135deg, #00FF87, #6b9fff, #FFB300)', glow: 'rgba(0, 230, 118, 0.4)', icon: '✦' },
}

function ArchetypeBadge({ archetype }) {
  if (!archetype) return null
  const s = ARCHETYPE_STYLES[archetype] || { bg: 'rgba(255,255,255,0.05)', glow: 'rgba(255,255,255,0.15)', icon: '◉' }
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 8,
      padding: '8px 16px', borderRadius: 999,
      background: s.bg,
      color: '#fff',
      fontFamily: '"Barlow Condensed", sans-serif',
      fontWeight: 900, fontSize: 13,
      letterSpacing: 1.8, textTransform: 'uppercase',
      boxShadow: `0 6px 18px ${s.glow}`,
      textShadow: '0 1px 2px rgba(0,0,0,0.3)',
    }}>
      <span style={{ fontSize: 16 }}>{s.icon}</span>
      {archetype}
    </span>
  )
}

function StatTable({ stats, tour }) {
  const avgs = tour === 'WTA' ? WTA_AVERAGES : ATP_AVERAGES
  const isHigherBetter = (k) => !['double_faults'].includes(k)

  return (
    <div className="glass-card" style={{ overflowX: 'auto', padding: '6px 12px' }}>
      <table className="baseline-table">
        <thead>
          <tr>
            <th>Stat</th>
            {SURFACES.map(s => (
              <th key={s} style={{ color: SURFACE_HEADER_COLORS[s] }}>{s}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {STAT_KEYS.map(key => {
            const avg = avgs[key]
            return (
              <tr key={key}>
                <td style={{ color: 'var(--muted)', whiteSpace: 'nowrap', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700 }}>{STAT_LABELS[key]}</td>
                {SURFACES.map(s => {
                  const v = stats[s]?.[key]
                  const isPct = key.includes('pct') || key.includes('won') || key.includes('converted') || key.includes('saved')
                  const display = v == null ? '—' : isPct ? fmtPct(v) : fmt(v)
                  let color = '#fff'
                  let arrow = null
                  if (v != null && avg != null) {
                    const better = v > avg
                    const above = isHigherBetter(key) ? better : !better
                    color = above ? 'var(--green-bright)' : 'var(--red-bright)'
                    arrow = above ? '▲' : '▼'
                  }
                  return (
                    <td key={s} style={{ color, fontWeight: 800, fontFamily: '"Barlow Condensed", sans-serif' }}>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                        {arrow && <span style={{ fontSize: 9, opacity: 0.85 }}>{arrow}</span>}
                        {display}
                      </span>
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// Last 10 form dots — larger with glow + tooltip
function FormDots({ form }) {
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      {form.slice(0, 10).map((m, i) => (
        <motion.div
          key={i}
          initial={{ scale: 0, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ delay: i * 0.05, type: 'spring', stiffness: 380 }}
          title={`${m.won ? 'W' : 'L'} vs ${m.opponent} (${m.surface})${m.score ? ' — ' + m.score : ''}`}
          style={{
            width: 18, height: 18, borderRadius: '50%',
            background: m.won
              ? 'radial-gradient(circle at 35% 35%, #00FF87, #00A854)'
              : 'radial-gradient(circle at 35% 35%, #FF6B7A, #B0223A)',
            boxShadow: m.won
              ? '0 0 12px rgba(0, 230, 118, 0.55), 0 0 0 1px rgba(0, 230, 118, 0.3) inset'
              : '0 0 12px rgba(255, 68, 68, 0.5), 0 0 0 1px rgba(255, 68, 68, 0.3) inset',
            cursor: 'pointer',
          }}
        />
      ))}
    </div>
  )
}

function WinRateSparkline({ matches }) {
  if (!matches || matches.length < 5) return null
  const sorted = [...matches].sort((a, b) => a.timestamp - b.timestamp)
  const data = sorted.map((_, i) => {
    const window = sorted.slice(Math.max(0, i - 19), i + 1)
    const wr = window.filter(m => m.won).length / window.length * 100
    return { i, wr: Math.round(wr) }
  }).slice(-20)

  return (
    <div style={{ height: 140 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          <defs>
            <linearGradient id="sa-line" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="#00FF87" />
              <stop offset="100%" stopColor="#00A854" />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis dataKey="i" hide />
          <YAxis domain={[0, 100]} tick={{ fill: 'var(--muted)', fontSize: 11 }} />
          <Tooltip
            formatter={v => [`${v}%`, 'Win Rate']}
            contentStyle={{ background: 'rgba(8, 13, 9, 0.95)', border: '1px solid var(--card-border)', borderRadius: 8 }}
          />
          <Line type="monotone" dataKey="wr"
            stroke="url(#sa-line)" strokeWidth={3}
            dot={false}
            isAnimationActive
            filter="drop-shadow(0 0 6px rgba(0, 230, 118, 0.4))"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

/**
 * Horizontal bar chart with animated fill from left to right.
 * Each row is one stat × one surface, color-coded vs the tour average.
 */
function HorizontalSurfaceBars({ stats, tour }) {
  const avgs = tour === 'WTA' ? WTA_AVERAGES : ATP_AVERAGES
  const keys = ['aces', 'double_faults', 'bp_converted']
  const labels = { aces: 'Aces/Match', double_faults: 'DFs/Match', bp_converted: 'BP Conversion %' }
  const surfaces = ['Hard', 'Clay', 'Grass']
  const isHigherBetter = (k) => !['double_faults'].includes(k)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      {keys.map(k => {
        // Find max for scaling
        const vals = surfaces.map(s => stats[s]?.[k]).filter(v => v != null)
        const maxV = Math.max(...vals, avgs[k] || 0) * 1.15

        return (
          <div key={k}>
            <div style={{
              fontFamily: '"Barlow Condensed", sans-serif',
              fontWeight: 800, fontSize: 11, letterSpacing: 1.5,
              color: 'var(--green-mid)', textTransform: 'uppercase',
              marginBottom: 10,
            }}>{labels[k]}</div>

            {surfaces.map(s => {
              const v = stats[s]?.[k]
              if (v == null) return null
              const pct = (v / maxV) * 100
              const better = isHigherBetter(k) ? v > avgs[k] : v < avgs[k]
              const color = better ? 'var(--green-bright)' : 'var(--red-bright)'
              const gradient = better
                ? 'linear-gradient(90deg, #00A854, #00FF87)'
                : 'linear-gradient(90deg, #B0223A, #FF3B5C)'
              const glow = better ? 'rgba(0, 230, 118, 0.35)' : 'rgba(255, 68, 68, 0.35)'

              return (
                <div key={s} style={{
                  display: 'grid',
                  gridTemplateColumns: '70px 1fr 60px',
                  alignItems: 'center', gap: 12,
                  marginBottom: 8,
                }}>
                  <div style={{
                    fontFamily: '"Barlow Condensed", sans-serif',
                    fontWeight: 700, fontSize: 11,
                    color: SURFACE_HEADER_COLORS[s] || '#888',
                    letterSpacing: 1.5, textTransform: 'uppercase',
                  }}>{s}</div>
                  <div style={{
                    position: 'relative', height: 12,
                    background: 'rgba(255, 255, 255, 0.04)',
                    borderRadius: 6, overflow: 'hidden',
                  }}>
                    <motion.div
                      initial={{ width: 0 }}
                      animate={{ width: `${pct}%` }}
                      transition={{ duration: 0.7, ease: 'easeOut' }}
                      style={{
                        height: '100%',
                        background: gradient,
                        boxShadow: `0 0 10px ${glow}`,
                        borderRadius: 6,
                      }}
                    />
                  </div>
                  <div style={{
                    textAlign: 'right',
                    fontFamily: '"Barlow Condensed", sans-serif',
                    fontWeight: 900, fontSize: 14,
                    color,
                  }}>
                    {k.includes('converted') ? fmtPct(v) : fmt(v)}
                  </div>
                </div>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}

function MatchHistory({ allMatches }) {
  const [surface, setSurface] = useState('All')
  const [page, setPage]       = useState(0)
  const PAGE = 20

  const filtered = surface === 'All' ? allMatches : allMatches.filter(m => m.surface === surface)
  const pages    = Math.ceil(filtered.length / PAGE)
  const rows     = filtered.slice(page * PAGE, page * PAGE + PAGE)

  return (
    <div>
      <div style={{ display: 'flex', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
        {['All','Hard','Clay','Grass'].map(s => (
          <motion.button
            key={s}
            whileTap={{ scale: 0.95 }}
            onClick={() => { setSurface(s); setPage(0) }}
            style={{
              padding: '8px 18px', borderRadius: 999, fontSize: 11, cursor: 'pointer',
              fontWeight: 800, fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 1.8,
              textTransform: 'uppercase',
              ...surfaceTabStyle(s, surface === s),
            }}
          >{s}</motion.button>
        ))}
      </div>
      <div className="glass-card" style={{ overflowX: 'auto', padding: '4px 10px' }}>
        <table className="baseline-table">
          <thead><tr>
            <th>Date</th><th>Tournament</th><th>Surface</th><th>Result</th><th>Opponent</th><th>Score</th>
          </tr></thead>
          <tbody>
            {rows.map((m, i) => (
              <tr key={i}>
                <td style={{ color: 'var(--muted)', whiteSpace: 'nowrap', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 11, letterSpacing: 1 }}>{m.date || '—'}</td>
                <td style={{ color: 'rgba(255,255,255,0.75)', fontSize: 13 }}>{m.tournament}</td>
                <td><SurfaceBadge surface={m.surface} /></td>
                <td><ResultPill result={m.won ? 'W' : 'L'} /></td>
                <td style={{ color: '#fff', fontWeight: 600 }}>{m.opponent_name}</td>
                <td style={{ fontVariantNumeric: 'tabular-nums', color: 'rgba(255,255,255,0.7)', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700 }}>{m.score || '—'}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={6} style={{ color: 'var(--muted)', textAlign: 'center', padding: 24 }}>No matches</td></tr>}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'center' }}>
          {Array.from({ length: pages }, (_, i) => (
            <button key={i} onClick={() => setPage(i)} style={{
              width: 36, height: 36, borderRadius: 8, cursor: 'pointer',
              background: page === i ? 'var(--green-bright)' : 'rgba(255, 255, 255, 0.025)',
              color: page === i ? '#000' : 'var(--muted)',
              border: `1px solid ${page === i ? 'var(--green-bright)' : 'var(--card-border)'}`,
              fontSize: 12, fontWeight: 800, fontFamily: '"Barlow Condensed", sans-serif',
              boxShadow: page === i ? '0 0 10px rgba(0, 230, 118, 0.3)' : 'none',
              transition: 'all .2s',
            }}>{i + 1}</button>
          ))}
        </div>
      )}
    </div>
  )
}

function HandBadge({ hand }) {
  if (!hand) return null
  return (
    <span style={{
      display: 'inline-block', fontSize: 10, fontWeight: 800,
      padding: '3px 10px', borderRadius: 999, marginLeft: 8,
      verticalAlign: 'middle', letterSpacing: 1,
      background: hand === 'L' ? 'var(--green-bright)' : 'rgba(255,255,255,0.05)',
      color:      hand === 'L' ? '#000' : 'var(--muted)',
      border: `1px solid ${hand === 'L' ? 'var(--green-bright)' : 'rgba(255,255,255,0.12)'}`,
      textTransform: 'uppercase',
    }}>{hand === 'L' ? 'Left' : 'Right'}</span>
  )
}

function TaSurfacePanel({ taStats, surface }) {
  if (!taStats?.surface_stats) return null
  const surf = taStats.surface_stats[surface] || taStats.surface_stats['All']
  if (!surf || !surf.matches) return null

  const rows = [
    ['Ace %',          surf.ace_pct != null    ? surf.ace_pct.toFixed(1)    + '%' : '—'],
    ['DF %',           surf.df_pct != null     ? surf.df_pct.toFixed(1)     + '%' : '—'],
    ['1st In %',       surf.first_in_pct != null  ? surf.first_in_pct.toFixed(1)  + '%' : '—'],
    ['1st Serve Won',  surf.first_won_pct != null ? surf.first_won_pct.toFixed(1) + '%' : '—'],
    ['2nd Serve Won',  surf.second_won_pct != null? surf.second_won_pct.toFixed(1)+ '%' : '—'],
    ['BP Saved %',     surf.bp_saved_pct != null  ? surf.bp_saved_pct.toFixed(1)  + '%' : '—'],
    ['BP Conv vs opp', surf.bp_conv_pct != null   ? surf.bp_conv_pct.toFixed(1)   + '%' : '—'],
    ['Matches (TA)',   surf.matches],
  ]

  return (
    <div className="glass-card" style={{ padding: '18px 22px', marginBottom: 18 }}>
      <div style={{
        fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800,
        fontSize: 10, letterSpacing: '0.3em',
        color: 'var(--green-bright)', textTransform: 'uppercase', marginBottom: 14,
      }}>
        Tennis Abstract — {surface} Data ({surf.matches} matches)
      </div>
      {rows.map(([lbl, val], i) => (
        <div key={lbl} style={{
          display: 'flex', justifyContent: 'space-between',
          padding: '7px 8px',
          borderBottom: '1px solid rgba(13, 21, 16, 0.6)',
          fontSize: 13,
          background: i % 2 === 1 ? 'rgba(0, 230, 118, 0.015)' : 'transparent',
          marginLeft: -8, marginRight: -8,
        }}>
          <span style={{ color: 'var(--muted)', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700 }}>{lbl}</span>
          <span style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, fontSize: 14, color: '#fff' }}>{val}</span>
        </div>
      ))}
    </div>
  )
}

const SURFACE_COLORS = {
  Hard:  '#6b9fff',
  Clay:  '#ff6b35',
  Grass: '#00e676',
  All:   '#00e676',
}

function WinRateCards({ stats, taStats }) {
  let bestSurface = null
  let bestWr = -1
  for (const s of ['Hard', 'Clay', 'Grass']) {
    const wr = stats[s]?.win_rate
    if (wr != null && wr > bestWr) { bestWr = wr; bestSurface = s }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 14 }}>
      {SURFACES.map(s => {
        const d = stats[s] || {}
        const wr = d.win_rate
        const mp = d.matches_played || 0
        const isBest = s === bestSurface
        const surfColor = SURFACE_COLORS[s] || '#00e676'

        const taSurf = taStats?.surface_stats?.[s === 'All' ? 'All' : s]
        const taM = taSurf?.matches || 0
        const taThin = s !== 'All' && taM < 5 && taM > 0

        return (
          <div
            key={s}
            className="glass-card"
            style={{
              padding: '18px 20px',
              borderColor: isBest ? surfColor : undefined,
              boxShadow: isBest
                ? `0 6px 24px ${surfColor}33, 0 0 0 1px ${surfColor}66 inset`
                : undefined,
            }}
          >
            <div style={{
              fontFamily: '"Barlow Condensed", sans-serif',
              fontSize: 10, fontWeight: 800, letterSpacing: 2,
              color: isBest ? surfColor : 'var(--green-mid)',
              textTransform: 'uppercase', marginBottom: 6,
            }}>
              {s === 'All' ? 'All Surfaces' : s}
            </div>
            <div style={{
              fontSize: isBest ? 52 : 38,
              fontWeight: 900,
              fontFamily: '"Barlow Condensed", sans-serif',
              color: wr > 55 ? 'var(--green-bright)' : wr < 45 ? 'var(--red-bright)' : '#fff',
              lineHeight: 1.05,
              textShadow: isBest ? `0 0 18px ${surfColor}55` : 'none',
            }}>
              {wr != null ? (
                <><NumberFlow value={Math.round(wr)} />%</>
              ) : '—'}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 6, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 10, color: 'var(--muted)', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, letterSpacing: 0.5 }}>SS {mp}</span>
              {taM > 0 && (
                <span style={{ fontSize: 10, color: 'var(--muted)', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, letterSpacing: 0.5 }}>· TA {taM}</span>
              )}
            </div>
            {taThin && (
              <div style={{ fontSize: 9, color: 'var(--amber)', fontWeight: 700, marginTop: 4, fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 0.5 }}>
                ⚠ Limited surface data
              </div>
            )}
            {isBest && (
              <div style={{
                fontSize: 9, color: surfColor, fontWeight: 800, marginTop: 8,
                textTransform: 'uppercase', letterSpacing: 1.5,
                borderRadius: 999, padding: '3px 10px',
                background: surfColor + '22', border: `1px solid ${surfColor}66`,
                display: 'inline-block', fontFamily: '"Barlow Condensed", sans-serif',
              }}>
                ★ Best Surface
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

export default function SurfaceAnalyzer({ tour }) {
  const [player, setPlayer] = useState(null)
  const [activeSurface, setActiveSurface] = useState('Hard')
  const { stats, loading, error } = usePlayerStats(player?.id, tour, player?.name || '')

  const lastMatchTs    = stats?.all_matches?.[0]?.timestamp || 0
  const lastMatchDate  = lastMatchTs > 0 ? new Date(lastMatchTs * 1000) : null
  const daysSinceLast  = lastMatchDate
    ? Math.floor((Date.now() - lastMatchDate.getTime()) / 86400000)
    : null
  const lastMatchLabel = lastMatchDate
    ? lastMatchDate.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    : null
  const isStale    = daysSinceLast != null && daysSinceLast > 7
  const isInactive = daysSinceLast != null && daysSinceLast > 21

  return (
    <div>
      <div style={{ marginBottom: 22 }}>
        <PlayerSearch tour={tour} label="Search player…" selected={player} onSelect={setPlayer} />
      </div>

      {!player && (
        <div className="glass-card" style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--muted)' }}>
          Search for a player to view surface analytics
        </div>
      )}

      {loading && <LoadingSpinner message="Fetching player data" />}
      {error && <div className="glass-card" style={{ color: 'var(--red-bright)', padding: 20, borderColor: 'rgba(255, 68, 68, 0.3)' }}>Error: {error}</div>}

      {stats && !loading && (
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
          {/* Player header */}
          <div className="glass-card" style={{ padding: '22px 24px', marginBottom: 18 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 12, flexWrap: 'wrap' }}>
              <h2 style={{
                margin: 0, fontSize: 32, fontWeight: 900,
                fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 1,
                color: '#fff',
              }}>{player.name}</h2>
              <ArchetypeBadge archetype={stats.archetype} />
              <HandBadge hand={stats.ta_stats?.handedness} />
              {lastMatchLabel && (
                <span style={{
                  fontSize: 10, fontWeight: 700, fontFamily: '"Barlow Condensed", sans-serif',
                  letterSpacing: 1, padding: '4px 11px', borderRadius: 999,
                  background: isStale ? 'rgba(255, 179, 0, 0.08)' : 'rgba(255, 255, 255, 0.03)',
                  color: isStale ? 'var(--amber)' : 'var(--muted)',
                  border: `1px solid ${isStale ? 'rgba(255, 179, 0, 0.3)' : 'var(--card-border)'}`,
                  textTransform: 'uppercase',
                }}>
                  Last match: {lastMatchLabel}
                </span>
              )}
            </div>

            {isInactive && (
              <div style={{
                display: 'flex', alignItems: 'center', gap: 10,
                background: 'rgba(255, 179, 0, 0.06)', border: '1px solid rgba(255, 179, 0, 0.3)',
                borderRadius: 10, padding: '10px 16px', marginBottom: 8,
              }}>
                <span style={{ fontSize: 16, color: 'var(--amber)' }}>⚠</span>
                <span style={{
                  fontSize: 12, fontWeight: 700, color: 'var(--amber)',
                  fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 0.5,
                }}>
                  Player may be inactive or injured — last match was {daysSinceLast} days ago
                </span>
              </div>
            )}

            <div style={{
              fontFamily: '"Barlow Condensed", sans-serif',
              fontSize: 11, fontWeight: 800, letterSpacing: 2,
              color: 'var(--green-mid)', textTransform: 'uppercase',
              marginBottom: 8, marginTop: 6,
            }}>Form — Last 10</div>
            <FormDots form={stats.form || []} />
          </div>

          <SectionDivider label="Win Rate by Surface" />
          <WinRateCards stats={stats} taStats={stats.ta_stats} />

          <SectionDivider label="Surface Stats" />
          <StatTable stats={stats} tour={tour} />

          {stats.ta_stats && (
            <>
              <div style={{ display: 'flex', gap: 10, margin: '18px 0 14px', flexWrap: 'wrap' }}>
                {['Hard', 'Clay', 'Grass', 'All'].map(s => (
                  <motion.button
                    key={s}
                    whileTap={{ scale: 0.95 }}
                    onClick={() => setActiveSurface(s)}
                    style={{
                      padding: '8px 18px', borderRadius: 999, fontSize: 11, cursor: 'pointer',
                      fontWeight: 800, fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 1.8,
                      textTransform: 'uppercase',
                      ...surfaceTabStyle(s, activeSurface === s),
                    }}
                  >{s}</motion.button>
                ))}
              </div>
              <TaSurfacePanel taStats={stats.ta_stats} surface={activeSurface} />
            </>
          )}

          <SectionDivider label="Rolling Win Rate (last 20 matches)" />
          <div className="glass-card" style={{ padding: '20px 18px' }}>
            <WinRateSparkline matches={stats.all_matches || []} />
          </div>

          <SectionDivider label="Surface Comparison — Aces · DFs · BP Conversion" />
          <div className="glass-card" style={{ padding: '22px 24px', marginBottom: 18 }}>
            <HorizontalSurfaceBars stats={stats} tour={tour} />
          </div>

          <SectionDivider label="Match History" />
          <MatchHistory allMatches={stats.all_matches || []} />
        </motion.div>
      )}
    </div>
  )
}
