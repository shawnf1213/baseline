import { useState, useRef, useEffect, Suspense, lazy } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import SurfaceAnalyzer from './pages/SurfaceAnalyzer'
import PropProjection  from './pages/PropProjection'
import HeadToHead      from './pages/HeadToHead'
import ValueBet        from './pages/ValueBet'

// Lazy Spline accent (desktop only)
const SplineAccent = lazy(() => import('./components/SplineAccent'))

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
      background: 'rgba(255, 255, 255, 0.025)',
      backdropFilter: 'blur(10px)',
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

export default function App() {
  const [tour, setTour] = useState('ATP')
  const [tab,  setTab]  = useState('surface')
  const prevTabIdx = useRef(0)
  const [isDesktop, setIsDesktop] = useState(() => typeof window !== 'undefined' && window.innerWidth > 900)

  useEffect(() => {
    const onResize = () => setIsDesktop(window.innerWidth > 900)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const handleTabChange = (key) => {
    const newIdx = TABS.findIndex(t => t.key === key)
    const curIdx = TABS.findIndex(t => t.key === tab)
    prevTabIdx.current = curIdx
    setTab(key)
  }

  const curIdx = TABS.findIndex(t => t.key === tab)
  const direction = curIdx >= prevTabIdx.current ? 1 : -1

  return (
    <div style={{ minHeight: '100vh', position: 'relative' }}>
      {/* Decorative Spline accent — desktop only, low z-index */}
      {isDesktop && (
        <Suspense fallback={null}>
          <SplineAccent />
        </Suspense>
      )}

      {/* Nav — glassmorphism header */}
      <nav style={{
        position: 'sticky',
        top: 0,
        zIndex: 100,
        background: 'rgba(0, 0, 0, 0.6)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
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
            {tab === 'surface' && <SurfaceAnalyzer tour={tour} />}
            {tab === 'prop'    && <PropProjection  tour={tour} />}
            {tab === 'h2h'     && <HeadToHead      tour={tour} />}
            {tab === 'value'   && <ValueBet        tour={tour} />}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  )
}
