// Baseline design tokens — a 1:1 port of frontend/src/mobile/theme.js.
//
// THE PWA IS THE BLUEPRINT (operator ruling 4). Every value here is copied from
// the website's token file rather than re-chosen, so the app and the site read
// as one product. If a colour needs to change it changes there first, then
// here; this file does not get its own opinions.
//
// Two things that cannot port literally, and what they became:
//
//   glass    the web uses a CSS linear-gradient with a 0.052 -> 0.014 white
//            alpha. React Native has no CSS gradients without a native module,
//            and the web deliberately avoids backdrop blur (it tanked scrolling
//            there and would here). A flat translucent fill at the gradient's
//            midpoint plus the same hairline border gives the same read over
//            the dark ground — the surface shows through because the fill is
//            semi-transparent, which is the whole trick on the web too.
//
//   fonts    "Barlow" and "Barlow Condensed" are loaded through
//            @expo-google-fonts in the root layout. Font FAMILY names are the
//            PostScript-style names those packages register, listed in `font`
//            below, so a screen never hardcodes a string that the loader did
//            not actually register.

export const T = {
  bg:        '#0a0a0a',
  bgElev:    '#0d0d0d',
  ground:    '#070707',
  card:      '#111111',
  cardHi:    '#161616',
  border:    '#1e1e1e',
  green:     '#00E676',
  greenDim:  '#00A854',
  red:       '#FF4444',
  amber:     '#FFB300',
  blue:      '#42A5F5',
  white:     '#FFFFFF',
  muted:     '#AAAAAA',
  muted2:    '#6b6b6b',

  // Glass — the website's exact gradients, drawn with expo-linear-gradient
  // (operator ruling, Phase 2). The web's `linear-gradient(160deg, a, b)`
  // becomes the stop pair below plus GLASS_DIR; blur stays off, as on the
  // web, for scroll performance. The flat values are kept for surfaces that
  // cannot host a gradient view (inputs, tab chips).
  glassStops:   ['rgba(255,255,255,0.052)', 'rgba(255,255,255,0.014)'] as const,
  glassHiStops: ['rgba(255,255,255,0.085)', 'rgba(255,255,255,0.028)'] as const,
  glass:       'rgba(255,255,255,0.033)',
  glassHi:     'rgba(255,255,255,0.056)',
  glassLine:   'rgba(255,255,255,0.09)',
  glassLineHi: 'rgba(255,255,255,0.16)',

  // Spacing and radius scales — identical to the web so the two line up.
  s1: 6, s2: 10, s3: 14, s4: 20, s5: 28, s6: 40,
  r1: 10, r2: 14, r3: 18, r4: 24,
} as const

// CSS `160deg` is measured clockwise from "to top", so the gradient runs from
// the top-right-ish edge toward the bottom-left: start/end points below are the
// unit-square equivalent of that angle.
export const GLASS_DIR = { start: { x: 0.68, y: 0 }, end: { x: 0.32, y: 1 } } as const

// Registered font family names. The web stacks `"Barlow", -apple-system`; on
// iOS the fallback is the system font, which is what RN uses when a family is
// missing — so a typo here degrades to San Francisco rather than crashing.
export const F = {
  body:      'Barlow_400Regular',
  bodyMed:   'Barlow_500Medium',
  bodySemi:  'Barlow_600SemiBold',
  cond:      'BarlowCondensed_600SemiBold',
  condBold:  'BarlowCondensed_700Bold',
  condBlack: 'BarlowCondensed_800ExtraBold',
  condHeavy: 'BarlowCondensed_900Black',
} as const

// ── CONVICTION TIERS ─────────────────────────────────────────────────────────
// Copied from bits.jsx tier(): the same thresholds the website and the Discord
// board divider use (80+ elite, 72-79 strong). `weight` orders cards; `rgb` is
// the accent each tier paints with. Nothing here decides a pick — it only
// decides how a number the backend already produced is coloured.
export type Tier = { label: string; weight: number; rgb: string; tone: string }

export function tier(conf: number | null | undefined): Tier {
  const c = typeof conf === 'number' ? conf : -1
  if (c >= 80) return { label: 'ELITE',  weight: 3, rgb: '0,230,118',  tone: T.green }
  if (c >= 72) return { label: 'STRONG', weight: 2, rgb: '0,230,118',  tone: T.green }
  if (c >= 65) return { label: 'LEAN',   weight: 1, rgb: '255,179,0',  tone: T.amber }
  return               { label: '',       weight: 0, rgb: '107,107,107', tone: T.muted2 }
}

// OVER / UNDER colouring, shared by every card. Green over, red under — the
// same dots the Discord board prints.
export function sideTone(lean: string | null | undefined) {
  const s = (lean || '').toUpperCase()
  if (s === 'OVER')  return { side: 'OVER',  tone: T.green, rgb: '0,230,118' }
  if (s === 'UNDER') return { side: 'UNDER', tone: T.red,   rgb: '255,68,68' }
  return               { side: '',      tone: T.muted2, rgb: '107,107,107' }
}
