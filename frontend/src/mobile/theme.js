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
  // matchMedia rather than a resize listener. It fires for anything that
  // actually changes which layout applies — a window drag, a rotation, a
  // browser zoom, a DPI change — whereas 'resize' misses some of those, and it
  // costs one listener instead of running a comparison on every resize frame.
  const query = `(min-width: ${WIDE_PX}px)`
  const get = () => typeof window !== 'undefined'
    && !!window.matchMedia && window.matchMedia(query).matches
  const [wide, setWide] = useState(get)
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const mq = window.matchMedia(query)
    const on = (e) => setWide(e.matches)
    // addListener is the Safari < 14 spelling; still worth keeping since this
    // is a phone-first app and an old iOS device would otherwise never update.
    if (mq.addEventListener) mq.addEventListener('change', on)
    else mq.addListener(on)
    setWide(mq.matches)          // re-sync in case it changed before we attached
    return () => {
      if (mq.removeEventListener) mq.removeEventListener('change', on)
      else mq.removeListener(on)
    }
  }, [query])
  return wide
}
