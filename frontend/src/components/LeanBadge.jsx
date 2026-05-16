import { motion } from 'motion/react'

export default function LeanBadge({ lean, size = 'lg' }) {
  if (!lean) return null
  const isOver = lean === 'OVER'
  const bg = isOver ? '#00E67622' : '#FF444422'
  const color = isOver ? 'var(--green)' : 'var(--red)'
  const border = isOver ? '#00E67655' : '#FF444455'
  const px = size === 'lg' ? '20px' : '10px'
  const py = size === 'lg' ? '10px' : '4px'
  const fs = size === 'lg' ? 20 : 12

  return (
    <motion.div
      key={lean}
      initial={{ rotateX: 90, opacity: 0 }}
      animate={{ rotateX: 0, opacity: 1 }}
      transition={{ duration: 0.4, ease: 'backOut' }}
      style={{
        display: 'inline-block', padding: `${py} ${px}`,
        background: bg, border: `1px solid ${border}`,
        borderRadius: 8, color, fontWeight: 800,
        fontSize: fs, letterSpacing: '.06em',
        fontFamily: '"Barlow Condensed", sans-serif',
        transformPerspective: 600,
      }}
    >
      {lean}
    </motion.div>
  )
}
