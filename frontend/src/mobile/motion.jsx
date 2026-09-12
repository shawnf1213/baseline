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
export function Tap({ children, onClick, style, ...rest }) {
  return (
    <motion.div onClick={onClick}
      whileTap={REDUCED ? undefined : { scale: 0.985 }}
      style={{ WebkitTapHighlightColor: 'transparent',
               cursor: onClick ? 'pointer' : undefined, ...style }}
      {...rest}>{children}</motion.div>
  )
}
