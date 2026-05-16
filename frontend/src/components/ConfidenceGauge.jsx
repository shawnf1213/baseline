import { motion } from 'motion/react'
import NumberFlow from '@number-flow/react'

function getColor(v) {
  if (v < 40) return 'var(--red)'
  if (v < 66) return 'var(--amber)'
  if (v <= 80) return 'var(--green)'
  return '#69FF47'
}
function getLabel(v) {
  if (v < 40) return 'Low Confidence'
  if (v < 66) return 'Moderate Confidence'
  if (v <= 80) return 'Good Confidence'
  return 'High Confidence'
}

export default function ConfidenceGauge({ confidence = 0 }) {
  const color = getColor(confidence)
  const r = 38, cx = 50, cy = 50
  const circumference = 2 * Math.PI * r
  const offset = circumference - (confidence / 100) * circumference

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
      <div style={{ position: 'relative', width: 100, height: 100 }}>
        <svg width="100" height="100">
          <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--border)" strokeWidth="6" transform="rotate(-90 50 50)" />
          <motion.circle
            cx={cx} cy={cy} r={r}
            fill="none" stroke={color} strokeWidth="6"
            strokeDasharray={circumference}
            initial={{ strokeDashoffset: circumference }}
            animate={{ strokeDashoffset: offset }}
            transition={{ duration: 0.8, ease: 'easeOut' }}
            strokeLinecap="round"
            transform="rotate(-90 50 50)"
          />
        </svg>
        <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ fontSize: 22, fontWeight: 800, color, lineHeight: 1 }}>
            <NumberFlow value={confidence} /><span>%</span>
          </div>
        </div>
      </div>
      <div style={{ fontSize: 11, color, letterSpacing: '.03em' }}>{getLabel(confidence)}</div>
    </div>
  )
}
