import { useRef } from 'react'

/**
 * Glassmorphism stat card with color-coded left border.
 *  - color="green" → above tour avg (bright green left bar, green value)
 *  - color="red"   → below tour avg (red left bar, red value)
 *  - color=null    → neutral
 */
export default function StatCard({ label, value, sub, color, children }) {
  const cardRef = useRef(null)

  const valColor = color === 'green' ? 'var(--green-bright)'
    : color === 'red' ? 'var(--red-bright)'
    : 'var(--white)'

  const accentBar = color === 'green' ? 'var(--green-mid)'
    : color === 'red' ? 'var(--red-mid)'
    : 'transparent'

  return (
    <div
      ref={cardRef}
      className="glass-card"
      style={{
        position: 'relative',
        padding: '16px 18px 16px 22px',
        overflow: 'hidden',
      }}
    >
      {/* Left accent bar */}
      {color && (
        <span style={{
          position: 'absolute', left: 0, top: 0, bottom: 0, width: 3,
          background: accentBar,
          boxShadow: `0 0 12px ${accentBar}`,
        }} />
      )}
      <div style={{
        fontSize: 11,
        color: 'var(--muted)',
        textTransform: 'uppercase',
        letterSpacing: '.08em',
        fontFamily: '"Barlow Condensed", sans-serif',
        fontWeight: 700,
        marginBottom: 6,
      }}>{label}</div>
      {children || (
        <>
          <div style={{
            fontSize: 28,
            fontWeight: 900,
            color: valColor,
            lineHeight: 1,
            fontFamily: '"Barlow Condensed", sans-serif',
          }}>{value ?? '—'}</div>
          {sub && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>{sub}</div>}
        </>
      )}
    </div>
  )
}
