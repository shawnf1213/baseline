import { useState, useEffect } from 'react'
// Mobile design tokens — the exact Baseline system the app surface uses.
// (Matches the PasswordGate + PWA theme color; greens/reds/amber are shared
// with the desktop CSS variables.)
export const T = {
  bg:       '#0a0a0a',
  bgElev:   '#0d0d0d',
  card:     '#111111',
  cardHi:   '#161616',
  border:   '#1e1e1e',
  green:    '#00E676',
  greenDim: '#00A854',
  red:      '#FF4444',
  amber:    '#FFB300',
  white:    '#FFFFFF',
  muted:    '#AAAAAA',
  muted2:   '#6b6b6b',
  font:     '"Barlow", -apple-system, BlinkMacSystemFont, sans-serif',
  cond:     '"Barlow Condensed", sans-serif',
}

// Surface accent colors (shared with desktop constants).
export const SURFACE_TINT = { Hard: '#42A5F5', Clay: '#EF6C00', Grass: '#2E7D32' }

// Safe-area insets for notched phones (used by the bottom nav + sheets).
export const SAFE_BOTTOM = 'env(safe-area-inset-bottom, 0px)'
export const SAFE_TOP = 'env(safe-area-inset-top, 0px)'


// ── DESKTOP BREAKPOINT ───────────────────────────────────────────────────────
// 1024 rather than 900: between the two a laptop window is wide enough to trip
// a desktop layout but not wide enough to hold a sidebar AND two columns of
// cards without either being cramped.
export const WIDE_PX = 1024

export function useIsWide() {
  const get = () => typeof window !== 'undefined' && window.innerWidth >= WIDE_PX
  const [wide, setWide] = useState(get)
  useEffect(() => {
    const on = () => setWide(get())
    window.addEventListener('resize', on)
    window.addEventListener('orientationchange', on)
    return () => {
      window.removeEventListener('resize', on)
      window.removeEventListener('orientationchange', on)
    }
  }, [])
  return wide
}
