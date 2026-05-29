import { motion } from 'motion/react'

const ENV_STYLES = {
  HIGH_BREAK: {
    bg: 'linear-gradient(120deg, #5a0a14 0%, #c01233 50%, #ff3b5c 100%)',
    glow: 'rgba(255, 59, 92, 0.35)',
    icon: '⚡',
    label: 'High Break Environment',
  },
  SERVE_DOM: {
    bg: 'linear-gradient(120deg, #0a1a4a 0%, #1e3a8a 50%, #6b9fff 100%)',
    glow: 'rgba(107, 159, 255, 0.35)',
    icon: '🏆',
    label: 'Serve Dominant',
  },
  RET_EDGE: {
    bg: 'linear-gradient(120deg, #0a4020 0%, #1a8040 50%, #00e676 100%)',
    glow: 'rgba(0, 230, 118, 0.35)',
    icon: '⚙',
    label: 'Returner Edge',
  },
  WEAK_SERVE: {
    bg: 'linear-gradient(120deg, #5a2810 0%, #c0521c 50%, #ff8b35 100%)',
    glow: 'rgba(255, 139, 53, 0.35)',
    icon: '⚠',
    label: 'Weak Serve',
  },
  STANDARD: {
    bg: 'linear-gradient(120deg, #18211c 0%, #2a3a30 50%, #3a4a40 100%)',
    glow: 'rgba(74, 106, 80, 0.2)',
    icon: '◉',
    label: 'Standard Environment',
  },
}

export default function EnvironmentBanner({ environment, environmentLabel }) {
  if (!environment) return null
  const s = ENV_STYLES[environment] || ENV_STYLES.STANDARD
  const isBreak = environment === 'HIGH_BREAK'

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.96 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.4 }}
      style={{
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        gap: 16,
        padding: '18px 24px',
        background: s.bg,
        borderRadius: 14,
        marginBottom: 18,
        overflow: 'hidden',
        boxShadow: `0 10px 30px ${s.glow}, 0 0 0 1px rgba(255, 255, 255, 0.08) inset`,
      }}
    >
      <span style={{
        fontSize: 28,
        filter: 'drop-shadow(0 2px 6px rgba(0,0,0,0.4))',
        animation: isBreak ? 'pulse 1s ease-in-out infinite' : 'none',
      }}>{s.icon}</span>
      <div style={{ flex: 1 }}>
        <div style={{
          fontFamily: '"Barlow Condensed", sans-serif',
          fontWeight: 800,
          fontSize: 10,
          letterSpacing: 3,
          textTransform: 'uppercase',
          color: 'rgba(255,255,255,0.7)',
          marginBottom: 4,
        }}>Match Environment</div>
        <div style={{
          fontFamily: '"Barlow Condensed", sans-serif',
          fontWeight: 900,
          fontSize: 22,
          letterSpacing: 1.5,
          textTransform: 'uppercase',
          color: '#fff',
          textShadow: '0 1px 3px rgba(0,0,0,0.4)',
        }}>{environmentLabel || s.label}</div>
      </div>
      {/* Scanning shimmer */}
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
        background: 'linear-gradient(90deg, transparent 30%, rgba(255,255,255,0.06) 50%, transparent 70%)',
        transform: 'translateX(-100%)',
        animation: 'shimmer 4s ease-in-out infinite',
        pointerEvents: 'none',
      }} />
    </motion.div>
  )
}
