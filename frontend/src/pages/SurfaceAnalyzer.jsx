import { useState } from 'react'
import { motion } from 'motion/react'
import NumberFlow from '@number-flow/react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  LineChart, Line, CartesianGrid,
} from 'recharts'
import PlayerSearch from '../components/PlayerSearch'
import StatCard from '../components/StatCard'
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

// ── Change 12: Surface-specific column header colors ────────────────────────
const SURFACE_HEADER_COLORS = {
  All:   '#aaa',
  Hard:  '#6b9fff',
  Clay:  '#ff6b35',
  Grass: '#00e676',
}

// ── Change 4: Surface-specific tab styles ───────────────────────────────────
function surfaceTabStyle(surface, isActive) {
  if (!isActive) return { background: 'transparent', color: '#2a3a30', border: '1px solid #1a2520' }
  const styles = {
    Hard:  { background: '#001a40', color: '#6b9fff', border: '1px solid #2a3d5a' },
    Clay:  { background: '#2a0800', color: '#ff6b35', border: '1px solid #5a2010' },
    Grass: { background: '#001a0b', color: '#00e676', border: '1px solid #1a4020' },
    All:   { background: '#0a0f0c', color: '#00e676', border: '1px solid #00e676' },
  }
  return styles[surface] || styles.All
}

function StatTable({ stats, tour }) {
  const avgs = tour === 'WTA' ? WTA_AVERAGES : ATP_AVERAGES
  const isHigherBetter = (k) => !['double_faults'].includes(k)

  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="baseline-table">
        <thead>
          <tr>
            <th>Stat</th>
            {/* ── Change 12: Colored surface column headers ── */}
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
                <td style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{STAT_LABELS[key]}</td>
                {SURFACES.map(s => {
                  const v = stats[s]?.[key]
                  const isPct = key.includes('pct') || key.includes('won') || key.includes('converted') || key.includes('saved')
                  const display = v == null ? '—' : isPct ? fmtPct(v) : fmt(v)
                  let color = 'var(--white)'
                  if (v != null && avg != null) {
                    const better = v > avg
                    color = (isHigherBetter(key) ? better : !better) ? 'var(--green)' : 'var(--red)'
                  }
                  return <td key={s} style={{ color, fontWeight: 600 }}>{display}</td>
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Change 9: Form dots with glow on win dots (Phase 10: staggered reveal) ──
function FormDots({ form }) {
  return (
    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
      {form.slice(0, 10).map((m, i) => (
        <motion.div
          key={i}
          initial={{ scale: 0, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ delay: i * 0.05, type: 'spring', stiffness: 400 }}
          title={`${m.won ? 'W' : 'L'} vs ${m.opponent} (${m.surface})`}
          style={{
            width: 12, height: 12, borderRadius: '50%',
            background: m.won ? 'var(--green)' : 'var(--red)',
            boxShadow: m.won ? '0 0 6px rgba(0, 230, 118, 0.5)' : undefined,
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
    <div style={{ height: 120 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e1e1e" />
          <XAxis dataKey="i" hide />
          <YAxis domain={[0, 100]} tick={{ fill: 'var(--muted)', fontSize: 11 }} />
          <Tooltip formatter={v => [`${v}%`, 'Win Rate']} contentStyle={{ background: '#1a1a1a', border: '1px solid var(--border)', borderRadius: 6 }} />
          <Line type="monotone" dataKey="wr" stroke="var(--green)" strokeWidth={2} dot={false} isAnimationActive />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function SurfaceBarChart({ stats, tour }) {
  const avgs = tour === 'WTA' ? WTA_AVERAGES : ATP_AVERAGES
  const keys = ['aces', 'double_faults', 'bp_converted']
  const labels = { aces: 'Aces', double_faults: 'DFs', bp_converted: 'BP Conv %' }
  const surfaces = ['Hard', 'Clay', 'Grass']

  const data = keys.map(k => {
    const row = { stat: labels[k], avg: avgs[k] }
    surfaces.forEach(s => { row[s] = stats[s]?.[k] ?? null })
    return row
  })

  const colors = { Hard: '#42A5F5', Clay: '#EF6C00', Grass: '#388E3C' }

  return (
    <div style={{ height: 200 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} barGap={4}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e1e1e" />
          <XAxis dataKey="stat" tick={{ fill: 'var(--muted)', fontSize: 12 }} />
          <YAxis tick={{ fill: 'var(--muted)', fontSize: 11 }} />
          <Tooltip contentStyle={{ background: '#1a1a1a', border: '1px solid var(--border)', borderRadius: 6 }} />
          {surfaces.map(s => <Bar key={s} dataKey={s} fill={colors[s]} radius={[3,3,0,0]} isAnimationActive />)}
        </BarChart>
      </ResponsiveContainer>
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
      {/* ── Change 4: surface-colored tabs ── */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        {['All','Hard','Clay','Grass'].map(s => (
          <button key={s} onClick={() => { setSurface(s); setPage(0) }} style={{
            padding: '6px 16px', borderRadius: 8, fontSize: 11, cursor: 'pointer',
            fontWeight: 700, fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 1.5,
            textTransform: 'uppercase',
            ...surfaceTabStyle(s, surface === s),
          }}>{s}</button>
        ))}
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table className="baseline-table">
          <thead><tr>
            <th>Date</th><th>Tournament</th><th>Surface</th><th>Result</th><th>Opponent</th><th>Score</th>
          </tr></thead>
          <tbody>
            {rows.map((m, i) => (
              <tr key={i}>
                <td style={{ color: '#2a3a30', whiteSpace: 'nowrap', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 11, letterSpacing: 1 }}>{m.date || '—'}</td>
                <td style={{ color: '#4a6a50', fontSize: 13 }}>{m.tournament}</td>
                <td><SurfaceBadge surface={m.surface} /></td>
                <td><ResultPill result={m.won ? 'W' : 'L'} /></td>
                <td>{m.opponent_name}</td>
                <td style={{ fontVariantNumeric: 'tabular-nums', color: '#3a5040', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 600 }}>{m.score || '—'}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={6} style={{ color: 'var(--muted)', textAlign: 'center', padding: 24 }}>No matches</td></tr>}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div style={{ display: 'flex', gap: 8, marginTop: 12, justifyContent: 'center' }}>
          {Array.from({ length: pages }, (_, i) => (
            <button key={i} onClick={() => setPage(i)} style={{
              width: 32, height: 32, borderRadius: 6, cursor: 'pointer',
              background: page === i ? 'var(--green)' : 'var(--card)',
              color: page === i ? '#000' : 'var(--muted)',
              border: `1px solid ${page === i ? 'var(--green)' : 'var(--border)'}`,
              fontSize: 12, fontWeight: 700,
            }}>{i + 1}</button>
          ))}
        </div>
      )}
    </div>
  )
}

// Handedness badge — R=gray, L=green
function HandBadge({ hand }) {
  if (!hand) return null
  return (
    <span style={{
      display: 'inline-block', fontSize: 9, fontWeight: 800,
      padding: '1px 6px', borderRadius: 4, marginLeft: 8,
      verticalAlign: 'middle', letterSpacing: '.05em',
      background: hand === 'L' ? 'var(--green)' : '#3a3a3a',
      color:      hand === 'L' ? '#000' : '#888',
      border: `1px solid ${hand === 'L' ? 'var(--green)' : '#555'}`,
    }}>{hand === 'L' ? 'Left-handed' : 'Right-handed'}</span>
  )
}

// TA surface stats panel shown below the Sofascore stat table
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
    ['Matches (TA)',    surf.matches],
  ]

  return (
    <div style={{ background: '#080d09', border: '1px solid #1a2520', borderRadius: 10, padding: '16px 18px', marginBottom: 16 }}>
      <div style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 9, letterSpacing: '0.3em', color: '#00e676', textTransform: 'uppercase', marginBottom: 12 }}>
        Tennis Abstract — {surface} Data ({surf.matches} matches)
      </div>
      {rows.map(([lbl, val]) => (
        <div key={lbl} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid #0d1510', fontSize: 12 }}>
          <span style={{ color: '#2a3a30', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 600 }}>{lbl}</span>
          <span style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 13 }}>{val}</span>
        </div>
      ))}
    </div>
  )
}

// ── Change 5: Win rate card with best-surface highlight ──────────────────────
const SURFACE_COLORS = {
  Hard:  '#6b9fff',
  Clay:  '#ff6b35',
  Grass: '#00e676',
  All:   '#00e676',
}
const SURFACE_BG_TINTS = {
  Hard:  'rgba(107,159,255,0.06)',
  Clay:  'rgba(255,107,53,0.06)',
  Grass: 'rgba(0,230,118,0.06)',
  All:   'rgba(0,230,118,0.06)',
}

function WinRateCards({ stats, taStats }) {
  // Find best surface by win_rate (exclude 'All', compare only Hard/Clay/Grass)
  let bestSurface = null
  let bestWr = -1
  for (const s of ['Hard', 'Clay', 'Grass']) {
    const wr = stats[s]?.win_rate
    if (wr != null && wr > bestWr) { bestWr = wr; bestSurface = s }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12 }}>
      {SURFACES.map(s => {
        const d = stats[s] || {}
        const wr = d.win_rate
        const mp = d.matches_played || 0
        const isBest = s === bestSurface
        const surfColor = SURFACE_COLORS[s] || '#00e676'
        const bgTint = SURFACE_BG_TINTS[s] || 'transparent'

        // TA career match count for this surface
        const taSurf = taStats?.surface_stats?.[s === 'All' ? 'All' : s]
        const taM = taSurf?.matches || 0
        const taThin = s !== 'All' && taM < 5 && taM > 0

        return (
          <div key={s} style={{
            background: isBest ? bgTint : '#0a0f0c',
            border: isBest ? `2px solid ${surfColor}` : '1px solid #1a2520',
            borderRadius: 10,
            padding: '14px 16px',
            transition: 'border .2s',
          }}>
            <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 4 }}>
              {s === 'All' ? 'All Surfaces' : s}
            </div>
            <div style={{
              fontSize: isBest ? '3.5rem' : '2.2rem',
              fontWeight: 900,
              fontFamily: '"Barlow Condensed", sans-serif',
              color: wr > 55 ? 'var(--green)' : wr < 45 ? 'var(--red)' : 'var(--white)',
              lineHeight: 1.1,
            }}>
              {wr != null ? (
                <><NumberFlow value={Math.round(wr)} />%</>
              ) : '—'}
            </div>
            {/* Match count row: SS recent + TA career */}
            <div style={{ display: 'flex', gap: 6, marginTop: 5, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 10, color: 'var(--muted)' }}>SS {mp}</span>
              {taM > 0 && (
                <span style={{ fontSize: 10, color: '#3a6a40' }}>· TA {taM}</span>
              )}
            </div>
            {/* Amber warning if thin TA data on this surface */}
            {taThin && (
              <div style={{ fontSize: 9, color: '#FFB300', fontWeight: 700, marginTop: 4, fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 0.5 }}>
                ⚠ Limited surface data
              </div>
            )}
            {isBest && (
              <div style={{ fontSize: 9, color: surfColor, fontWeight: 700, marginTop: 4, textTransform: 'uppercase', letterSpacing: '.08em', borderRadius: 4, padding: '2px 8px', background: surfColor + '22', border: '1px solid ' + surfColor + '44', display: 'inline-block' }}>
                Best Surface
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

  // Inactivity / freshness — computed at component level (not in IIFE)
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

  const section = (title) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16, margin: '20px 0 12px' }}>
      <div style={{ flex: 1, height: 1, background: '#0d1510' }} />
      <span style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 9, letterSpacing: '0.3em', textTransform: 'uppercase', color: '#1a2a1e', whiteSpace: 'nowrap' }}>{title}</span>
      <div style={{ flex: 1, height: 1, background: '#0d1510' }} />
    </div>
  )

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <PlayerSearch tour={tour} label="Search player…" selected={player} onSelect={setPlayer} />
      </div>

      {!player && (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--muted)' }}>
          Search for a player to view surface analytics
        </div>
      )}

      {loading && <LoadingSpinner message="Fetching player data…" />}

      {error && <div style={{ color: 'var(--red)', padding: 20 }}>Error: {error}</div>}

      {stats && !loading && (
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
          {/* Header */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8, flexWrap: 'wrap' }}>
            <h2 style={{ margin: 0, fontSize: 28, fontWeight: 900, fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 1 }}>{player.name}</h2>
            {stats.archetype && (
              <span style={{ padding: '3px 12px', borderRadius: 6, fontSize: 10, fontWeight: 700, fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 1, textTransform: 'uppercase', background: '#001a0b', color: '#00e676', border: '1px solid #1a4020' }}>
                {stats.archetype}
              </span>
            )}
            <HandBadge hand={stats.ta_stats?.handedness} />
            {lastMatchLabel && (
              <span style={{
                fontSize: 10, fontWeight: 700, fontFamily: '"Barlow Condensed", sans-serif',
                letterSpacing: 1, padding: '3px 10px', borderRadius: 6,
                background: isStale ? '#1a1000' : '#0a0f0c',
                color: isStale ? '#f5a623' : '#888',
                border: `1px solid ${isStale ? '#5a3800' : '#1a2520'}`,
              }}>
                Last match: {lastMatchLabel}
              </span>
            )}
          </div>

          {/* Inactivity warning — shown when last match was > 21 days ago */}
          {isInactive && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              background: '#1a1000', border: '1px solid #5a3800',
              borderRadius: 8, padding: '8px 14px', marginBottom: 12,
            }}>
              <span style={{ fontSize: 14 }}>⚠</span>
              <span style={{
                fontSize: 11, fontWeight: 700, color: '#f5a623',
                fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 0.5,
              }}>
                Player may be inactive or injured — last match was {daysSinceLast} days ago
              </span>
            </div>
          )}

          {section('Form (last 10)')}
          {/* ── Change 9: FormDots with glow ── */}
          <FormDots form={stats.form || []} />

          {section('Win Rate by Surface')}
          {/* ── Change 5: Win rate cards with best-surface highlight + TA match counts ── */}
          <WinRateCards stats={stats} taStats={stats.ta_stats} />

          {section('Surface Stats')}
          {/* ── Changes 3 & 12 applied inside StatTable ── */}
          <StatTable stats={stats} tour={tour} />

          {/* Tennis Abstract supplemental panel — surface selector */}
          {stats.ta_stats && (
            <>
              {/* ── Change 4: Surface-colored tab buttons ── */}
              <div style={{ display: 'flex', gap: 8, margin: '16px 0 10px', flexWrap: 'wrap' }}>
                {['Hard', 'Clay', 'Grass', 'All'].map(s => (
                  <button key={s} onClick={() => setActiveSurface(s)} style={{
                    padding: '6px 16px', borderRadius: 8, fontSize: 11, cursor: 'pointer',
                    fontWeight: 700, fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 1.5,
                    textTransform: 'uppercase',
                    ...surfaceTabStyle(s, activeSurface === s),
                  }}>{s}</button>
                ))}
              </div>
              <TaSurfacePanel taStats={stats.ta_stats} surface={activeSurface} />
            </>
          )}

          {section('Rolling Win Rate (last 20 matches)')}
          <div style={{ background: '#0a0f0c', border: '1px solid #1a2520', borderRadius: 12, padding: '18px 16px' }}>
            <WinRateSparkline matches={stats.all_matches || []} />
          </div>

          {section('Surface Comparison — Aces · DFs · BP Conversion')}
          <div style={{ background: '#0a0f0c', border: '1px solid #1a2520', borderRadius: 12, padding: '18px 16px' }}>
            <SurfaceBarChart stats={stats} tour={tour} />
          </div>

          {section('Match History')}
          <MatchHistory allMatches={stats.all_matches || []} />
        </motion.div>
      )}
    </div>
  )
}
