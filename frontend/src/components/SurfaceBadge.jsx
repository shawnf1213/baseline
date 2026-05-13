const COLORS = { Hard: '#42A5F5', Clay: '#EF6C00', Grass: '#388E3C' }

export default function SurfaceBadge({ surface }) {
  const color = COLORS[surface] || '#888'
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: 12,
      fontSize: 11, fontWeight: 700, letterSpacing: '.04em',
      background: color + '22', color, border: `1px solid ${color}55`,
    }}>
      {surface}
    </span>
  )
}
