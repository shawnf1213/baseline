import { useEffect, useRef, useState } from 'react'

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
  const [displayed, setDisplayed] = useState(0)
  const raf = useRef(null)

  useEffect(() => {
    const start = 0, end = confidence, dur = 800
    const t0 = performance.now()
    const tick = (t) => {
      const p = Math.min((t - t0) / dur, 1)
      setDisplayed(Math.round(start + (end - start) * p))
      if (p < 1) raf.current = requestAnimationFrame(tick)
    }
    raf.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf.current)
  }, [confidence])

  const color = getColor(confidence)
  const r = 38, cx = 50, cy = 50
  const circ = 2 * Math.PI * r
  const dash = (displayed / 100) * circ

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
      <div style={{ position: 'relative', width: 100, height: 100 }}>
        <svg width="100" height="100" style={{ transform: 'rotate(-90deg)' }}>
          <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--border)" strokeWidth="6" />
          <circle cx={cx} cy={cy} r={r} fill="none" stroke={color} strokeWidth="6"
            strokeDasharray={`${dash} ${circ - dash}`}
            strokeLinecap="round"
            style={{ transition: 'stroke-dasharray .05s linear' }}
          />
        </svg>
        <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ fontSize: 22, fontWeight: 800, color, lineHeight: 1 }}>{displayed}%</div>
        </div>
      </div>
      <div style={{ fontSize: 11, color, letterSpacing: '.03em' }}>{getLabel(confidence)}</div>
    </div>
  )
}
