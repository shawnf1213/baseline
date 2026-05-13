export default function ResultPill({ result }) {
  const w = result === 'W'
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: 10,
      fontSize: 11, fontWeight: 700,
      background: w ? '#00E67622' : '#FF444422',
      color: w ? 'var(--green)' : 'var(--red)',
      border: `1px solid ${w ? '#00E67644' : '#FF444444'}`,
    }}>
      {result}
    </span>
  )
}
