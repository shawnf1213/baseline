import { useState, useRef, useEffect, lazy, Suspense } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import SplineAccent    from './components/SplineAccent'
import ErrorBoundary   from './components/ErrorBoundary'
import MobileShell     from './mobile/MobileShell'

// LAZY ON PURPOSE — THESE FOUR ARE THE WHOLE LEGACY OPTIMIZER, AND
// DESKTOP_OPTIMIZER IS false, SO NOBODY RENDERS THEM. Imported statically they
// were still bundled, because they are referenced from live code the flag
// guards and Vite cannot prove the branch is dead. Between them they drag in
// recharts, which was the single largest thing in a 467KB main chunk that every
// phone downloaded before the app could paint.
//
// lazy() moves them into chunks fetched only if the flag is flipped back on, so
// the optimizer stays one boolean away from working without charging every
// mobile user for it.
const SurfaceAnalyzer = lazy(() => import('./pages/SurfaceAnalyzer'))
const PropProjection  = lazy(() => import('./pages/PropProjection'))
const HeadToHead      = lazy(() => import('./pages/HeadToHead'))
const ValueBet        = lazy(() => import('./pages/ValueBet'))

// Tab definitions with icons (inline SVG so no extra deps)
const TABS = [
  { key: 'surface', label: 'Surface Analyzer', icon: 'bar' },
  { key: 'prop',    label: 'Prop Projection',  icon: 'bolt' },
  { key: 'h2h',     label: 'Head to Head',     icon: 'rackets' },
  { key: 'value',   label: 'Value Bet',        icon: 'target' },
]

const TabIcon = ({ icon, color }) => {
  const s = { width: 16, height: 16, stroke: color, strokeWidth: 2, fill: 'none', strokeLinecap: 'round', strokeLinejoin: 'round' }
  switch (icon) {
    case 'bar': return (
      <svg viewBox="0 0 24 24" {...s}>
        <path d="M3 21h18M7 17V9M12 17V5M17 17v-6" />
      </svg>
    )
    case 'rackets': return (
      <svg viewBox="0 0 24 24" {...s}>
        <circle cx="8" cy="8" r="5" /><circle cx="16" cy="16" r="5" />
        <path d="M11.5 11.5l-7 7M12.5 12.5l7-7" />
      </svg>
    )
    case 'target': return (
      <svg viewBox="0 0 24 24" {...s}>
        <circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1.5" fill={color} />
      </svg>
    )
    case 'bolt': return (
      <svg viewBox="0 0 24 24" {...s}>
        <path d="M13 2L3 14h7l-1 8 10-12h-7l1-8z" fill={color} fillOpacity="0.25" />
      </svg>
    )
    default: return null
  }
}

function HeaderLogo() {
  return (
    <div style={{
      fontFamily: '"Barlow Condensed", sans-serif',
      fontWeight: 900,
      fontSize: 26,
      letterSpacing: 5,
      textTransform: 'uppercase',
      filter: 'drop-shadow(0 0 14px rgba(0, 230, 118, 0.45))',
    }}>
      BASE<span style={{ color: 'var(--green-bright)' }}>LINE</span>
      <span style={{
        marginLeft: 14,
        fontWeight: 700,
        fontSize: 10,
        letterSpacing: 3,
        color: 'var(--green-dim)',
        verticalAlign: 'middle',
        opacity: 0.6,
      }}>OPTIMIZER</span>
    </div>
  )
}

function TourToggle({ tour, setTour }) {
  return (
    <div style={{
      display: 'flex',
      gap: 6,
      padding: 4,
      background: 'rgba(14, 24, 18, 0.5)',
      border: '1px solid var(--card-border)',
      borderRadius: 999,
    }}>
      {['ATP', 'WTA'].map(t => {
        const active = tour === t
        return (
          <motion.button
            key={t}
            whileTap={{ scale: 0.94 }}
            onClick={() => setTour(t)}
            style={{
              fontFamily: '"Barlow Condensed", sans-serif',
              fontWeight: 800,
              fontSize: 13,
              letterSpacing: 2,
              padding: '8px 22px',
              border: 'none',
              cursor: 'pointer',
              borderRadius: 999,
              minWidth: 70,
              background: active ? 'var(--green-bright)' : 'transparent',
              color: active ? '#000' : 'var(--muted)',
              transition: 'background-color 300ms ease, color 300ms ease',
              animation: active ? 'pulse-glow 2.5s ease-in-out infinite' : 'none',
            }}
          >
            {t}
          </motion.button>
        )
      })}
    </div>
  )
}

function LiveIndicator() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span className="live-dot" />
      <span style={{
        fontFamily: '"Barlow Condensed", sans-serif',
        fontWeight: 700,
        fontSize: 10,
        letterSpacing: 2.5,
        color: 'var(--green-dim)',
        textTransform: 'uppercase',
      }}>Live Data</span>
    </div>
  )
}

function TabBar({ tabs, activeKey, onChange }) {
  const containerRef = useRef(null)
  const [indicator, setIndicator] = useState({ left: 0, width: 0 })

  useEffect(() => {
    const node = containerRef.current?.querySelector(`[data-tabkey="${activeKey}"]`)
    if (node) {
      setIndicator({ left: node.offsetLeft, width: node.offsetWidth })
    }
  }, [activeKey])

  return (
    <div
      ref={containerRef}
      style={{
        position: 'relative',
        display: 'flex',
        paddingLeft: 24,
        paddingRight: 24,
        overflowX: 'auto',
        WebkitOverflowScrolling: 'touch',
        gap: 6,
      }}
    >
      {tabs.map(({ key, label, icon }) => {
        const active = activeKey === key
        const color = active ? 'var(--green-bright)' : 'var(--muted)'
        return (
          <button
            key={key}
            data-tabkey={key}
            onClick={() => onChange(key)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              fontFamily: '"Barlow Condensed", sans-serif',
              fontWeight: 800,
              fontSize: 12,
              letterSpacing: 2.5,
              textTransform: 'uppercase',
              padding: '16px 18px',
              border: 'none',
              background: active ? 'rgba(0, 230, 118, 0.05)' : 'transparent',
              cursor: 'pointer',
              whiteSpace: 'nowrap',
              color: active ? 'var(--white)' : 'var(--muted)',
              transition: 'color 220ms ease, background 220ms ease',
              minHeight: 48,
              position: 'relative',
            }}
          >
            <TabIcon icon={icon} color={color} />
            {label}
          </button>
        )
      })}
      {/* Sliding underline */}
      <motion.div
        animate={{ left: indicator.left, width: indicator.width }}
        transition={{ type: 'spring', stiffness: 260, damping: 28 }}
        style={{
          position: 'absolute',
          bottom: 0,
          height: 3,
          background: 'linear-gradient(90deg, var(--green-bright), var(--green-mid))',
          borderRadius: 3,
          boxShadow: '0 0 12px var(--green-bright)',
          pointerEvents: 'none',
        }}
      />
    </div>
  )
}

// Desktop layout only when BOTH dimensions are roomy. A phone in landscape is
// ~930px wide but only ~430px tall — the width alone would wrongly trip a
// width-only breakpoint into the desktop layout, so require height too. No phone
// (portrait or landscape) exceeds 500px on its short side; laptops/desktops do.
// Flip to true to route wide viewports back to the original optimizer layout.
const DESKTOP_OPTIMIZER = false

function isDesktopViewport() {
  if (typeof window === 'undefined') return true
  return window.innerWidth > 900 && window.innerHeight > 500
}

export default function App() {
  const [tour, setTour] = useState('ATP')
  const [tab,  setTab]  = useState('surface')
  const prevTabIdx = useRef(0)
  const [isDesktop, setIsDesktop] = useState(isDesktopViewport)

  useEffect(() => {
    const onResize = () => setIsDesktop(isDesktopViewport())
    window.addEventListener('resize', onResize)
    window.addEventListener('orientationchange', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      window.removeEventListener('orientationchange', onResize)
    }
  }, [])

  // ── ONE APP ON EVERY SCREEN ────────────────────────────────────────────────
  // This used to hand desktop the original optimizer layout and phones the
  // research shell. The shell is where all the current work lives — the live
  // boards, conviction-weighted rows, the projections tab, the pick log, the
  // Discord/Stripe gate — so anyone opening the site on a laptop was served a
  // genuinely old product with no way to reach the new one.
  //
  // The shell is width-capped and centred, so a wide window shows the same
  // interface in a readable column rather than a phone layout stretched across
  // 1900px.
  //
  // The optimizer below is kept, not deleted, and is reachable again by
  // flipping DESKTOP_OPTIMIZER to true. Gated on a flag rather than left after
  // an unconditional return so it stays live code the linter can see.
  if (!DESKTOP_OPTIMIZER || !isDesktop) {
    return (
      <ErrorBoundary label="Baseline App">
        <MobileShell />
      </ErrorBoundary>
    )
  }

  const handleTabChange = (key) => {
    const newIdx = TABS.findIndex(t => t.key === key)
    const curIdx = TABS.findIndex(t => t.key === tab)
    prevTabIdx.current = curIdx
    setTab(key)
  }

  const curIdx = TABS.findIndex(t => t.key === tab)
  const direction = curIdx >= prevTabIdx.current ? 1 : -1

  return (
    <ErrorBoundary label="App">
    <div style={{ minHeight: '100vh', position: 'relative' }}>
      {/* Decorative ambient orbs — CSS-only, desktop only */}
      {isDesktop && <SplineAccent />}

      {/* Nav — solid dark with subtle green border (no backdrop-filter for perf) */}
      <nav style={{
        position: 'sticky',
        top: 0,
        zIndex: 100,
        background: 'rgba(5, 10, 5, 0.92)',
        borderBottom: '1px solid rgba(0, 230, 118, 0.18)',
      }}>
        {/* Top strip — 80px tall */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 28px',
          minHeight: 80,
          position: 'relative',
          overflow: 'hidden',
        }}>
          <HeaderLogo />
          <div style={{ display: 'flex', alignItems: 'center', gap: 22 }}>
            <LiveIndicator />
            <TourToggle tour={tour} setTour={setTour} />
          </div>
          {/* Animated scanning line */}
          <div className="scan-line-bar" />
        </div>

        {/* Tab row */}
        <TabBar tabs={TABS} activeKey={tab} onChange={handleTabChange} />
      </nav>

      {/* Page */}
      <div style={{
        maxWidth: 1080,
        margin: '0 auto',
        padding: '28px 18px 80px',
        overflow: 'hidden',
        position: 'relative',
        zIndex: 1,
      }}>
        <AnimatePresence mode="wait" custom={direction}>
          <motion.div
            key={tab}
            custom={direction}
            variants={{
              enter: (d) => ({ x: d * 40, opacity: 0 }),
              center: { x: 0, opacity: 1 },
              exit: (d) => ({ x: d * -40, opacity: 0 }),
            }}
            initial="enter"
            animate="center"
            exit="exit"
            transition={{ duration: 0.25, ease: 'easeInOut' }}
          >
            {/* Each tab has its own error boundary — one tab crashing won't
                affect the others. Board Optimizer is the most likely to
                fail (network-heavy) so its boundary is especially important. */}
            {/* Suspense is required now that these are lazy — without a
                boundary React throws on the first render of a pending chunk. */}
            <Suspense fallback={<div style={{ padding: 40, color: '#7a7a7a' }}>Loading…</div>}>
              {tab === 'surface' && <ErrorBoundary label="Surface Analyzer"><SurfaceAnalyzer tour={tour} /></ErrorBoundary>}
              {tab === 'prop'    && <ErrorBoundary label="Prop Projection"><PropProjection   tour={tour} /></ErrorBoundary>}
              {tab === 'h2h'     && <ErrorBoundary label="Head to Head"><HeadToHead         tour={tour} /></ErrorBoundary>}
              {tab === 'value'   && <ErrorBoundary label="Value Bet"><ValueBet               tour={tour} /></ErrorBoundary>}
            </Suspense>
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
    </ErrorBoundary>
  )
}
