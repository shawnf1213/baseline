const STYLES = {
  Hard:  {
    bg: 'linear-gradient(135deg, #1e3a8a 0%, #3b82f6 60%, #6b9fff 100%)',
    fg: '#fff',
    glow: 'rgba(107, 159, 255, 0.45)',
    texture: 'repeating-linear-gradient(45deg, transparent 0 4px, rgba(255,255,255,0.04) 4px 5px)',
  },
  Clay:  {
    bg: 'linear-gradient(135deg, #7a2410 0%, #c0421c 50%, #ff6b35 100%)',
    fg: '#fff',
    glow: 'rgba(255, 107, 53, 0.45)',
    texture: 'repeating-radial-gradient(circle at 30% 30%, rgba(255,255,255,0.06) 0 2px, transparent 2px 4px)',
  },
  Grass: {
    bg: 'linear-gradient(135deg, #0a4020 0%, #1a8040 50%, #00e676 100%)',
    fg: '#fff',
    glow: 'rgba(0, 230, 118, 0.45)',
    texture: 'repeating-linear-gradient(90deg, transparent 0 3px, rgba(255,255,255,0.04) 3px 4px)',
  },
}

export default function SurfaceBadge({ surface }) {
  const s = STYLES[surface] || STYLES.Hard
  return (
    <span style={{
      position: 'relative',
      display: 'inline-block',
      padding: '6px 14px',
      borderRadius: 999,
      fontSize: 12,
      fontWeight: 800,
      letterSpacing: '.12em',
      fontFamily: '"Barlow Condensed", sans-serif',
      background: s.bg,
      color: s.fg,
      textTransform: 'uppercase',
      boxShadow: `0 4px 14px ${s.glow}, 0 0 0 1px rgba(255,255,255,0.08) inset`,
      overflow: 'hidden',
    }}>
      <span style={{
        position: 'absolute', inset: 0,
        background: s.texture,
        pointerEvents: 'none',
        mixBlendMode: 'overlay',
      }} />
      <span style={{ position: 'relative' }}>{surface}</span>
    </span>
  )
}
