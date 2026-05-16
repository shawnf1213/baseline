import { useState, useRef } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import SurfaceAnalyzer from './pages/SurfaceAnalyzer'
import PropProjection  from './pages/PropProjection'
import HeadToHead      from './pages/HeadToHead'
import ValueBet        from './pages/ValueBet'

// v2 visual redesign deployed
const TABS = [
  { key: 'surface', label: 'Player Surface Analyzer' },
  { key: 'prop',    label: 'Prop Projection' },
  { key: 'h2h',    label: 'Head to Head' },
  { key: 'value',  label: 'Value Bet' },
]

export default function App() {
  const [tour, setTour] = useState('ATP')
  const [tab,  setTab]  = useState('surface')
  const prevTabIdx = useRef(0)

  const handleTabChange = (key) => {
    const newIdx = TABS.findIndex(t => t.key === key)
    const curIdx = TABS.findIndex(t => t.key === tab)
    prevTabIdx.current = curIdx
    setTab(key)
  }

  const curIdx = TABS.findIndex(t => t.key === tab)
  const direction = curIdx >= prevTabIdx.current ? 1 : -1

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg)' }}>
      {/* Top bar */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '14px 24px', borderBottom: '1px solid var(--border)',
        background: 'var(--card)', position: 'sticky', top: 0, zIndex: 100,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <img src="/baseline-logo.png" alt="Baseline" style={{ height: 40, width: 'auto' }} />
        </div>
        <div style={{ display: 'flex', background: '#1a1a1a', borderRadius: 8, border: '1px solid var(--border)', padding: 3, gap: 2 }}>
          {['ATP', 'WTA'].map(t => (
            <button key={t} onClick={() => setTour(t)} style={{
              padding: '6px 18px', borderRadius: 6, fontSize: 12, fontWeight: 700,
              cursor: 'pointer', border: 'none',
              background: tour === t ? 'var(--green)' : 'transparent',
              color: tour === t ? '#000' : 'var(--muted)',
              transition: 'all .15s',
            }}>{t}</button>
          ))}
        </div>
      </div>

      {/* Tab nav */}
      <div style={{
        display: 'flex', borderBottom: '1px solid var(--border)',
        background: 'var(--card)', overflowX: 'auto',
        WebkitOverflowScrolling: 'touch',
      }}>
        {TABS.map(({ key, label }) => (
          <button key={key} onClick={() => handleTabChange(key)} style={{
            padding: '14px 22px', border: 'none', cursor: 'pointer',
            background: 'transparent', whiteSpace: 'nowrap', fontSize: 13, fontWeight: 600,
            color: tab === key ? 'var(--green)' : 'var(--muted)',
            borderBottom: `2px solid ${tab === key ? 'var(--green)' : 'transparent'}`,
            transition: 'color .15s, border-color .15s', minHeight: 44,
          }}>
            {label}
          </button>
        ))}
      </div>

      {/* Page */}
      <div style={{ maxWidth: 960, margin: '0 auto', padding: '24px 16px 60px', overflow: 'hidden' }}>
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
            transition={{ duration: 0.2, ease: 'easeInOut' }}
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
