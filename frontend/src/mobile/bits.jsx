import { T } from './theme'
import { fmtSigned } from './data'

export function Card({ children, style, onClick, ...rest }) {
  return (
    <div onClick={onClick} style={{
      background: T.card, border: `1px solid ${T.border}`, borderRadius: 14,
      ...(onClick ? { cursor: 'pointer', WebkitTapHighlightColor: 'transparent' } : null),
      ...style,
    }} {...rest}>{children}</div>
  )
}

// Filter / segment chip — 40px+ tall touch target.
export function Chip({ active, onClick, children, style }) {
  return (
    <button onClick={onClick} style={{
      minHeight: 40, padding: '8px 14px', borderRadius: 999,
      fontFamily: T.cond, fontWeight: 700, fontSize: 13, letterSpacing: 0.6,
      textTransform: 'uppercase', whiteSpace: 'nowrap', cursor: 'pointer',
      border: `1px solid ${active ? T.green : T.border}`,
      background: active ? 'rgba(0,230,118,0.12)' : 'transparent',
      color: active ? T.green : T.muted,
      transition: 'all 140ms ease', ...style,
    }}>{children}</button>
  )
}

// Neutral edge delta (model projection − line). No value-judgment coloring.
export function Delta({ value, size = 15 }) {
  return (
    <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: size, color: T.white, letterSpacing: 0.4 }}>
      {fmtSigned(value)}
    </span>
  )
}

export function Heart({ active, onClick, size = 22 }) {
  return (
    <button onClick={(e) => { e.stopPropagation(); onClick?.() }} aria-label="bookmark" style={{
      background: 'transparent', border: 'none', cursor: 'pointer', padding: 8,
      minWidth: 40, minHeight: 40, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      WebkitTapHighlightColor: 'transparent',
    }}>
      <svg width={size} height={size} viewBox="0 0 24 24"
        fill={active ? T.green : 'none'} stroke={active ? T.green : T.muted}
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z" />
      </svg>
    </button>
  )
}

export function Spinner({ size = 22 }) {
  return (
    <span style={{
      width: size, height: size, display: 'inline-block',
      border: `2.5px solid ${T.border}`, borderTopColor: T.green,
      borderRadius: '50%', animation: 'baseline-spin 0.7s linear infinite',
    }} />
  )
}

export function Empty({ title, hint, icon }) {
  return (
    <div style={{ textAlign: 'center', padding: '48px 24px', color: T.muted }}>
      {icon && <div style={{ fontSize: 34, marginBottom: 10, opacity: 0.7 }}>{icon}</div>}
      <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 17, color: T.white, letterSpacing: 0.5 }}>{title}</div>
      {hint && <div style={{ fontSize: 13.5, marginTop: 8, lineHeight: 1.5, maxWidth: 320, marginLeft: 'auto', marginRight: 'auto' }}>{hint}</div>}
    </div>
  )
}

export function SectionLabel({ children, right }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', margin: '2px 2px 10px' }}>
      <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 12, letterSpacing: 2, textTransform: 'uppercase', color: T.green }}>{children}</span>
      {right}
    </div>
  )
}

// Tiny sparkline-style bars for a series of values (recent match values / hit strip).
export function MiniBars({ values, refLine }) {
  const nums = (values || []).filter(v => typeof v === 'number')
  if (!nums.length) return <span style={{ color: T.muted2, fontSize: 12 }}>no log</span>
  const max = Math.max(...nums, refLine || 0)
  const min = Math.min(...nums, refLine || Infinity)
  const span = Math.max(1, max - Math.min(min, 0))
  // Series is newest-first from the API; show oldest→newest left→right.
  const series = [...values].reverse()
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 34 }}>
      {series.map((v, i) => {
        if (typeof v !== 'number') return <div key={i} style={{ width: 7 }} />
        const h = Math.max(3, ((v - Math.min(min, 0)) / span) * 34)
        const over = refLine != null && v > refLine
        const under = refLine != null && v < refLine
        return (
          <div key={i} title={String(v)} style={{
            width: 7, height: h, borderRadius: 2,
            background: refLine == null ? T.muted2 : over ? T.green : under ? T.red : T.amber,
            opacity: 0.85,
          }} />
        )
      })}
    </div>
  )
}
