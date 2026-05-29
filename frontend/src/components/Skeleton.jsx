/**
 * Skeleton placeholder elements with shimmer animation.
 * Use to fill loading states instead of empty containers.
 */
export function SkeletonLine({ width = '100%', height = 14, style = {} }) {
  return (
    <div
      className="skel"
      style={{
        width,
        height,
        borderRadius: 6,
        ...style,
      }}
    />
  )
}

export function SkeletonCard({ height = 140, style = {} }) {
  return (
    <div
      className="skel glass-card"
      style={{
        height,
        borderRadius: 16,
        ...style,
      }}
    />
  )
}

export function SkeletonStatRow({ count = 5 }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
          <SkeletonLine width="40%" />
          <SkeletonLine width="20%" />
        </div>
      ))}
    </div>
  )
}
