import { useRef } from 'react'

export default function StatCard({ label, value, sub, color, children }) {
  const cardRef = useRef(null)

  const onMove = (e) => {
    const el = cardRef.current; if (!el) return
    const r = el.getBoundingClientRect()
    const x = ((e.clientX - r.left) / r.width - 0.5) * 30
    const y = ((e.clientY - r.top)  / r.height - 0.5) * -30
    el.style.transform = `perspective(600px) rotateY(${x}deg) rotateX(${y}deg) scale(1.02)`
    const lx = ((e.clientX - r.left) / r.width * 100).toFixed(1)
    const ly = ((e.clientY - r.top)  / r.height * 100).toFixed(1)
    el.style.background = `radial-gradient(circle at ${lx}% ${ly}%, #1e1e1e, var(--card))`
  }

  const onLeave = (e) => {
    const el = cardRef.current; if (!el) return
    el.style.transform = 'perspective(600px) rotateY(0deg) rotateX(0deg) scale(1)'
    el.style.background = 'var(--card)'
    el.style.transition = 'transform .3s ease-out, background .3s ease-out'
  }

  const valColor = color === 'green' ? 'var(--green)' : color === 'red' ? 'var(--red)' : 'var(--white)'

  return (
    <div ref={cardRef} onMouseMove={onMove} onMouseLeave={onLeave} style={{
      background: 'var(--card)', border: '1px solid var(--border)',
      borderRadius: 10, padding: '16px 18px',
      willChange: 'transform', transition: 'transform .3s ease-out',
    }}>
      <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 6 }}>{label}</div>
      {children || (
        <>
          <div style={{ fontSize: 26, fontWeight: 800, color: valColor, lineHeight: 1 }}>{value ?? '—'}</div>
          {sub && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>{sub}</div>}
        </>
      )}
    </div>
  )
}
