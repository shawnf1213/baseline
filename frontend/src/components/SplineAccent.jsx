/**
 * Decorative accent — pure CSS gradient orb.
 *
 * Previously this lazy-loaded a Spline 3D scene, but the placeholder scene URL
 * was unreliable and could crash the React tree if Spline's runtime threw an
 * unhandled error mid-render. Reverted to a guaranteed-safe CSS-only gradient
 * that gives the same "atmospheric green glow" effect with zero failure risk.
 */
export default function SplineAccent() {
  return (
    <>
      {/* Top-right ambient orb */}
      <div
        aria-hidden
        style={{
          position: 'fixed',
          top: -160,
          right: -160,
          width: 560,
          height: 560,
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(0, 230, 118, 0.12) 0%, rgba(0, 230, 118, 0.05) 35%, transparent 70%)',
          filter: 'blur(60px)',
          pointerEvents: 'none',
          zIndex: 0,
          animation: 'breath 8s ease-in-out infinite',
        }}
      />
      {/* Bottom-left subtle counter-orb */}
      <div
        aria-hidden
        style={{
          position: 'fixed',
          bottom: -200,
          left: -200,
          width: 480,
          height: 480,
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(0, 230, 118, 0.05) 0%, transparent 65%)',
          filter: 'blur(50px)',
          pointerEvents: 'none',
          zIndex: 0,
          animation: 'breath 11s ease-in-out infinite reverse',
        }}
      />
    </>
  )
}
