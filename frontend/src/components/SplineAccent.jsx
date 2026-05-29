import { Suspense, useEffect, useState } from 'react'

/**
 * Decorative Spline 3D accent. Loads lazily and only mounts on desktop.
 * Uses a try/catch wrapper so any Spline runtime error is silently swallowed —
 * this is purely decorative and should never block the app.
 */
export default function SplineAccent() {
  const [Spline, setSpline] = useState(null)
  const [errored, setErrored] = useState(false)

  useEffect(() => {
    let cancelled = false
    import('@splinetool/react-spline')
      .then(mod => { if (!cancelled) setSpline(() => mod.default) })
      .catch(() => { if (!cancelled) setErrored(true) })
    return () => { cancelled = true }
  }, [])

  if (errored || !Spline) {
    // Fallback: subtle CSS gradient orb if Spline fails to load
    return (
      <div
        aria-hidden
        style={{
          position: 'fixed',
          top: -120,
          right: -120,
          width: 480,
          height: 480,
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(0, 230, 118, 0.10) 0%, rgba(0, 230, 118, 0.04) 35%, transparent 70%)',
          filter: 'blur(40px)',
          pointerEvents: 'none',
          zIndex: 0,
        }}
      />
    )
  }

  return (
    <div
      aria-hidden
      style={{
        position: 'fixed',
        top: 0,
        right: 0,
        width: 360,
        height: 360,
        opacity: 0.45,
        pointerEvents: 'none',
        zIndex: 0,
        filter: 'blur(0.5px)',
      }}
    >
      <Suspense fallback={null}>
        {/* Public Spline scene — abstract green orb. Errors fall back to gradient orb above. */}
        <Spline scene="https://prod.spline.design/6Wq1Q7YGyM-iab9p/scene.splinecode" />
      </Suspense>
    </div>
  )
}
