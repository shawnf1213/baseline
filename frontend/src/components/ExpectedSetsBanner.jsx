import { motion } from 'motion/react'

/**
 * Banner that explains the expected-sets assumption driving the projection.
 * Shows the competitiveness label, expected sets, and a one-line interpretation
 * so bettors understand why volume props (aces, games, BPs) are scaling up or
 * down for this specific matchup.
 */
const STYLES = {
  'Heavy favorite': {
    bg: 'linear-gradient(120deg, #5a2810 0%, #c0521c 50%, #ff8b35 100%)',
    glow: 'rgba(255, 139, 53, 0.30)',
    icon: '⚡',
    interp: 'Heavy favorite — limits volume on aces, games and break points.',
  },
  'Clear favorite': {
    bg: 'linear-gradient(120deg, #5a4810 0%, #a07820 50%, #FFD60A 100%)',
    glow: 'rgba(255, 214, 10, 0.28)',
    icon: '▲',
    interp: 'Clear favorite — modest volume reduction from a typical match.',
  },
  'Slight favorite': {
    bg: 'linear-gradient(120deg, #0a3820 0%, #1a6035 50%, #00A854 100%)',
    glow: 'rgba(0, 168, 84, 0.30)',
    icon: '◐',
    interp: 'Slight favorite — close to a typical match length.',
  },
  'Even matchup': {
    bg: 'linear-gradient(120deg, #0a4020 0%, #1a8040 50%, #00FF87 100%)',
    glow: 'rgba(0, 255, 135, 0.35)',
    icon: '◆',
    interp: 'Competitive matchup — increases volume on aces, games and break points.',
  },
}

export default function ExpectedSetsBanner({
  expectedSets,
  competitiveness,
  winProbGap,
  p1Prob,
  p2Prob,
  p1Name,
  p2Name,
  isBo5,
}) {
  if (expectedSets == null) return null
  const s = STYLES[competitiveness] || STYLES['Slight favorite']
  const format = isBo5 ? 'Best of 5' : 'Best of 3'

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.35 }}
      style={{
        position: 'relative',
        padding: '18px 22px',
        background: s.bg,
        borderRadius: 14,
        marginBottom: 18,
        overflow: 'hidden',
        boxShadow: `0 10px 28px ${s.glow}, 0 0 0 1px rgba(255, 255, 255, 0.08) inset`,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 18, flexWrap: 'wrap' }}>
        <span style={{
          fontSize: 30,
          filter: 'drop-shadow(0 2px 6px rgba(0,0,0,0.4))',
        }}>{s.icon}</span>

        <div style={{ flex: '1 1 240px', minWidth: 220 }}>
          <div style={{
            fontFamily: '"Barlow Condensed", sans-serif',
            fontWeight: 800,
            fontSize: 10,
            letterSpacing: 3,
            textTransform: 'uppercase',
            color: 'rgba(255,255,255,0.75)',
            marginBottom: 4,
          }}>
            Expected Match Length · {format}
          </div>
          <div style={{
            fontFamily: '"Barlow Condensed", sans-serif',
            fontWeight: 900,
            fontSize: 22,
            letterSpacing: 1.2,
            color: '#fff',
            textShadow: '0 1px 3px rgba(0,0,0,0.4)',
            lineHeight: 1.15,
          }}>
            {competitiveness} · {expectedSets.toFixed(1)} sets projected
          </div>
          <div style={{
            fontFamily: 'Barlow, sans-serif',
            fontSize: 12,
            color: 'rgba(255,255,255,0.85)',
            marginTop: 4,
            lineHeight: 1.4,
          }}>
            {s.interp}
          </div>
        </div>

        {/* Win prob mini-display */}
        {p1Prob != null && p2Prob != null && (
          <div style={{
            display: 'flex',
            gap: 14,
            alignItems: 'center',
            background: 'rgba(0, 0, 0, 0.28)',
            border: '1px solid rgba(255, 255, 255, 0.1)',
            borderRadius: 12,
            padding: '10px 14px',
          }}>
            <div style={{ textAlign: 'center', minWidth: 56 }}>
              <div style={{
                fontFamily: '"Barlow Condensed", sans-serif',
                fontSize: 9, fontWeight: 800,
                color: 'rgba(255,255,255,0.7)',
                letterSpacing: 1.2, textTransform: 'uppercase',
              }}>{p1Name || 'P1'}</div>
              <div style={{
                fontFamily: '"Barlow Condensed", sans-serif',
                fontWeight: 900, fontSize: 22,
                color: '#fff',
                lineHeight: 1,
              }}>{p1Prob.toFixed(0)}%</div>
            </div>
            <div style={{
              fontSize: 11, fontWeight: 800,
              color: 'rgba(255,255,255,0.5)',
              fontFamily: '"Barlow Condensed", sans-serif',
              letterSpacing: 1,
            }}>vs</div>
            <div style={{ textAlign: 'center', minWidth: 56 }}>
              <div style={{
                fontFamily: '"Barlow Condensed", sans-serif',
                fontSize: 9, fontWeight: 800,
                color: 'rgba(255,255,255,0.7)',
                letterSpacing: 1.2, textTransform: 'uppercase',
              }}>{p2Name || 'P2'}</div>
              <div style={{
                fontFamily: '"Barlow Condensed", sans-serif',
                fontWeight: 900, fontSize: 22,
                color: '#fff',
                lineHeight: 1,
              }}>{p2Prob.toFixed(0)}%</div>
            </div>
            {winProbGap != null && (
              <div style={{
                paddingLeft: 12, marginLeft: 4,
                borderLeft: '1px solid rgba(255, 255, 255, 0.18)',
                textAlign: 'center',
              }}>
                <div style={{
                  fontFamily: '"Barlow Condensed", sans-serif',
                  fontSize: 9, fontWeight: 800,
                  color: 'rgba(255,255,255,0.7)',
                  letterSpacing: 1.2, textTransform: 'uppercase',
                }}>Gap</div>
                <div style={{
                  fontFamily: '"Barlow Condensed", sans-serif',
                  fontWeight: 900, fontSize: 18,
                  color: '#fff',
                  lineHeight: 1,
                }}>{winProbGap.toFixed(0)}pp</div>
              </div>
            )}
          </div>
        )}
      </div>
    </motion.div>
  )
}
