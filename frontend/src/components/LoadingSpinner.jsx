import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'motion/react'

const STAGES = [
  'Pulling surface data',
  'Calculating break point environment',
  'Applying CPR adjustment',
  'Analyzing handedness matchup',
  'Building projection',
]

/**
 * Loading overlay that cycles through descriptive stage messages on top of
 * an animated green progress bar. Pass `message` to override the cycling text.
 */
export default function LoadingSpinner({ message }) {
  const [stage, setStage] = useState(0)

  useEffect(() => {
    if (message) return
    const id = setInterval(() => setStage(s => (s + 1) % STAGES.length), 800)
    return () => clearInterval(id)
  }, [message])

  const text = message || STAGES[stage]

  return (
    <div style={{ padding: '48px 20px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 22 }}>
      {/* Animated progress bar */}
      <div style={{
        position: 'relative',
        width: '100%',
        maxWidth: 420,
        height: 4,
        background: 'rgba(255, 255, 255, 0.05)',
        borderRadius: 4,
        overflow: 'hidden',
      }}>
        <motion.div
          animate={{ x: ['-100%', '100%'] }}
          transition={{ duration: 1.4, repeat: Infinity, ease: 'easeInOut' }}
          style={{
            position: 'absolute', top: 0, left: 0,
            height: '100%', width: '50%',
            background: 'linear-gradient(90deg, transparent, var(--green-bright), transparent)',
            boxShadow: '0 0 12px var(--green-bright)',
          }}
        />
      </div>

      {/* Cycling stage message */}
      <div style={{ height: 22, position: 'relative', minWidth: 280, textAlign: 'center' }}>
        <AnimatePresence mode="wait">
          <motion.div
            key={text}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.35 }}
            style={{
              fontFamily: '"Barlow Condensed", sans-serif',
              fontWeight: 700,
              fontSize: 13,
              letterSpacing: 2,
              color: 'var(--green-mid)',
              textTransform: 'uppercase',
            }}
          >
            {text}…
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  )
}
