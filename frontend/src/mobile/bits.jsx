import { motion } from 'framer-motion'
import { T, HEADLINE_FILL } from './theme'
import { GrowBar, Reveal, Tap } from './motion'
import { fmtSigned } from './data'

// ── SHARED SURFACES ──────────────────────────────────────────────────────────
// Card and Chip are used by every tab, so this file is where motion and depth
// buy the most: one change here reaches Boards, Picks, Players, Search and
// Projections at once.
//
// The app had framer-motion and gsap installed and imported in ZERO files, and
// not one transition or animation anywhere in src/mobile. It read as flat
// because nothing ever moved or responded, not because anything was missing.
//
// Motion here is functional rather than decorative: cards rise as they arrive
// so a list reads as arriving rather than blinking into place, and everything
// tappable shrinks under the finger so a touch is acknowledged before the
// network answers. Durations stay under ~250ms — past that it stops feeling
// responsive and starts feeling slow.

// Respect a user who has asked the OS for less motion. Ignoring that setting
// causes real discomfort for people with vestibular conditions, and the cost of
// honouring it is one media query.
const REDUCED = typeof window !== 'undefined'
  && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

// The entrance stagger is capped at this index. Past it a card is below the
// fold on any screen, so it can never be SEEN arriving — and paying for a
// motion component to animate something nobody watches is the whole cost with
// none of the benefit. A board of 168 rows was mounting 168 of these.
const ANIMATE_UP_TO = 8

export function Card({ children, style, onClick, index = 0, ...rest }) {
  const base = {
    // GLASS, matching the landing page's cards. Translucent rather than a
    // solid dark fill, so the mesh ground shows through and the app reads
    // as the same surface as its own front door. No backdrop-filter — see
    // theme.js; blurring every card is what tanked scrolling before.
    background: T.glass,
    border: `1px solid ${T.glassLine}`, borderRadius: T.r3,
    boxShadow: '0 1px 0 rgba(255,255,255,0.06) inset, 0 10px 30px rgba(0,0,0,0.45)',
    ...(onClick ? { cursor: 'pointer', WebkitTapHighlightColor: 'transparent' } : null),
    ...style,
  }

  // A PLAIN DIV once past the fold. Identical styling, none of the per-frame
  // subscription — and because it never animates, its final state IS its only
  // state, so nothing about how the list looks changes.
  if (REDUCED || index > ANIMATE_UP_TO) {
    return <div onClick={onClick} style={base} {...rest}>{children}</div>
  }

  return (
    <motion.div
      onClick={onClick}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1],
                    delay: Math.min(index, 6) * 0.035 }}
      whileTap={onClick ? { scale: 0.985 } : undefined}
      style={base} {...rest}>{children}</motion.div>
  )
}

// Filter / segment chip — 40px+ tall touch target.
export function Chip({ active, onClick, children, style }) {
  return (
    <motion.button
      onClick={onClick}
      whileTap={REDUCED ? undefined : { scale: 0.93 }}
      transition={{ type: 'spring', stiffness: 520, damping: 30 }}
      style={{
        minHeight: 42, padding: '8px 18px', borderRadius: 999,
        fontFamily: T.cond, fontWeight: 800, fontSize: 13.5, letterSpacing: 1,
        textTransform: 'uppercase', whiteSpace: 'nowrap', cursor: 'pointer',
        border: `1px solid ${active ? `${T.green}88` : T.glassLine}`,
        // AN UNSELECTED CHIP HAD NO FILL, so it read as a text label with a
        // hairline round it rather than as a button you can press.
        background: active
          ? `linear-gradient(160deg, ${T.green}2E, ${T.green}12)`
          : 'rgba(255,255,255,0.035)',
        color: active ? T.green : T.muted,
        boxShadow: active ? `0 4px 16px ${T.green}22` : 'none',
        transition: 'background 140ms ease, color 140ms ease, box-shadow 180ms ease',
        ...style,
      }}>{children}</motion.button>
  )
}

// Neutral edge delta (model projection − line). No value-judgment coloring.
export function Delta({ value, size = 15 }) {
  return (
    <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: size, color: T.white, letterSpacing: 0.4 }}>
      {fmtSigned(value)}
    </span>
  )
}

export function Heart({ active, onClick, size = 22 }) {
  return (
    <button onClick={(e) => { e.stopPropagation(); onClick?.() }} aria-label="bookmark" style={{
      background: 'transparent', border: 'none', cursor: 'pointer', padding: 8,
      minWidth: 40, minHeight: 40, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      WebkitTapHighlightColor: 'transparent',
    }}>
      <svg width={size} height={size} viewBox="0 0 24 24"
        fill={active ? T.green : 'none'} stroke={active ? T.green : T.muted}
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z" />
      </svg>
    </button>
  )
}

// Two-or-more-way switcher used as a section-level tab bar (Boards: PP/UD).
export function Segment({ options, value, onChange, style }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: `repeat(${options.length}, 1fr)`, gap: 4,
      background: T.card, border: `1px solid ${T.border}`, borderRadius: 12, padding: 4, ...style,
    }}>
      {options.map(o => {
        const on = o.key === value
        return (
          <button key={o.key} onClick={() => onChange(o.key)} style={{
            minHeight: 38, borderRadius: 9, border: 'none', cursor: 'pointer',
            background: on ? 'rgba(0,230,118,0.14)' : 'transparent',
            color: on ? T.green : T.muted,
            fontFamily: T.cond, fontWeight: 800, fontSize: 13, letterSpacing: 1,
            textTransform: 'uppercase', WebkitTapHighlightColor: 'transparent',
            transition: 'all 140ms ease',
          }}>{o.label}</button>
        )
      })}
    </div>
  )
}

// Settled state of a tracked pick. PUSH reads as a win because that is how the
// record scores it (W+PUSH over W+L+PUSH), so the badge must not imply otherwise.
export function ResultBadge({ tone, label, value }) {
  const c = tone === 'win' ? T.green : tone === 'loss' ? T.red : tone === 'void' ? T.muted2 : T.amber
  const bg = tone === 'win' ? 'rgba(0,230,118,0.12)'
           : tone === 'loss' ? 'rgba(255,82,82,0.12)'
           : tone === 'void' ? 'rgba(255,255,255,0.05)' : 'rgba(255,193,7,0.10)'
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 9px', borderRadius: 7,
      background: bg, border: `1px solid ${c}33`, color: c,
      fontFamily: T.cond, fontWeight: 800, fontSize: 11, letterSpacing: 1, textTransform: 'uppercase',
    }}>
      {label}{value != null && <span style={{ opacity: 0.75, letterSpacing: 0.2 }}>{value}</span>}
    </span>
  )
}

export function Spinner({ size = 22 }) {
  return (
    <span style={{
      width: size, height: size, display: 'inline-block',
      border: `2.5px solid ${T.border}`, borderTopColor: T.green,
      borderRadius: '50%', animation: 'baseline-spin 0.7s linear infinite',
    }} />
  )
}

export function Empty({ title, hint, icon }) {
  return (
    <div style={{ textAlign: 'center', padding: '48px 24px', color: T.muted }}>
      {icon && <div style={{ fontSize: 34, marginBottom: 10, opacity: 0.7 }}>{icon}</div>}
      <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 17, color: T.white, letterSpacing: 0.5 }}>{title}</div>
      {hint && <div style={{ fontSize: 13.5, marginTop: 8, lineHeight: 1.5, maxWidth: 320, marginLeft: 'auto', marginRight: 'auto' }}>{hint}</div>}
    </div>
  )
}

// The hero's eyebrow, reused. A hairline runs from the label to the right-hand
// slot so a section reads as a band across the page rather than a loose caption
// floating above a list.
export function SectionLabel({ children, right }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12,
                  margin: `${T.s4}px 2px ${T.s2}px` }}>
      <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 12,
                     letterSpacing: 2.2, textTransform: 'uppercase',
                     color: T.green, whiteSpace: 'nowrap' }}>{children}</span>
      <span style={{ flex: 1, height: 1, background:
        `linear-gradient(90deg, ${T.glassLineHi}, rgba(255,255,255,0.02))` }} />
      {right}
    </div>
  )
}

// ── PAGE FURNITURE FROM THE LANDING PAGE ────────────────────────────────────
// Every tab had built its own 26px condensed heading inline. These are the
// hero's, so a screen title inside the app is the same object as the one on the
// marketing page rather than a near-miss of it.

export function PageTitle({ children, sub, right }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12,
                  marginBottom: T.s3 }}>
      <div style={{ minWidth: 0, flex: 1 }}>
        <h1 style={{
          fontFamily: T.cond, fontWeight: 800, margin: 0,
          fontSize: 'clamp(30px, 5.4vw, 40px)', lineHeight: 1, letterSpacing: 0.2,
          ...HEADLINE_FILL,
        }}>{children}</h1>
        {sub ? (
          <div style={{ color: T.muted, fontSize: 12.5, marginTop: 7,
                        display: 'flex', alignItems: 'center', gap: 7,
                        flexWrap: 'wrap' }}>{sub}</div>
        ) : null}
      </div>
      {right}
    </div>
  )
}

// The hero's status pill. `live` adds the pulsing dot.
export function Pill({ children, tone = T.green, live, style }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 7,
      padding: '5px 11px', borderRadius: 999,
      background: `${tone}14`, border: `1px solid ${tone}3D`,
      color: tone, fontSize: 11.5, fontWeight: 700, ...style,
    }}>
      {live ? <span className="live-dot" style={{ width: 6, height: 6,
                                                  background: tone }} /> : null}
      {children}
    </span>
  )
}

// A row of glass tabs — the sport switch, the book switch, anything that picks
// one of two or three. Replaces three separately-styled button rows that had
// each drifted to different heights and radii.
export function GlassTabs({ options, value, onChange, style }) {
  return (
    <div style={{ display: 'flex', gap: 6, padding: 5, borderRadius: T.r3,
                  background: 'rgba(255,255,255,0.025)',
                  border: `1px solid ${T.glassLine}`, ...style }}>
      {options.map(o => {
        const on = o.key === value
        return (
          <button key={o.key} onClick={() => onChange(o.key)} style={{
            flex: 1, minHeight: 42, borderRadius: T.r2, cursor: 'pointer',
            border: on ? `1px solid ${T.green}66` : '1px solid transparent',
            background: on
              ? `linear-gradient(160deg, ${T.green}2E, ${T.green}12)`
              : 'rgba(255,255,255,0.02)',
            color: on ? T.green : T.muted2,
            fontFamily: T.cond, fontWeight: 800, fontSize: 14.5,
            letterSpacing: 1.2, textTransform: 'uppercase',
            boxShadow: on ? `0 4px 14px ${T.green}1F` : 'none',
            transition: 'background 180ms ease, color 180ms ease',
            WebkitTapHighlightColor: 'transparent',
          }}>{o.label}</button>
        )
      })}
    </div>
  )
}

// ── ONE PERSON, ONE ROW ──────────────────────────────────────────────────────
// Players, Search and the saved-players list each drew this themselves, three
// times, with three different paddings, avatar sizes and chevrons — the same
// object rendered three ways in one app. `right` takes whatever that screen
// knows that the others do not: a prop count, a rank, an unsave heart.
export function PersonRow({ name, photo, meta, right, onClick, index = 0 }) {
  return (
    <Reveal i={index}>
      <Tap onClick={onClick} style={{
        display: 'flex', alignItems: 'center', gap: 12,
        padding: '10px 12px 10px 14px', marginBottom: 8, borderRadius: T.r3,
        background: T.glass, border: `1px solid ${T.glassLine}`,
        boxShadow: '0 6px 18px rgba(0,0,0,0.35)',
      }}>
        {photo}
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 17,
                        color: T.white, letterSpacing: 0.3, lineHeight: 1.15,
                        whiteSpace: 'nowrap', overflow: 'hidden',
                        textOverflow: 'ellipsis' }}>{name}</div>
          {meta ? (
            <div style={{ color: T.muted, fontSize: 12.5, marginTop: 1,
                          whiteSpace: 'nowrap', overflow: 'hidden',
                          textOverflow: 'ellipsis' }}>{meta}</div>
          ) : null}
        </div>
        {right}
        {onClick ? (
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none"
               stroke={T.muted2} strokeWidth="2" strokeLinecap="round"
               style={{ flexShrink: 0 }}><path d="M9 6l6 6-6 6" /></svg>
        ) : null}
      </Tap>
    </Reveal>
  )
}

// Tiny sparkline-style bars for a series of values (recent match values / hit strip).
export function MiniBars({ values, refLine }) {
  const nums = (values || []).filter(v => typeof v === 'number')
  if (!nums.length) return <span style={{ color: T.muted2, fontSize: 12 }}>no log</span>
  const max = Math.max(...nums, refLine || 0)
  const min = Math.min(...nums, refLine || Infinity)
  const span = Math.max(1, max - Math.min(min, 0))
  // Series is newest-first from the API; show oldest→newest left→right.
  const series = [...values].reverse()
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 34 }}>
      {series.map((v, i) => {
        if (typeof v !== 'number') return <div key={i} style={{ width: 7 }} />
        const h = Math.max(3, ((v - Math.min(min, 0)) / span) * 34)
        const over = refLine != null && v > refLine
        const under = refLine != null && v < refLine
        return (
          <div key={i} title={String(v)} style={{
            width: 7, height: h, borderRadius: 2,
            background: refLine == null ? T.muted2 : over ? T.green : under ? T.red : T.amber,
            opacity: 0.85,
          }} />
        )
      })}
    </div>
  )
}

// ── ONE VISUAL LANGUAGE, DEFINED ONCE ────────────────────────────────────────
// These are the pieces the Boards redesign introduced, lifted out of BoardTab
// so every screen speaks the same language instead of each growing its own
// one-off treatment. A confidence of 78 must look the same on the board, in the
// pick log, on a saved item and inside a player screen — otherwise a reader has
// to relearn the interface on every tab, and inconsistency reads as sloppiness
// far more than plainness does.

// Confidence bands. THESE ARE THE BANDS confidence.py ALREADY USES (80/72/64),
// so what the eye is told matches what the model said. A card must never look
// elite while scoring 61: an interface that oversells the model is worse than
// a plain one.
export function tier(conf) {
  if (conf == null) return { key: 'none', label: '', weight: 0 }
  if (conf >= 80) return { key: 'elite', label: 'ELITE', weight: 3 }
  if (conf >= 72) return { key: 'strong', label: 'STRONG', weight: 2 }
  if (conf >= 64) return { key: 'lean', label: 'LEAN', weight: 1 }
  return { key: 'thin', label: '', weight: 0 }
}

// Colour for a side. Accepts a lean string or a signed edge, because different
// screens hold one or the other and neither should have to convert.
export function sideTone(leanOrEdge) {
  const s = typeof leanOrEdge === 'number'
    ? (leanOrEdge > 0 ? 'OVER' : leanOrEdge < 0 ? 'UNDER' : null)
    : (leanOrEdge || '').toUpperCase()
  if (s === 'OVER') return { side: 'OVER', tone: T.green, rgb: '0,230,118' }
  if (s === 'UNDER') return { side: 'UNDER', tone: T.red, rgb: '255,68,68' }
  return { side: null, tone: T.muted2, rgb: '107,107,107' }
}

// The coloured edge of a card: which way, and how strongly. Absolutely
// positioned, so any Card using it needs position:relative and overflow:hidden.
export function SideRail({ rgb, weight = 1, tone }) {
  return (
    <div style={{
      position: 'absolute', left: 0, top: 0, bottom: 0,
      // 3px did not register at arm's length on a phone. The rail is the
      // fastest read on a card and has to be legible without looking for it.
      width: weight >= 3 ? 7 : weight === 2 ? 6 : 4,
      background: rgb
        ? `linear-gradient(180deg, rgba(${rgb},0.95), rgba(${rgb},0.35))`
        : (tone || T.border),
    }} />
  )
}

export function TierBadge({ conf, tone, rgb }) {
  const tr = tier(conf)
  if (!tr.label) return null
  return (
    <span style={{
      fontFamily: T.cond, fontWeight: 800, fontSize: 9.5, letterSpacing: 1.2,
      color: tone, padding: '2.5px 7px', borderRadius: 6,
      background: `rgba(${rgb},0.13)`, border: `1px solid rgba(${rgb},0.34)`,
      boxShadow: 'none',
    }}>{tr.label}</span>
  )
}

// Confidence as something you can see the size of, rather than a bare number.
export function ConfBar({ conf, tone, max = 132 }) {
  if (conf == null) return null
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 7, flex: 1, maxWidth: max }}>
      <div style={{ flex: 1 }}>
        <GrowBar pct={conf} tone={tone} height={6} track="#191919" />
      </div>
      <span style={{ fontSize: 11.5, fontWeight: 800, color: tone,
                     fontVariantNumeric: 'tabular-nums' }}>{Math.round(conf)}</span>
    </div>
  )
}

// The one number a card exists to show, in a tinted well.
export function BigStat({ value, label, proj, tone, rgb, muted }) {
  // TWO NUMBERS, BOTH LEGIBLE. The projection is the model's actual answer and
  // it was 9px grey text tucked under the edge — smaller than the line it is
  // being compared against, and the quietest thing on a card built to show it.
  //
  // The edge keeps the colour because it carries the direction and drives the
  // card's conviction; the projection is white, because it is a measurement
  // rather than a verdict and colouring it would imply a side it does not have.
  const showProj = proj !== undefined && proj !== null && proj !== ''
  return (
    <div style={{
      minWidth: showProj ? 132 : 90, padding: '8px 11px', borderRadius: 12,
      background: muted ? 'transparent' : `rgba(${rgb},0.10)`,
      border: `1px solid ${muted ? T.border : `rgba(${rgb},0.30)`}`,
      display: 'flex', alignItems: 'center',
      justifyContent: showProj ? 'space-between' : 'center', gap: 10,
    }}>
      {showProj && (
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 22, fontWeight: 800, color: T.white,
                        lineHeight: 1.05, fontVariantNumeric: 'tabular-nums' }}>{proj}</div>
          <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9,
                        letterSpacing: 1.2, color: T.muted2 }}>PROJ</div>
        </div>
      )}
      {showProj && (
        <div style={{ width: 1, alignSelf: 'stretch',
                      background: `rgba(${rgb},0.28)` }} />
      )}
      <div style={{ textAlign: 'center' }}>
        <div style={{ fontSize: showProj ? 22 : 28, fontWeight: 800,
                      color: muted ? T.muted2 : tone,
                      lineHeight: 1.05, fontVariantNumeric: 'tabular-nums' }}>{value}</div>
        <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9,
                      letterSpacing: 1.2, color: T.muted2 }}>{label}</div>
      </div>
    </div>
  )
}

// Card styling for a conviction tier — border and glow scale together so the
// list has a visible top end rather than one flat texture.
export function tierCardStyle(conf, rgb) {
  const w = tier(conf).weight
  return {
    border: `1px solid ${w >= 3 ? `rgba(${rgb},0.42)`
                       : w === 2 ? `rgba(${rgb},0.22)` : T.glassLine}`,
    boxShadow: w >= 3
      ? `0 0 0 1px rgba(${rgb},0.22), 0 10px 30px rgba(${rgb},0.10),`
        + ' 0 10px 28px rgba(0,0,0,0.5)'
      : '0 10px 28px rgba(0,0,0,0.45)',
  }
}
