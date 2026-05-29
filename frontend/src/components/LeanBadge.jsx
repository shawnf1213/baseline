import { motion } from 'motion/react'

/**
 * Large dramatic OVER/UNDER pill with flip-in animation and glow.
 * Default size is large (120×60 minimum). Pass size="sm" for inline use.
 */
export default function LeanBadge({ lean, size = 'lg' }) {
  if (!lean) return null
  const isOver = lean === 'OVER'

  if (size === 'sm') {
    const bg = isOver ? 'rgba(0, 230, 118, 0.18)' : 'rgba(255, 68, 68, 0.18)'
    const color = isOver ? 'var(--green-bright)' : 'var(--red-bright)'
    const border = isOver ? 'rgba(0, 230, 118, 0.5)' : 'rgba(255, 68, 68, 0.5)'
    return (
      <span style={{
        display: 'inline-block', padding: '4px 10px',
        background: bg, border: `1px solid ${border}`,
        borderRadius: 8, color, fontWeight: 800,
        fontSize: 12, letterSpacing: '.06em',
        fontFamily: '"Barlow Condensed", sans-serif',
      }}>
        {lean}
      </span>
    )
  }

  const bg = isOver
    ? 'linear-gradient(135deg, #00E676 0%, #00FF87 100%)'
    : 'linear-gradient(135deg, #FF4444 0%, #FF3B5C 100%)'
  const glow = isOver
    ? '0 0 24px rgba(0, 230, 118, 0.55), 0 6px 18px rgba(0, 230, 118, 0.25)'
    : '0 0 24px rgba(255, 68, 68, 0.55), 0 6px 18px rgba(255, 68, 68, 0.25)'

  return (
    <motion.div
      key={lean}
      initial={{ rotateY: 90, opacity: 0, scale: 0.85 }}
      animate={{ rotateY: 0, opacity: 1, scale: 1 }}
      transition={{ duration: 0.55, ease: 'backOut' }}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        minWidth: 130,
        minHeight: 64,
        padding: '0 28px',
        background: bg,
        borderRadius: 14,
        color: '#fff',
        fontWeight: 900,
        fontSize: 26,
        letterSpacing: '.1em',
        fontFamily: '"Barlow Condensed", sans-serif',
        boxShadow: glow,
        textShadow: '0 1px 2px rgba(0,0,0,0.25)',
        transformPerspective: 600,
        textTransform: 'uppercase',
      }}
    >
      {lean}
    </motion.div>
  )
}
