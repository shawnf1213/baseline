import { useState, useEffect } from 'react'
import { motion } from 'motion/react'
import NumberFlow from '@number-flow/react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import PlayerSearch from '../components/PlayerSearch'
import LoadingSpinner from '../components/LoadingSpinner'
import SurfaceBadge from '../components/SurfaceBadge'
import { fetchH2H } from '../utils/api'
import { fmt } from '../utils/constants'

const SURFACES = ['All', 'Hard', 'Clay', 'Grass']

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

function TimelineChart({ matches }) {
  if (!matches?.length) return null
  const data = [...matches].reverse().map((m, i) => ({
    i, date: m['Match Date'],
    win: m.Result === 'W' ? 1 : 0,
    surface: m.Surface,
    score: m.Score,
    opp: m.Opponent,
  }))

  return (
    <div style={{ height: 140 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          <defs>
            <linearGradient id="h2h-line" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="#00FF87" />
              <stop offset="50%" stopColor="rgba(255,255,255,0.15)" />
              <stop offset="100%" stopColor="#FF3B5C" />
            </linearGradient>
          </defs>
          <XAxis dataKey="date" tick={{ fill: 'var(--muted)', fontSize: 10 }} />
          <YAxis domain={[-0.2, 1.2]} hide />
          <ReferenceLine y={0.5} stroke="rgba(255,255,255,0.08)" strokeDasharray="4 4" />
          <Tooltip
            contentStyle={{ background: 'rgba(8, 13, 9, 0.95)', border: '1px solid var(--card-border)', borderRadius: 8, backdropFilter: 'blur(10px)' }}
            formatter={(v, _, p) => [v === 1 ? 'Win' : 'Loss', `${p.payload.date} vs ${p.payload.opp}`]}
          />
          <Line dataKey="win"
            stroke="url(#h2h-line)" strokeWidth={2}
            dot={({ cx, cy, payload }) => (
              <g key={payload.i}>
                <circle cx={cx} cy={cy} r={8}
                  fill={payload.win ? 'rgba(0, 230, 118, 0.18)' : 'rgba(255, 68, 68, 0.18)'}
                  stroke="none" />
                <circle cx={cx} cy={cy} r={5}
                  fill={payload.win ? 'var(--green-bright)' : 'var(--red-bright)'}
                  stroke="none">
                  <animate attributeName="r" values="5;6;5" dur="2.4s" repeatCount="indefinite" />
                </circle>
              </g>
            )}
            isAnimationActive
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

export default function HeadToHead({ tour }) {
  const [p1, setP1] = useState(null)
  const [p2, setP2] = useState(null)
  const [surface,  setSurface]  = useState('All')
  const [h2h,      setH2H]      = useState(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState(null)

  useEffect(() => {
    if (!p1 || !p2) { setH2H(null); return }
    setLoading(true); setError(null)
    fetchH2H({
      player1_id: String(p1.id), player2_id: String(p2.id), tour,
      surface: surface === 'All' ? null : surface,
    })
      .then(setH2H)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [p1, p2, tour, surface])

  // Calculate win percentage split
  const winPctP1 = h2h && h2h.total > 0 ? (h2h.p1_wins / h2h.total) * 100 : 50
  const winPctP2 = 100 - winPctP1

  return (
    <div>
      <SectionDivider label="Players" />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14, marginBottom: 18 }}>
        <PlayerSearch tour={tour} label="Player 1" selected={p1} onSelect={p => { setP1(p); setH2H(null) }} />
        <PlayerSearch tour={tour} label="Player 2" selected={p2} onSelect={p => { setP2(p); setH2H(null) }} />
      </div>

      <SectionDivider label="Surface Filter" />
      <div style={{ display: 'flex', gap: 10, marginBottom: 22, flexWrap: 'wrap' }}>
        {SURFACES.map(s => {
          const active = surface === s
          return (
            <motion.button
              key={s}
              whileTap={{ scale: 0.94 }}
              onClick={() => setSurface(s)}
              style={{
                padding: '8px 18px', borderRadius: 999, fontSize: 12, cursor: 'pointer',
                background: active ? 'var(--green-bright)' : 'rgba(255, 255, 255, 0.025)',
                color: active ? '#000' : 'var(--muted)',
                border: `1px solid ${active ? 'var(--green-bright)' : 'var(--card-border)'}`,
                fontWeight: 800,
                fontFamily: '"Barlow Condensed", sans-serif',
                letterSpacing: 2,
                textTransform: 'uppercase',
                boxShadow: active ? '0 0 14px rgba(0, 230, 118, 0.3)' : 'none',
                transition: 'all .2s',
              }}
            >{s}</motion.button>
          )
        })}
      </div>

      {!p1 && !p2 && (
        <div className="glass-card" style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--muted)' }}>
          Select two players to view H2H record
        </div>
      )}
      {loading && <LoadingSpinner message="Loading H2H data" />}
      {error   && <div className="glass-card" style={{ color: 'var(--red-bright)', padding: 16, borderColor: 'rgba(255, 68, 68, 0.3)' }}>Error: {error}</div>}

      {h2h && !loading && (
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
        >
          {/* H2H Record — enormous numbers */}
          <SectionDivider label="Overall H2H Record" />
          {h2h.total === 0 ? (
            <div className="glass-card" style={{ padding: '40px 20px', textAlign: 'center', color: 'var(--muted)' }}>
              No H2H data available
            </div>
          ) : (
            <div className="glass-card" style={{ padding: '32px 28px', marginBottom: 18 }}>
              <div style={{
                display: 'grid', gridTemplateColumns: '1fr auto 1fr',
                alignItems: 'center', gap: 24, marginBottom: 20,
              }}>
                {/* Player 1 — green */}
                <div style={{ textAlign: 'center' }}>
                  <div style={{
                    fontSize: 12, color: 'var(--muted)',
                    fontFamily: '"Barlow Condensed", sans-serif',
                    fontWeight: 700, letterSpacing: 2, textTransform: 'uppercase',
                    marginBottom: 8,
                  }}>{p1?.name}</div>
                  <motion.div
                    initial={{ scale: 0.4, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    transition={{ type: 'spring', stiffness: 200, damping: 18, delay: 0.1 }}
                    style={{
                      fontSize: 88, fontWeight: 900,
                      color: 'var(--green-bright)',
                      lineHeight: 1,
                      fontFamily: '"Barlow Condensed", sans-serif',
                      textShadow: '0 0 32px rgba(0, 230, 118, 0.5)',
                    }}
                  >
                    <NumberFlow value={h2h.p1_wins} />
                  </motion.div>
                </div>

                {/* VS */}
                <div style={{
                  fontSize: 32, color: 'var(--muted)',
                  fontFamily: '"Barlow Condensed", sans-serif',
                  fontWeight: 800, letterSpacing: 3,
                }}>VS</div>

                {/* Player 2 — red */}
                <div style={{ textAlign: 'center' }}>
                  <div style={{
                    fontSize: 12, color: 'var(--muted)',
                    fontFamily: '"Barlow Condensed", sans-serif',
                    fontWeight: 700, letterSpacing: 2, textTransform: 'uppercase',
                    marginBottom: 8,
                  }}>{p2?.name}</div>
                  <motion.div
                    initial={{ scale: 0.4, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    transition={{ type: 'spring', stiffness: 200, damping: 18, delay: 0.2 }}
                    style={{
                      fontSize: 88, fontWeight: 900,
                      color: 'var(--red-bright)',
                      lineHeight: 1,
                      fontFamily: '"Barlow Condensed", sans-serif',
                      textShadow: '0 0 32px rgba(255, 68, 68, 0.5)',
                    }}
                  >
                    <NumberFlow value={h2h.p2_wins} />
                  </motion.div>
                </div>
              </div>

              {/* Win % split progress bar */}
              <div style={{
                position: 'relative', height: 10, borderRadius: 5,
                background: 'rgba(255, 255, 255, 0.05)', overflow: 'hidden',
                marginBottom: 12,
              }}>
                <motion.div
                  initial={{ width: 0 }}
                  animate={{ width: `${winPctP1}%` }}
                  transition={{ duration: 0.9, ease: 'easeOut', delay: 0.25 }}
                  style={{
                    position: 'absolute', left: 0, top: 0, bottom: 0,
                    background: 'linear-gradient(90deg, var(--green-bright), var(--green-mid))',
                    boxShadow: '0 0 12px rgba(0, 230, 118, 0.4)',
                    borderRadius: 5,
                  }}
                />
              </div>

              <div style={{
                display: 'flex', justifyContent: 'space-between',
                fontFamily: '"Barlow Condensed", sans-serif',
                fontSize: 11, fontWeight: 700, letterSpacing: 1.5,
                color: 'var(--muted)', textTransform: 'uppercase',
              }}>
                <span style={{ color: 'var(--green-mid)' }}>{winPctP1.toFixed(0)}%</span>
                <span>
                  {h2h.total} {h2h.total !== 1 ? 'meetings' : 'meeting'}
                  {h2h.date_range && ` · ${h2h.date_range}`}
                </span>
                <span style={{ color: 'var(--red-mid)' }}>{winPctP2.toFixed(0)}%</span>
              </div>

              {h2h.surface_breakdown && Object.keys(h2h.surface_breakdown).length > 0 && h2h.total >= 3 && (
                <div style={{
                  marginTop: 12, fontSize: 11, color: 'var(--muted)',
                  textAlign: 'center', fontFamily: '"Barlow Condensed", sans-serif',
                  letterSpacing: 1.2, textTransform: 'uppercase',
                }}>
                  {Object.entries(h2h.surface_breakdown).map(([s, n]) => `${n} ${s}`).join(' · ')}
                </div>
              )}
            </div>
          )}

          {/* Stat averages */}
          {(h2h.ace_avg != null || h2h.df_avg != null) && (
            <>
              <SectionDivider label={`${p1?.name} Averages in H2H`} />
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 14, marginBottom: 18 }}>
                {[
                  ['Aces/Match', fmt(h2h.ace_avg)],
                  ['DFs/Match',  fmt(h2h.df_avg)],
                  ['BP Won',     fmt(h2h.bp_avg)],
                ].filter(([,v]) => v !== '—').map(([lbl, val]) => (
                  <div key={lbl} className="glass-card" style={{ padding: '18px 20px' }}>
                    <div style={{
                      fontFamily: '"Barlow Condensed", sans-serif', fontSize: 10, fontWeight: 800,
                      letterSpacing: 2, textTransform: 'uppercase',
                      color: 'var(--green-mid)', marginBottom: 6,
                    }}>{lbl}</div>
                    <div style={{
                      fontFamily: '"Barlow Condensed", sans-serif', fontSize: 28, fontWeight: 900, color: '#fff',
                    }}>{val}</div>
                  </div>
                ))}
              </div>
            </>
          )}

          {/* Timeline */}
          {h2h.matches?.length > 0 && (
            <>
              <SectionDivider label="Results Timeline" />
              <div className="glass-card" style={{ padding: '18px 18px 10px', marginBottom: 18 }}>
                <TimelineChart matches={h2h.matches} p1Name={p1?.name} />
              </div>
            </>
          )}

          {/* Match table */}
          {h2h.matches?.length > 0 && (
            <>
              <SectionDivider label="Match History" />
              <div className="glass-card" style={{ overflowX: 'auto', padding: '4px 10px' }}>
                <table className="baseline-table">
                  <thead><tr>
                    <th>Date</th><th>Tournament</th><th>Surface</th><th>Winner</th><th>Score</th>
                  </tr></thead>
                  <tbody>
                    {h2h.matches.map((m, i) => (
                      <tr key={i}>
                        <td style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{m['Match Date']}</td>
                        <td style={{ color: 'rgba(255,255,255,0.8)' }}>{m.Tournament}</td>
                        <td><SurfaceBadge surface={m.Surface} /></td>
                        <td style={{ color: m.Result === 'W' ? 'var(--green-bright)' : 'var(--muted)', fontWeight: 700 }}>
                          {m.Result === 'W' ? p1?.name : p2?.name}
                        </td>
                        <td style={{ fontVariantNumeric: 'tabular-nums', color: 'rgba(255,255,255,0.7)' }}>{m.Score}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </motion.div>
      )}
    </div>
  )
}
