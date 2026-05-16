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

function TimelineChart({ matches, p1Name }) {
  if (!matches?.length) return null
  const data = [...matches].reverse().map((m, i) => ({
    i, date: m['Match Date'],
    win: m.Result === 'W' ? 1 : 0,
    surface: m.Surface,
    score: m.Score,
    opp: m.Opponent,
  }))

  return (
    <div style={{ height: 120 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          <XAxis dataKey="date" tick={{ fill: 'var(--muted)', fontSize: 10 }} />
          <YAxis domain={[-0.2, 1.2]} hide />
          <ReferenceLine y={0.5} stroke="var(--border)" strokeDasharray="4 4" />
          <Tooltip
            contentStyle={{ background: '#1a1a1a', border: '1px solid var(--border)', borderRadius: 6 }}
            formatter={(v, _, p) => [v === 1 ? 'Win' : 'Loss', `${p.payload.date} vs ${p.payload.opp}`]}
          />
          <Line dataKey="win" dot={({ cx, cy, payload }) => (
            <circle key={payload.i} cx={cx} cy={cy} r={5}
              fill={payload.win ? 'var(--green)' : 'var(--red)'}
              stroke="none" />
          )} stroke="transparent" isAnimationActive />
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

  const section = (t) => (
    <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.08em', marginBottom: 12, marginTop: 24, paddingBottom: 6, borderBottom: '1px solid var(--border)' }}>{t}</div>
  )

  return (
    <div>
      {section('Players')}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
        <PlayerSearch tour={tour} label="Player 1" selected={p1} onSelect={p => { setP1(p); setH2H(null) }} />
        <PlayerSearch tour={tour} label="Player 2" selected={p2} onSelect={p => { setP2(p); setH2H(null) }} />
      </div>

      {section('Surface Filter')}
      <div style={{ display: 'flex', gap: 8, marginBottom: 20, flexWrap: 'wrap' }}>
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

      {!p1 && !p2 && <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--muted)' }}>Select two players to view H2H record</div>}
      {loading && <LoadingSpinner message="Loading H2H data…" />}
      {error   && <div style={{ color: 'var(--red)', padding: 16 }}>Error: {error}</div>}

      {h2h && !loading && (
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}>
          {/* Record */}
          {section('Overall H2H Record')}
          <div style={{ display: 'flex', alignItems: 'center', gap: 32, padding: '20px 24px', background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, marginBottom: 16 }}>
            {h2h.total === 0 ? (
              <div style={{ fontSize: 15, color: 'var(--muted)', padding: '8px 0' }}>No H2H data available</div>
            ) : (
              <>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>{p1?.name}</div>
                  <div style={{ fontSize: 60, fontWeight: 900, color: '#00e676', lineHeight: 1 }}><NumberFlow value={h2h.p1_wins} /></div>
                </div>
                <div style={{ fontSize: 28, color: '#333' }}>—</div>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>{p2?.name}</div>
                  <div style={{ fontSize: 60, fontWeight: 900, color: '#ff4444', lineHeight: 1 }}><NumberFlow value={h2h.p2_wins} /></div>
                </div>
                <div style={{ marginLeft: 12, fontSize: 13, color: 'var(--muted)' }}>
                  <div>{h2h.total} {h2h.total !== 1 ? 'meetings' : 'meeting'}</div>
                  {h2h.surface_matches > 0 && surface !== 'All' && (
                    <div style={{ marginTop: 4 }}>
                      {h2h.surface_p1_wins}—{h2h.surface_p2_wins} on {surface}
                    </div>
                  )}
                  {h2h.date_range && (
                    <div style={{ marginTop: 4, fontSize: 11 }}>{h2h.date_range}</div>
                  )}
                  {h2h.surface_breakdown && Object.keys(h2h.surface_breakdown).length > 0 && h2h.total >= 3 && (
                    <div style={{ marginTop: 6, fontSize: 11, color: 'var(--muted)' }}>
                      {Object.entries(h2h.surface_breakdown).map(([s, n]) => `${n} ${s}`).join(' · ')}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>

          {/* Stat avgs */}
          {(h2h.ace_avg != null || h2h.df_avg != null) && (
            <>
              {section(`${p1?.name} Averages in H2H`)}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 12, marginBottom: 16 }}>
                {[
                  ['Aces/Match', fmt(h2h.ace_avg)],
                  ['DFs/Match',  fmt(h2h.df_avg)],
                  ['BP Won',     fmt(h2h.bp_avg)],
                ].filter(([,v]) => v !== '—').map(([lbl, val]) => (
                  <div key={lbl} style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px' }}>
                    <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>{lbl}</div>
                    <div style={{ fontSize: 22, fontWeight: 800 }}>{val}</div>
                  </div>
                ))}
              </div>
            </>
          )}

          {/* Timeline */}
          {h2h.matches?.length > 0 && (
            <>
              {section('Results Timeline')}
              <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 10, padding: 16, marginBottom: 16 }}>
                <TimelineChart matches={h2h.matches} p1Name={p1?.name} />
              </div>
            </>
          )}

          {/* Match table */}
          {h2h.matches?.length > 0 && (
            <>
              {section('Match History')}
              <div style={{ overflowX: 'auto' }}>
                <table className="baseline-table">
                  <thead><tr>
                    <th>Date</th><th>Tournament</th><th>Surface</th><th>Winner</th><th>Score</th>
                  </tr></thead>
                  <tbody>
                    {h2h.matches.map((m, i) => (
                      <tr key={i}>
                        <td style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{m['Match Date']}</td>
                        <td>{m.Tournament}</td>
                        <td><SurfaceBadge surface={m.Surface} /></td>
                        <td style={{ color: m.Result === 'W' ? 'var(--green)' : 'var(--muted)', fontWeight: 600 }}>
                          {m.Result === 'W' ? p1?.name : p2?.name}
                        </td>
                        <td style={{ fontVariantNumeric: 'tabular-nums', color: 'var(--muted)' }}>{m.Score}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {h2h.total === 0 && (
            <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--muted)' }}>
              No H2H data available
            </div>
          )}
        </motion.div>
      )}
    </div>
  )
}
