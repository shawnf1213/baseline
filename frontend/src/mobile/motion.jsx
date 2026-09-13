import { motion } from 'motion/react'
import NumberFlow from '@number-flow/react'
import { T } from './theme'

// ── THE MOTION LAYER ─────────────────────────────────────────────────────────
// Everything in this app arrived fully-formed and sat still. That is the single
// biggest reason it read as flat next to the sites it is being compared to —
// not colour, not spacing. A number that lands on 7.4 has been computed; a
// number that counts to 7.4 has been WORKED OUT, and that difference is most of
// what people mean when they say an interface feels alive.
//
// NOTHING NEW WAS INSTALLED FOR THIS. framer-motion and @number-flow/react were
// already dependencies and almost entirely unused — only Card animated.
//
// EVERY PIECE RESPECTS prefers-reduced-motion. Vestibular disorders are real
// and an app about numbers must not become unusable to make a point about feel.
const REDUCED = typeof window !== 'undefined'
  && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

const EASE = [0.16, 1, 0.3, 1]

// Reveal on mount, staggered by position. `i` is the item's index in its list —
// a section of four cards cascades instead of appearing as a block.
export function Reveal({ children, i = 0, y = 12, style, ...rest }) {
  // Past the fold there is nothing to reveal to anybody — see bits.jsx::Card.
  if (REDUCED || i > 8) {
    return <div style={style} {...rest}>{children}</div>
  }
  return (
    <motion.div
      initial={REDUCED ? false : { opacity: 0, y }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.42, ease: EASE,
                    // Capped: past ~8 items the last one visibly lags behind
                    // the scroll and reads as jank rather than choreography.
                    delay: REDUCED ? 0 : Math.min(i, 8) * 0.05 }}
      style={style} {...rest}>{children}</motion.div>
  )
}

// A number that arrives at its value. NumberFlow animates digit-by-digit, which
// is the effect worth having — a plain tween through 3.7, 4.1, 5.9 on the way
// to 7.4 shows numbers the model never produced.
export function Num({ value, decimals = 1, prefix = '', suffix = '', style }) {
  if (typeof value !== 'number' || !isFinite(value)) {
    return <span style={style}>—</span>
  }
  if (REDUCED) {
    return <span style={style}>{prefix}{value.toFixed(decimals)}{suffix}</span>
  }
  return (
    <span style={style}>
      <NumberFlow value={value} prefix={prefix} suffix={suffix}
                  format={{ minimumFractionDigits: decimals,
                            maximumFractionDigits: decimals }}
                  transformTiming={{ duration: 700, easing: 'ease-out' }} />
    </span>
  )
}

// A bar that grows to its width. Same argument as the number: the length is the
// datum, and watching it arrive is what makes it read as measured.
export function GrowBar({ pct, tone = T.green, height = 6, delay = 0, track }) {
  const w = `${Math.max(0, Math.min(100, pct || 0))}%`
  return (
    <div style={{ height, borderRadius: height, overflow: 'hidden',
                  background: track || 'rgba(255,255,255,0.07)' }}>
      <motion.div
        initial={REDUCED ? false : { width: 0 }}
        animate={{ width: w }}
        transition={{ duration: 0.8, ease: EASE, delay: REDUCED ? 0 : delay }}
        style={{ height: '100%', borderRadius: height, background: tone }} />
    </div>
  )
}

// A ring that draws itself. Used wherever a percentage is the headline.
export function Ring({ pct, size = 96, stroke = 8, tone = T.green,
                       children, delay = 0 }) {
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const v = Math.max(0, Math.min(100, pct || 0))
  return (
    <div style={{ position: 'relative', width: size, height: size,
                  flexShrink: 0 }}>
      <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none"
                stroke="rgba(255,255,255,0.07)" strokeWidth={stroke} />
        <motion.circle
          cx={size / 2} cy={size / 2} r={r} fill="none" stroke={tone}
          strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={c}
          initial={REDUCED ? false : { strokeDashoffset: c }}
          animate={{ strokeDashoffset: c - (c * v) / 100 }}
          transition={{ duration: 1, ease: EASE, delay: REDUCED ? 0 : delay }} />
      </svg>
      <div style={{ position: 'absolute', inset: 0, display: 'flex',
                    flexDirection: 'column', alignItems: 'center',
                    justifyContent: 'center' }}>{children}</div>
    </div>
  )
}

// A press response. Touch targets that do not acknowledge a tap feel broken on
// a phone long before they feel plain.
export function Tap({ children, onClick, style, plain, ...rest }) {
  // whileTap costs a motion component per instance, and PropRow puts one in
  // every row. On a list that is hundreds of them for a press response the
  // reader gets on one. `plain` opts a list row out; the CSS :active in
  // index.css covers the feedback.
  if (REDUCED || plain) {
    return (
      <div onClick={onClick} className="tap-press"
           style={{ WebkitTapHighlightColor: 'transparent',
                    cursor: onClick ? 'pointer' : undefined, ...style }}
           {...rest}>{children}</div>
    )
  }
  return (
    <motion.div onClick={onClick}
      whileTap={REDUCED ? undefined : { scale: 0.985 }}
      style={{ WebkitTapHighlightColor: 'transparent',
               cursor: onClick ? 'pointer' : undefined, ...style }}
      {...rest}>{children}</motion.div>
  )
}

// ── THE EDGE, AS A DISTANCE ──────────────────────────────────────────────────
// "31.3 | −19.2" in a box is two numbers you have to hold in your head and
// subtract. The edge IS a gap between two points, so this draws it as one: the
// book's number and ours on a shared track with the space between them filled.
// A reader sees how far apart they are before reading either figure, which is
// the whole claim the product makes.
//
// THE LABELS ARE FIXED, THE MARKERS ARE NOT. They used to be centred on their
// own values, which works only while the two are far apart — on a thin edge
// (21.4 against 21.5) the two labels sat on the same few pixels and printed
// over each other. Book on the left, ours on the right, always, and the reader
// ties each to its dot by COLOUR rather than by position: the book is grey like
// its marker, ours carries the lean like its own.
//
// The dots still move, so the gap between them is still the picture — and when
// there is barely an edge they sit almost on top of each other, which is
// exactly what that means.
export function EdgeScale({ line, proj, tone, rgb }) {
  if (typeof line !== 'number' || typeof proj !== 'number') return null
  const lo = Math.min(line, proj), hi = Math.max(line, proj)
  // Pad beyond the pair so neither marker ever sits on an end — a dot pinned to
  // the edge of a track reads as clipped rather than extreme.
  const pad = Math.max((hi - lo) * 0.35, Math.abs(hi) * 0.06, 0.5)
  const a = lo - pad, b = hi + pad
  const at = (v) => ((v - a) / (b - a || 1)) * 100
  const lPct = at(line), pPct = at(proj)
  const from = Math.min(lPct, pPct), width = Math.abs(pPct - lPct)
  const dot = (c, d, p, delay) => (
    <motion.div key={p}
      initial={REDUCED ? false : { scale: 0, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ duration: 0.35, ease: EASE, delay }}
      style={{ position: 'absolute', left: `${p}%`, top: '50%',
               width: d, height: d, marginLeft: -d / 2, marginTop: -d / 2,
               borderRadius: d, background: c,
               border: `2px solid ${T.ground}` }} />
  )

  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 8.5,
                        letterSpacing: 1, textTransform: 'uppercase',
                        color: T.muted2 }}>Book line</div>
          <div style={{ fontSize: 14, fontWeight: 800, color: T.muted,
                        lineHeight: 1.15,
                        fontVariantNumeric: 'tabular-nums' }}>{line}</div>
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ minWidth: 0, textAlign: 'right' }}>
          <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 8.5,
                        letterSpacing: 1, textTransform: 'uppercase',
                        color: T.muted2 }}>Baseline</div>
          <div style={{ fontSize: 17, fontWeight: 800, color: tone || T.white,
                        lineHeight: 1.15,
                        fontVariantNumeric: 'tabular-nums' }}>{proj}</div>
        </div>
      </div>

      <div style={{ position: 'relative', height: 14, marginTop: 6 }}>
        <div style={{ position: 'absolute', left: 0, right: 0, top: '50%',
                      marginTop: -2, height: 4, borderRadius: 3,
                      background: 'rgba(255,255,255,0.07)' }} />
        <motion.div
          initial={REDUCED ? false : { width: 0 }}
          animate={{ width: `${width}%` }}
          transition={{ duration: 0.75, ease: EASE, delay: 0.1 }}
          style={{ position: 'absolute', left: `${from}%`, top: '50%',
                   marginTop: -2, height: 4, borderRadius: 3,
                   background: `linear-gradient(90deg, rgba(${rgb},0.35),`
                             + ` rgba(${rgb},0.95))` }} />
        {dot(T.muted2, 8, lPct, 0.35)}
        {dot(tone || T.white, 11, pPct, 0.47)}
      </div>
    </div>
  )
}

