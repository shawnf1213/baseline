import { useState, useEffect } from 'react'
// ── APP SURFACE TOKENS ───────────────────────────────────────────────────────
// The app behind the paywall is LIGHT; the landing page is dark. That split is
// deliberate: a marketing page wants drama, and a tool somebody reads for
// twenty minutes at a time wants contrast and calm. Landing.jsx carries its own
// palette and does not import this, so the two never fight.
//
// WHY THE KEYS DID NOT CHANGE. Four hundred call sites read T.white, T.card and
// T.bg. Renaming them to suit a light theme would have meant touching every
// screen in the app to achieve nothing a reader can see, so the keys keep their
// meaning — `white` is THE PRIMARY TEXT COLOUR, `card` is the raised surface —
// and only the values moved. `text` and `surface` are the honest names and are
// aliases; new code should prefer them.
//
// GREEN AND RED ARE DARKER THAN THE DARK THEME'S. #00E676 on white is a neon
// smear that fails contrast badly; these are the same hues taken down to where
// they read as text. The bright originals survive as `greenGlow` / `redGlow`
// for fills and bars, where saturation is an asset rather than a legibility
// problem.
const SURFACE = '#FFFFFF'
const INK = '#12151A'

export const T = {
  bg:       '#F4F6F8',   // page — off-white, so a white card still reads raised
  bgElev:   '#FFFFFF',
  card:     SURFACE,
  cardHi:   '#F9FAFB',
  border:   '#E3E7EC',
  green:    '#03985A',   // text/accent green — passes on white
  greenDim: '#0BAF69',
  red:      '#D92D20',
  amber:    '#B25E09',
  white:    INK,         // PRIMARY TEXT. See the note above.
  muted:    '#5B6472',
  muted2:   '#8B94A3',

  // Semantic aliases — the names this palette would have had from the start.
  text:     INK,
  surface:  SURFACE,

  // Saturated versions, for FILLS ONLY: bars, chart columns, dots, rings. Never
  // for text on a light background.
  greenGlow: '#00C853',
  redGlow:   '#FF4444',
  amberGlow: '#FFB300',

  // Elevation. A light theme separates surfaces with shadow where a dark one
  // used a lighter fill, so this is not decoration — without it every card
  // dissolves into the page.
  shadow:   '0 1px 2px rgba(16,24,40,0.05), 0 1px 3px rgba(16,24,40,0.07)',
  shadowMd: '0 4px 10px rgba(16,24,40,0.06), 0 2px 4px rgba(16,24,40,0.05)',
  shadowLg: '0 12px 28px rgba(16,24,40,0.10), 0 4px 10px rgba(16,24,40,0.06)',

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
