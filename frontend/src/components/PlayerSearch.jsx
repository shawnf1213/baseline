import { useState, useRef, useEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { usePlayerSearch } from '../hooks/usePlayerSearch'
import { Search, X } from 'lucide-react'

// Convert ISO alpha-2 country code to flag emoji using Unicode regional indicators.
// e.g. "IT" → 🇮🇹, "ES" → 🇪🇸, "US" → 🇺🇸
function getFlagEmoji(alpha2) {
  if (!alpha2 || alpha2.length !== 2) return ''
  try {
    return String.fromCodePoint(
      ...[...alpha2.toUpperCase()].map(c => 0x1F1E6 + c.charCodeAt(0) - 65)
    )
  } catch {
    return ''
  }
}

export default function PlayerSearch({ tour, onSelect, label = 'Search player…', selected }) {
  const { query, setQuery, results, loading, error } = usePlayerSearch(tour)
  const [open, setOpen] = useState(false)
  const [focused, setFocused] = useState(false)
  const ref = useRef(null)
  const inputRef = useRef(null)
  const blurTimer = useRef(null)

  const handleFocus = useCallback(() => {
    clearTimeout(blurTimer.current)
    setOpen(true); setFocused(true)
  }, [])

  const handleBlur = useCallback(() => {
    blurTimer.current = setTimeout(() => { setOpen(false); setFocused(false) }, 150)
  }, [])

  useEffect(() => {
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) {
        setOpen(false); setFocused(false)
      }
    }
    document.addEventListener('mousedown', handler)
    document.addEventListener('touchstart', handler)
    return () => {
      document.removeEventListener('mousedown', handler)
      document.removeEventListener('touchstart', handler)
      clearTimeout(blurTimer.current)
    }
  }, [])

  const pick = (player) => {
    clearTimeout(blurTimer.current)
    onSelect(player)
    setQuery(''); setOpen(false); setFocused(false)
  }

  const clear = () => {
    onSelect(null); setQuery(''); setOpen(false); setFocused(false)
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  const showDropdown = open && query.length >= 3

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      {selected ? (
        <motion.div
          initial={{ y: -8, opacity: 0, scale: 0.96 }}
          animate={{ y: 0, opacity: 1, scale: 1 }}
          transition={{ type: 'spring', stiffness: 320, damping: 22 }}
          onClick={clear}
          style={{
            position: 'relative',
            background: 'rgba(14, 32, 22, 0.7)',
            border: '1px solid rgba(0, 230, 118, 0.45)',
            borderRadius: 14,
            padding: '16px 18px',
            cursor: 'pointer',
            boxShadow: '0 0 18px rgba(0, 230, 118, 0.12), 0 4px 14px rgba(0,0,0,0.35)',
            overflow: 'hidden',
          }}
        >
          <div style={{ position: 'relative', zIndex: 1 }}>
            <div style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, fontSize: 9, letterSpacing: 3, textTransform: 'uppercase', color: 'var(--green-bright)', marginBottom: 6 }}>{label}</div>
            <div style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 900, fontSize: 24, color: '#fff', letterSpacing: 0.5, lineHeight: 1.1 }}>
              {selected.name}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
              {selected.currentRank && (
                <span style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, fontSize: 10, letterSpacing: 1.5, textTransform: 'uppercase', background: 'rgba(0, 230, 118, 0.15)', color: 'var(--green-bright)', border: '1px solid rgba(0, 230, 118, 0.4)', padding: '3px 9px', borderRadius: 999 }}>
                  Rank #{selected.currentRank}
                </span>
              )}
              {selected.countryAcr && (
                <span style={{ fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 700, fontSize: 10, letterSpacing: 1, textTransform: 'uppercase', background: 'rgba(107, 159, 255, 0.1)', color: 'var(--hard-blue)', border: '1px solid rgba(107, 159, 255, 0.3)', padding: '3px 9px', borderRadius: 999, display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                  {getFlagEmoji(selected.countryCode) && <span style={{ fontSize: 12 }}>{getFlagEmoji(selected.countryCode)}</span>}
                  {selected.countryAcr}
                </span>
              )}
              <X size={14} color="var(--muted)" style={{ marginLeft: 'auto' }} />
            </div>
          </div>
        </motion.div>
      ) : (
        <>
          <div style={{ position: 'relative' }}>
            <Search
              size={16}
              style={{
                position: 'absolute', left: 14, top: '50%',
                transform: 'translateY(-50%)',
                color: focused ? 'var(--green-bright)' : 'var(--muted)',
                pointerEvents: 'none',
                transition: 'color 200ms ease',
                animation: loading ? 'pulse 1s ease-in-out infinite' : 'none',
              }}
            />
            <input
              ref={inputRef}
              value={query}
              onChange={e => setQuery(e.target.value)}
              onFocus={handleFocus}
              onBlur={handleBlur}
              placeholder={label}
              autoComplete="off"
              style={{
                width: '100%', boxSizing: 'border-box',
                padding: '14px 38px 14px 42px',
                background: 'rgba(14, 24, 18, 0.55)',
                border: `1px solid ${focused ? 'var(--card-border-hover)' : 'var(--card-border)'}`,
                borderRadius: 14,
                color: 'var(--white)',
                fontSize: 15,
                fontFamily: '"Barlow Condensed", sans-serif',
                fontWeight: 600,
                letterSpacing: 1,
                outline: 'none',
                boxShadow: focused ? '0 0 14px rgba(0, 230, 118, 0.12)' : 'none',
                transition: 'border-color 200ms ease, box-shadow 200ms ease',
              }}
            />
            {loading && (
              <div style={{
                position: 'absolute', right: 14, top: '50%', transform: 'translateY(-50%)',
                width: 14, height: 14,
                border: '2px solid var(--card-border)', borderTopColor: 'var(--green-bright)',
                borderRadius: '50%', animation: 'spin .6s linear infinite',
              }} />
            )}
          </div>
          <AnimatePresence>
            {showDropdown && (
              <motion.div
                initial={{ opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.2 }}
                style={{
                  position: 'absolute', top: '100%', left: 0, right: 0,
                  zIndex: 1000,
                  background: 'rgba(8, 13, 9, 0.97)',
                  border: '1px solid var(--card-border)',
                  borderRadius: 12,
                  marginTop: 6,
                  overflow: 'hidden',
                  boxShadow: '0 12px 28px rgba(0, 0, 0, 0.6)',
                }}
              >
                {loading ? (
                  <div style={{ padding: '14px 16px', color: 'var(--muted)', fontSize: 13 }}>Searching…</div>
                ) : error ? (
                  <div style={{ padding: '14px 16px', color: 'var(--red-bright)', fontSize: 13 }}>⚠ {error}</div>
                ) : results.length > 0 ? (
                  results.slice(0, 6).map((p, i) => (
                    <motion.div
                      key={p.id}
                      initial={{ opacity: 0, x: -8 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ duration: 0.18, delay: i * 0.05 }}
                      onMouseDown={e => { e.preventDefault(); pick(p) }}
                      style={{
                        padding: '12px 16px', cursor: 'pointer',
                        borderBottom: '1px solid rgba(26, 37, 32, 0.6)',
                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                        transition: 'background 150ms ease',
                      }}
                      onMouseEnter={e => e.currentTarget.style.background = 'rgba(0, 230, 118, 0.06)'}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        {getFlagEmoji(p.countryCode) && (
                          <span style={{ fontSize: 18 }}>{getFlagEmoji(p.countryCode)}</span>
                        )}
                        <div>
                          <div style={{ fontWeight: 800, fontSize: 14, fontFamily: '"Barlow Condensed", sans-serif', color: '#fff', letterSpacing: 0.5 }}>{p.name}</div>
                          {p.countryAcr && <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: '"Barlow Condensed", sans-serif', letterSpacing: 1 }}>{p.countryAcr}</div>}
                        </div>
                      </div>
                      {p.currentRank && (
                        <span style={{
                          fontSize: 10, color: 'var(--green-bright)',
                          fontFamily: '"Barlow Condensed", sans-serif',
                          fontWeight: 800, letterSpacing: 1,
                          background: 'rgba(0, 230, 118, 0.1)',
                          border: '1px solid rgba(0, 230, 118, 0.3)',
                          padding: '2px 8px', borderRadius: 999,
                        }}>#{p.currentRank}</span>
                      )}
                    </motion.div>
                  ))
                ) : (
                  <div style={{ padding: '14px 16px', color: 'var(--muted)', fontSize: 13 }}>No players found</div>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </>
      )}
      <style>{`@keyframes spin { to { transform: translateY(-50%) rotate(360deg); } }`}</style>
    </div>
  )
}
