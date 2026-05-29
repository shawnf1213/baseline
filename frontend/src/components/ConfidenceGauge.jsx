import { motion } from 'motion/react'
import NumberFlow from '@number-flow/react'

function getColor(v) {
  if (v < 40) return '#FF3B5C'
  if (v < 66) return '#FFD60A'
  if (v <= 80) return '#00E676'
  return '#00FF87'
}
function getLabel(v) {
  if (v < 40) return 'Low Confidence'
  if (v < 66) return 'Moderate Confidence'
  if (v <= 80) return 'Good Confidence'
  return 'High Confidence'
}

/**
 * Speedometer-style gauge: 160px diameter, tick marks, gradient arc with glow.
 * Pass `size` to override (defaults to 160).
 */
export default function ConfidenceGauge({ confidence = 0, size = 160 }) {
  const color = getColor(confidence)
  const label = getLabel(confidence)
  const cx = size / 2
  const cy = size / 2
  const r = size / 2 - 14
  const circumference = 2 * Math.PI * r
  const offset = circumference - (confidence / 100) * circumference

  // Tick marks around the circle
  const ticks = Array.from({ length: 24 }, (_, i) => {
    const angle = (i / 24) * 360 - 90
    const rad = (angle * Math.PI) / 180
    const innerR = r + 4
    const outerR = r + 10
    return {
      x1: cx + innerR * Math.cos(rad),
      y1: cy + innerR * Math.sin(rad),
      x2: cx + outerR * Math.cos(rad),
      y2: cy + outerR * Math.sin(rad),
      major: i % 6 === 0,
    }
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
      <div style={{ position: 'relative', width: size, height: size }}>
        <svg width={size} height={size} style={{ filter: `drop-shadow(0 0 10px ${color}55)` }}>
          <defs>
            <linearGradient id="conf-arc" gradientUnits="userSpaceOnUse" x1="0" y1={size} x2={size} y2="0">
              <stop offset="0%"  stopColor="#FF3B5C" />
              <stop offset="50%" stopColor="#FFD60A" />
              <stop offset="100%" stopColor="#00FF87" />
            </linearGradient>
          </defs>

          {/* Tick marks */}
          {ticks.map((t, i) => (
            <line key={i} x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2}
              stroke={t.major ? 'rgba(0, 230, 118, 0.35)' : 'rgba(255, 255, 255, 0.08)'}
              strokeWidth={t.major ? 1.5 : 1}
            />
          ))}

          {/* Track */}
          <circle cx={cx} cy={cy} r={r} fill="none" stroke="rgba(255, 255, 255, 0.06)" strokeWidth="8" />

          {/* Gradient arc */}
          <motion.circle
            cx={cx} cy={cy} r={r}
            fill="none"
            stroke="url(#conf-arc)"
            strokeWidth="8"
            strokeDasharray={circumference}
            initial={{ strokeDashoffset: circumference }}
            animate={{ strokeDashoffset: offset }}
            transition={{ duration: 1.1, ease: 'easeOut' }}
            strokeLinecap="round"
            transform={`rotate(-90 ${cx} ${cy})`}
          />
        </svg>
        <div style={{
          position: 'absolute', inset: 0,
          display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center',
        }}>
          <div style={{
            fontSize: size * 0.28,
            fontWeight: 900,
            color,
            lineHeight: 1,
            fontFamily: '"Barlow Condensed", sans-serif',
            textShadow: `0 0 12px ${color}66`,
          }}>
            <NumberFlow value={confidence} /><span style={{ fontSize: size * 0.18 }}>%</span>
          </div>
          <div style={{
            fontSize: 9, color: 'var(--muted)',
            fontFamily: '"Barlow Condensed", sans-serif',
            fontWeight: 700, letterSpacing: 2,
            textTransform: 'uppercase',
            marginTop: 4,
          }}>Confidence</div>
        </div>
      </div>
      <div style={{
        display: 'inline-flex', alignItems: 'center', gap: 8,
        fontSize: 12, color,
        fontFamily: '"Barlow Condensed", sans-serif',
        fontWeight: 700, letterSpacing: 1.5, textTransform: 'uppercase',
      }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, boxShadow: `0 0 8px ${color}` }} />
        {label}
      </div>
    </div>
  )
}
