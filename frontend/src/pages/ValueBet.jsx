import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import PlayerSearch from '../components/PlayerSearch'
import LoadingSpinner from '../components/LoadingSpinner'
import StatCard from '../components/StatCard'
import { fetchStats } from '../utils/api'
import { fmt, fmtPct } from '../utils/constants'

const SURFACES = ['Hard', 'Clay', 'Grass']

function impliedProb(odds) {
  if (!odds || isNaN(odds)) return null
  const n = parseFloat(odds)
  if (n > 0) return 100 / (n + 100)
  if (n < 0) return (-n) / (-n + 100)
  return null
}

function modelWinProb(p1Stats, p2Stats) {
  const wr1 = p1Stats?.win_rate ?? 50
  const wr2 = p2Stats?.win_rate ?? 50
  const total = wr1 + wr2
  return total > 0 ? (wr1 / total) * 100 : 50
}

export default function ValueBet({ tour }) {
  const [p1,      setP1]      = useState(null)
  const [p2,      setP2]      = useState(null)
  const [surface, setSurface] = useState('Hard')
  const [odds1,   setOdds1]   = useState('')
  const [odds2,   setOdds2]   = useState('')
  const [p1Stats, setP1Stats] = useState(null)
  const [p2Stats, setP2Stats] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!p1 || !p2) return
    setLoading(true)
    Promise.all([fetchStats(String(p1.id), tour), fetchStats(String(p2.id), tour)])
      .then(([s1, s2]) => { setP1Stats(s1); setP2Stats(s2) })
      .finally(() => setLoading(false))
  }, [p1, p2, tour])

  const s1 = p1Stats?.[surface] || p1Stats?.All || {}
  const s2 = p2Stats?.[surface] || p2Stats?.All || {}
  const arch1 = p1Stats?.archetype
  const arch2 = p2Stats?.archetype
  const modelP1 = p1Stats && p2Stats ? modelWinProb(s1, s2) : null
  const modelP2 = modelP1 != null ? 100 - modelP1 : null

  const book1 = impliedProb(odds1) ? impliedProb(odds1) * 100 : null
  const book2 = impliedProb(odds2) ? impliedProb(odds2) * 100 : null
  const edge1 = modelP1 != null && book1 != null ? modelP1 - book1 : null
  const edge2 = modelP2 != null && book2 != null ? modelP2 - book2 : null

  const section = (t) => (
    <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.08em', marginBottom: 12, marginTop: 24, paddingBottom: 6, borderBottom: '1px solid var(--border)' }}>{t}</div>
  )

  return (
    <div>
      {section('Players')}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
        <PlayerSearch tour={tour} label="Player 1" selected={p1} onSelect={p => { setP1(p); setP1Stats(null) }} />
        <PlayerSearch tour={tour} label="Player 2" selected={p2} onSelect={p => { setP2(p); setP2Stats(null) }} />
      </div>

      {section('Surface')}
      <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
        {SURFACES.map(s => (
          <button key={s} onClick={() => setSurface(s)} style={{
            padding: '6px 16px', borderRadius: 20, fontSize: 12, cursor: 'pointer',
            background: surface === s ? 'var(--green)' : 'var(--card)',
            color: surface === s ? '#000' : 'var(--muted)',
            border: `1px solid ${surface === s ? 'var(--green)' : 'var(--border)'}`,
            fontWeight: surface === s ? 700 : 400,
          }}>{s}</button>
        ))}
      </div>

      {!p1 && !p2 && <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--muted)' }}>Select two players to analyze value</div>}
      {loading && <LoadingSpinner message="Fetching player data…" />}

      {p1Stats && p2Stats && !loading && (
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}>
          {/* Archetypes */}
          {section('Player Archetypes')}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
            {[[p1, arch1, s1], [p2, arch2, s2]].map(([pl, arch, s], idx) => (
              <div key={idx} style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 10, padding: 16 }}>
                <div style={{ fontWeight: 700, marginBottom: 8 }}>{pl?.name}</div>
                {arch && <span style={{ padding: '3px 10px', borderRadius: 12, fontSize: 11, fontWeight: 700, background: '#00E67622', color: 'var(--green)', border: '1px solid #00E67644' }}>{arch}</span>}
                <div style={{ marginTop: 12, fontSize: 12 }}>
                  {[['Win Rate', fmtPct(s.win_rate)],['Aces/M', fmt(s.aces)],['1st Srv Won', fmtPct(s.first_serve_pts_won)],['BP Conv', fmtPct(s.bp_converted)]].map(([lbl, val]) => (
                    <div key={lbl} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid #151515' }}>
                      <span style={{ color: 'var(--muted)' }}>{lbl}</span>
                      <span style={{ fontWeight: 600 }}>{val}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {/* Model win probability */}
          {section('Model Win Probability')}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
            <StatCard label={`${p1?.name} — Model`} value={`${modelP1?.toFixed(1)}%`} color={modelP1 > 55 ? 'green' : modelP1 < 45 ? 'red' : undefined} />
            <StatCard label={`${p2?.name} — Model`} value={`${modelP2?.toFixed(1)}%`} color={modelP2 > 55 ? 'green' : modelP2 < 45 ? 'red' : undefined} />
          </div>

          {/* Odds input */}
          {section('Sportsbook Implied Odds (American)')}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
            {[[p1, odds1, setOdds1, book1, edge1], [p2, odds2, setOdds2, book2, edge2]].map(([pl, odds, setOdds, book, edge], idx) => (
              <div key={idx} style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 10, padding: 16 }}>
                <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 8 }}>{pl?.name}</div>
                <input
                  type="number" value={odds} placeholder="+150 or -180"
                  onChange={e => setOdds(e.target.value)}
                  style={{ width: '100%', padding: '8px 12px', background: '#0a0a0a', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--white)', fontSize: 14, marginBottom: 10 }}
                />
                {book != null && (
                  <div style={{ fontSize: 12 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                      <span style={{ color: 'var(--muted)' }}>Book implied</span>
                      <span style={{ fontWeight: 600 }}>{book.toFixed(1)}%</span>
                    </div>
                    {edge != null && (
                      <div style={{ marginTop: 8, padding: '10px 12px', borderRadius: 8,
                        background: edge > 5 ? '#00E67615' : '#1a1a1a',
                        border: `1px solid ${edge > 5 ? 'var(--green)' : 'var(--border)'}`,
                      }}>
                        <div style={{ fontSize: 11, fontWeight: 700, color: edge > 5 ? 'var(--green)' : 'var(--muted)', marginBottom: 2 }}>
                          {edge > 5 ? '✓ VALUE' : 'NO VALUE'}
                        </div>
                        <div style={{ fontSize: 13, fontWeight: 800, color: edge > 5 ? 'var(--green)' : 'var(--muted)' }}>
                          Edge: {edge >= 0 ? '+' : ''}{edge.toFixed(1)}%
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </motion.div>
      )}
    </div>
  )
}
