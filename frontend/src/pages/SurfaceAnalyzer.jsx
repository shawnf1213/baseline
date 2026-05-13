import { useState } from 'react'
import { motion } from 'framer-motion'
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

function StatTable({ stats, tour }) {
  const avgs = tour === 'WTA' ? WTA_AVERAGES : ATP_AVERAGES
  const isHigherBetter = (k) => !['double_faults'].includes(k)

  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="baseline-table">
        <thead>
          <tr>
            <th>Stat</th>
            {SURFACES.map(s => <th key={s}>{s}</th>)}
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

function FormDots({ form }) {
  return (
    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
      {form.slice(0, 10).map((m, i) => (
        <div key={i} title={`${m.won ? 'W' : 'L'} vs ${m.opponent} (${m.surface})`} style={{
          width: 12, height: 12, borderRadius: '50%',
          background: m.won ? 'var(--green)' : 'var(--red)',
        }} />
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
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        {['All','Hard','Clay','Grass'].map(s => (
          <button key={s} onClick={() => { setSurface(s); setPage(0) }} style={{
            padding: '5px 14px', borderRadius: 20, fontSize: 12, cursor: 'pointer',
            background: surface === s ? 'var(--green)' : 'var(--card)',
            color: surface === s ? '#000' : 'var(--muted)',
            border: `1px solid ${surface === s ? 'var(--green)' : 'var(--border)'}`,
            fontWeight: surface === s ? 700 : 400,
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
                <td style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{m.date || '—'}</td>
                <td>{m.tournament}</td>
                <td><SurfaceBadge surface={m.surface} /></td>
                <td><ResultPill result={m.won ? 'W' : 'L'} /></td>
                <td>{m.opponent_name}</td>
                <td style={{ fontVariantNumeric: 'tabular-nums', color: 'var(--muted)' }}>{m.score || '—'}</td>
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

export default function SurfaceAnalyzer({ tour }) {
  const [player, setPlayer] = useState(null)
  const { stats, loading, error } = usePlayerStats(player?.id, tour)

  const section = (title) => (
    <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.08em', marginBottom: 12, marginTop: 28, paddingBottom: 6, borderBottom: '1px solid var(--border)' }}>
      {title}
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
            <h2 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>{player.name}</h2>
            {stats.archetype && (
              <span style={{ padding: '3px 10px', borderRadius: 12, fontSize: 12, fontWeight: 700, background: '#00E67622', color: 'var(--green)', border: '1px solid #00E67644' }}>
                {stats.archetype}
              </span>
            )}
          </div>

          {section('Form (last 10)')}
          <FormDots form={stats.form || []} />

          {section('Win Rate by Surface')}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12 }}>
            {SURFACES.map(s => {
              const d = stats[s] || {}
              const wr = d.win_rate
              const mp = d.matches_played || 0
              return (
                <StatCard key={s} label={s === 'All' ? 'All Surfaces' : s}
                  value={wr != null ? `${wr.toFixed(0)}%` : '—'}
                  sub={`${mp} matches`}
                  color={wr > 55 ? 'green' : wr < 45 ? 'red' : undefined}
                />
              )
            })}
          </div>

          {section('Surface Stats')}
          <StatTable stats={stats} tour={tour} />

          {section('Rolling Win Rate (last 20 matches)')}
          <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 10, padding: 16 }}>
            <WinRateSparkline matches={stats.all_matches || []} />
          </div>

          {section('Surface Comparison — Aces · DFs · BP Conversion')}
          <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 10, padding: 16 }}>
            <SurfaceBarChart stats={stats} tour={tour} />
          </div>

          {section('Match History')}
          <MatchHistory allMatches={stats.all_matches || []} />
        </motion.div>
      )}
    </div>
  )
}
