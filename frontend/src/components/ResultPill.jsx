export default function ResultPill({ result }) {
  const w = result === 'W'
  return (
    <span style={{
      display: 'inline-block',
      padding: '3px 10px',
      borderRadius: 999,
      fontSize: 11,
      fontWeight: 800,
      fontFamily: '"Barlow Condensed", sans-serif',
      letterSpacing: 1,
      background: w ? 'rgba(0, 230, 118, 0.15)' : 'rgba(255, 68, 68, 0.15)',
      color: w ? 'var(--green-bright)' : 'var(--red-bright)',
      border: `1px solid ${w ? 'rgba(0, 230, 118, 0.4)' : 'rgba(255, 68, 68, 0.4)'}`,
      boxShadow: w ? '0 0 8px rgba(0, 230, 118, 0.2)' : '0 0 8px rgba(255, 68, 68, 0.2)',
    }}>
      {result}
    </span>
  )
}
