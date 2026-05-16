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
      {/* Nav */}
      <nav style={{ position: 'sticky', top: 0, zIndex: 100, background: '#060809', borderBottom: '1px solid #111a14' }}>
        {/* Top strip */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 24px', borderBottom: '1px solid #0d1510' }}>
          <div style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 900, fontSize: 22, letterSpacing: 4, textTransform: 'uppercase' }}>
            BASE<span style={{ color: '#00e676' }}>LINE</span>
            <span style={{ marginLeft: 12, fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 9, letterSpacing: 3, color: '#1a3a25', verticalAlign: 'middle' }}>OPTIMIZER</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            {/* Live indicator */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#00e676', display: 'inline-block', animation: 'pulse 2s infinite' }} />
              <span style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 9, letterSpacing: 2, color: '#1a4025', textTransform: 'uppercase' }}>Live Data</span>
            </div>
            {/* ATP/WTA toggle */}
            <div style={{ display: 'flex', background: '#080d09', border: '1px solid #1a2520', borderRadius: 6, overflow: 'hidden' }}>
              {['ATP', 'WTA'].map(t => (
                <button key={t} onClick={() => setTour(t)} style={{
                  fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 11, letterSpacing: 1,
                  padding: '7px 18px', border: 'none', cursor: 'pointer', transition: 'all .15s',
                  background: tour === t ? '#00e676' : 'transparent',
                  color: tour === t ? '#000' : '#3a5045',
                }}>{t}</button>
              ))}
            </div>
          </div>
        </div>
        {/* Tab row */}
        <div style={{ display: 'flex', paddingLeft: 24, overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
          {TABS.map(({ key, label }) => (
            <button key={key} onClick={() => handleTabChange(key)} style={{
              fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 11, letterSpacing: 2,
              textTransform: 'uppercase', padding: '13px 20px',
              border: 'none', borderBottom: `2px solid ${tab === key ? '#00e676' : 'transparent'}`,
              cursor: 'pointer', background: 'transparent', whiteSpace: 'nowrap',
              color: tab === key ? '#00e676' : '#2a3a30',
              transition: 'color .15s, border-color .15s', minHeight: 44,
            }}>
              {label}
            </button>
          ))}
        </div>
      </nav>

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
