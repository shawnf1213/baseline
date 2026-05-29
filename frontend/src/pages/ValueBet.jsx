import { useState, useEffect } from 'react'
import { motion } from 'motion/react'
import NumberFlow from '@number-flow/react'
import PlayerSearch from '../components/PlayerSearch'
import LoadingSpinner from '../components/LoadingSpinner'
import { fetchStats } from '../utils/api'
import { fmt, fmtPct } from '../utils/constants'

const SURFACES = ['Hard', 'Clay', 'Grass']

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

function impliedProb(odds) {
  if (!odds || isNaN(odds)) return null
  const n = parseFloat(odds)
  if (n > 0) return 100 / (n + 100)
  if (n < 0) return (-n) / (-n + 100)
  return null
}

function modelWinProb(p1Stats, p2Stats, p1Rank, p2Rank) {
  const rankSplits = p1Stats?.ta_stats?.rank_splits
  let wr1 = p1Stats?.win_rate ?? 50
  if (rankSplits && p2Rank) {
    const rank = parseInt(p2Rank)
    if (!isNaN(rank)) {
      if (rank <= 10 && rankSplits.top10 != null)       wr1 = rankSplits.top10
      else if (rank <= 50 && rankSplits['11to50'] != null) wr1 = rankSplits['11to50']
      else if (rankSplits['51plus'] != null)              wr1 = rankSplits['51plus']
    }
  }
  const wr2 = p2Stats?.win_rate ?? 50
  const total = wr1 + wr2
  return total > 0 ? (wr1 / total) * 100 : 50
}

/**
 * Semicircular win-probability gauge. Color depends on which player is favored
 * (passed in via `colorMode`: "favored" → green, "underdog" → red).
 */
function SemiGauge({ value, label, colorMode = 'favored' }) {
  const isFav = colorMode === 'favored'
  const color = isFav ? 'var(--green-bright)' : 'var(--red-bright)'
  const trackColor = 'rgba(255, 255, 255, 0.06)'
  const cx = 90, cy = 90, r = 70
  const startAngle = 180
  const endAngle = startAngle + (180 * value) / 100

  const polar = (angle) => {
    const rad = (angle * Math.PI) / 180
    return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) }
  }

  const arc = (a1, a2) => {
    const p1 = polar(a1)
    const p2 = polar(a2)
    const largeArc = a2 - a1 > 180 ? 1 : 0
    return `M ${p1.x} ${p1.y} A ${r} ${r} 0 ${largeArc} 1 ${p2.x} ${p2.y}`
  }

  return (
    <div style={{ position: 'relative', width: 180, height: 110 }}>
      <svg width={180} height={110} style={{ filter: `drop-shadow(0 0 12px ${isFav ? 'rgba(0, 230, 118, 0.4)' : 'rgba(255, 68, 68, 0.4)'})` }}>
        <path d={arc(180, 360)} stroke={trackColor} strokeWidth={14} fill="none" strokeLinecap="round" />
        <motion.path
          d={arc(startAngle, endAngle)}
          stroke={color}
          strokeWidth={14}
          fill="none"
          strokeLinecap="round"
          initial={{ pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: 1.1, ease: 'easeOut' }}
        />
      </svg>
      <div style={{
        position: 'absolute', inset: 0,
        display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'flex-end',
        paddingBottom: 8,
      }}>
        <div style={{
          fontSize: 36, fontWeight: 900, color,
          fontFamily: '"Barlow Condensed", sans-serif',
          lineHeight: 1,
          textShadow: `0 0 14px ${color}66`,
        }}>
          <NumberFlow value={Math.round(value)} /><span style={{ fontSize: 18 }}>%</span>
        </div>
      </div>
      <div style={{
        marginTop: 6, textAlign: 'center',
        fontSize: 11, color: 'var(--muted)',
        fontFamily: '"Barlow Condensed", sans-serif',
        fontWeight: 700, letterSpacing: 1.5, textTransform: 'uppercase',
      }}>{label}</div>
    </div>
  )
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
    Promise.all([
      fetchStats(String(p1.id), tour, p1.name || ''),
      fetchStats(String(p2.id), tour, p2.name || ''),
    ])
      .then(([s1, s2]) => { setP1Stats(s1); setP2Stats(s2) })
      .finally(() => setLoading(false))
  }, [p1, p2, tour])

  const s1 = p1Stats?.[surface] || p1Stats?.All || {}
  const s2 = p2Stats?.[surface] || p2Stats?.All || {}
  const arch1 = p1Stats?.archetype
  const arch2 = p2Stats?.archetype
  const modelP1 = p1Stats && p2Stats ? modelWinProb(s1, s2, p2?.currentRank, p1?.currentRank) : null
  const modelP2 = modelP1 != null ? 100 - modelP1 : null

  const book1 = impliedProb(odds1) ? impliedProb(odds1) * 100 : null
  const book2 = impliedProb(odds2) ? impliedProb(odds2) * 100 : null
  const edge1 = modelP1 != null && book1 != null ? modelP1 - book1 : null
  const edge2 = modelP2 != null && book2 != null ? modelP2 - book2 : null

  // Determine favored side for color
  const p1IsFavored = modelP1 != null && modelP1 >= 50

  return (
    <div>
      <SectionDivider label="Players" />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14, marginBottom: 18 }}>
        <PlayerSearch tour={tour} label="Player 1" selected={p1} onSelect={p => { setP1(p); setP1Stats(null) }} />
        <PlayerSearch tour={tour} label="Player 2" selected={p2} onSelect={p => { setP2(p); setP2Stats(null) }} />
      </div>

      <SectionDivider label="Surface" />
      <div style={{ display: 'flex', gap: 10, marginBottom: 22 }}>
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
          Select two players to analyze value
        </div>
      )}
      {loading && <LoadingSpinner message="Fetching player data" />}

      {p1Stats && p2Stats && !loading && (
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
          {/* Model win probability — large semicircular gauges */}
          <SectionDivider label="Model Win Probability" />
          <div className="glass-card" style={{ padding: '28px 24px', marginBottom: 18 }}>
            <div style={{
              display: 'grid', gridTemplateColumns: '1fr auto 1fr',
              alignItems: 'center', gap: 24,
            }}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
                <div style={{
                  fontSize: 12, color: 'var(--muted)',
                  fontFamily: '"Barlow Condensed", sans-serif',
                  fontWeight: 700, letterSpacing: 1.5, textTransform: 'uppercase',
                }}>{p1?.name}</div>
                <SemiGauge value={modelP1 || 0} label="Model" colorMode={p1IsFavored ? 'favored' : 'underdog'} />
              </div>

              <div style={{
                fontSize: 24, color: 'var(--muted)',
                fontFamily: '"Barlow Condensed", sans-serif',
                fontWeight: 800, letterSpacing: 3,
              }}>VS</div>

              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
                <div style={{
                  fontSize: 12, color: 'var(--muted)',
                  fontFamily: '"Barlow Condensed", sans-serif',
                  fontWeight: 700, letterSpacing: 1.5, textTransform: 'uppercase',
                }}>{p2?.name}</div>
                <SemiGauge value={modelP2 || 0} label="Model" colorMode={!p1IsFavored ? 'favored' : 'underdog'} />
              </div>
            </div>
          </div>

          {/* Archetype cards */}
          <SectionDivider label="Player Archetypes" />
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 14, marginBottom: 18 }}>
            {[[p1, arch1, s1, p1Stats], [p2, arch2, s2, p2Stats]].map(([pl, arch, s, full], idx) => {
              const hand = full?.ta_stats?.handedness
              const rSplits = full?.ta_stats?.rank_splits || {}

              // Archetype style by name
              const archStyle = {
                'Big Server': { bg: 'linear-gradient(135deg, #5a0a14, #ff3b5c)', glow: 'rgba(255, 59, 92, 0.4)', icon: '⚡' },
                'Precision Baseliner': { bg: 'linear-gradient(135deg, #0a1a4a, #6b9fff)', glow: 'rgba(107, 159, 255, 0.4)', icon: '◎' },
                'Counterpuncher': { bg: 'linear-gradient(135deg, #5a3800, #FFB300)', glow: 'rgba(255, 179, 0, 0.4)', icon: '🛡' },
                'All-Court Player': { bg: 'linear-gradient(135deg, #00FF87, #6b9fff, #FFB300)', glow: 'rgba(0, 230, 118, 0.4)', icon: '✦' },
              }[arch] || { bg: 'rgba(255, 255, 255, 0.05)', glow: 'rgba(255, 255, 255, 0.15)', icon: '◉' }

              return (
                <div key={idx} className="glass-card" style={{ padding: '20px 22px' }}>
                  <div style={{
                    fontFamily: '"Barlow Condensed", sans-serif',
                    fontWeight: 900, fontSize: 18,
                    color: '#fff', marginBottom: 10,
                    display: 'flex', alignItems: 'center', gap: 8,
                  }}>
                    {pl?.name}
                    {hand && (
                      <span style={{
                        fontSize: 9, fontWeight: 800, padding: '2px 7px', borderRadius: 999,
                        background: hand === 'L' ? 'var(--green-bright)' : 'rgba(255,255,255,0.05)',
                        color: hand === 'L' ? '#000' : 'var(--muted)',
                        border: `1px solid ${hand === 'L' ? 'var(--green-bright)' : 'rgba(255,255,255,0.12)'}`,
                      }}>{hand}</span>
                    )}
                  </div>

                  {arch && (
                    <div style={{
                      display: 'inline-flex', alignItems: 'center', gap: 8,
                      padding: '7px 14px', borderRadius: 999,
                      background: archStyle.bg,
                      color: '#fff',
                      fontFamily: '"Barlow Condensed", sans-serif',
                      fontWeight: 900, fontSize: 12,
                      letterSpacing: 1.5, textTransform: 'uppercase',
                      boxShadow: `0 4px 14px ${archStyle.glow}`,
                      marginBottom: 14,
                      textShadow: '0 1px 2px rgba(0,0,0,0.3)',
                    }}>
                      <span style={{ fontSize: 14 }}>{archStyle.icon}</span>
                      {arch}
                    </div>
                  )}

                  <div style={{ fontSize: 12 }}>
                    {[
                      ['Win Rate', fmtPct(s.win_rate)],
                      ['Aces/M', fmt(s.aces)],
                      ['1st Srv Won', fmtPct(s.first_serve_pts_won)],
                      ['BP Conv', fmtPct(s.bp_converted)],
                      ...(rSplits.top10 != null ? [['TA: vs Top 10', rSplits.top10.toFixed(1) + '%']] : []),
                      ...(rSplits['11to50'] != null ? [['TA: vs 11-50', rSplits['11to50'].toFixed(1) + '%']] : []),
                    ].map(([lbl, val], rowIdx) => (
                      <div key={lbl} style={{
                        display: 'flex', justifyContent: 'space-between', padding: '7px 8px',
                        borderBottom: '1px solid rgba(13, 21, 16, 0.6)',
                        background: rowIdx % 2 === 1 ? 'rgba(0, 230, 118, 0.015)' : 'transparent',
                        marginLeft: -8, marginRight: -8,
                      }}>
                        <span style={{ color: 'var(--muted)', fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700 }}>{lbl}</span>
                        <span style={{ fontWeight: 800, color: '#fff', fontFamily: '"Barlow Condensed", sans-serif' }}>{val}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>

          {/* Odds input */}
          <SectionDivider label="Sportsbook Implied Odds (American)" />
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 14, marginBottom: 18 }}>
            {[[p1, odds1, setOdds1, book1, edge1], [p2, odds2, setOdds2, book2, edge2]].map(([pl, odds, setOdds, book, edge], idx) => {
              const hasValue = edge != null && edge > 5
              return (
                <div key={idx} className="glass-card" style={{ padding: '20px 22px' }}>
                  <div style={{
                    fontSize: 12, color: 'var(--muted)',
                    fontFamily: '"Barlow Condensed", sans-serif',
                    fontWeight: 700, letterSpacing: 1.5, textTransform: 'uppercase',
                    marginBottom: 10,
                  }}>{pl?.name}</div>
                  <input
                    type="number" value={odds} placeholder="+150 or -180"
                    onChange={e => setOdds(e.target.value)}
                    style={{
                      width: '100%', padding: '11px 14px',
                      background: 'rgba(255, 255, 255, 0.025)',
                      border: '1px solid var(--card-border)', borderRadius: 12,
                      color: 'var(--white)', fontSize: 15, marginBottom: 14,
                      fontFamily: '"Barlow Condensed", sans-serif',
                      fontWeight: 700, letterSpacing: 0.5,
                      outline: 'none',
                    }}
                  />
                  {book != null && (
                    <div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
                        <span style={{ color: 'var(--muted)', fontSize: 12, fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase' }}>Book Implied</span>
                        <span style={{ fontWeight: 800, color: '#fff', fontFamily: '"Barlow Condensed", sans-serif' }}>{book.toFixed(1)}%</span>
                      </div>
                      {edge != null && (
                        <div style={{
                          padding: '14px 16px', borderRadius: 12,
                          background: hasValue ? 'rgba(0, 230, 118, 0.08)' : 'rgba(255, 255, 255, 0.02)',
                          border: `1px solid ${hasValue ? 'rgba(0, 230, 118, 0.4)' : 'rgba(255, 255, 255, 0.06)'}`,
                          boxShadow: hasValue ? '0 0 16px rgba(0, 230, 118, 0.15)' : 'none',
                        }}>
                          <div style={{
                            fontSize: 11, fontWeight: 800,
                            color: hasValue ? 'var(--green-bright)' : 'var(--muted)',
                            fontFamily: '"Barlow Condensed", sans-serif',
                            letterSpacing: 2, textTransform: 'uppercase',
                            marginBottom: 4,
                          }}>
                            {hasValue ? '✓ VALUE' : 'NO VALUE'}
                          </div>
                          <div style={{
                            fontSize: 22, fontWeight: 900,
                            color: hasValue ? 'var(--green-bright)' : 'var(--muted)',
                            fontFamily: '"Barlow Condensed", sans-serif',
                            display: 'inline-flex', alignItems: 'center', gap: 8,
                            textShadow: hasValue ? '0 0 10px rgba(0, 230, 118, 0.4)' : 'none',
                          }}>
                            <span style={{ fontSize: 18 }}>{edge >= 0 ? '▲' : '▼'}</span>
                            Edge {edge >= 0 ? '+' : ''}{edge.toFixed(1)}%
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </motion.div>
      )}
    </div>
  )
}
