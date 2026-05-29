/**
 * Decorative accent — single small static radial glow.
 *
 * The previous version used two 480-560px animated `filter: blur(60px)` orbs,
 * which forced the browser to re-rasterize huge blurred surfaces every frame
 * and tanked scroll perf. Replaced with one small static gradient that
 * provides the green ambient glow without any animation cost.
 */
export default function SplineAccent() {
  return (
    <div
      aria-hidden
      style={{
        position: 'fixed',
        top: -120,
        right: -120,
        width: 320,
        height: 320,
        borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(0, 230, 118, 0.08) 0%, transparent 70%)',
        pointerEvents: 'none',
        zIndex: 0,
      }}
    />
  )
}
