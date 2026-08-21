import { useState } from 'react'
import { T } from './theme'
import { sofaImg, initials } from './data'

// Player headshot. Sourced directly from Sofascore by id (the backend exposes
// no photo). Falls back to an initials avatar when there's no id or the image
// fails to load — never a fabricated/placeholder face.
export default function PlayerPhoto({ id, name, size = 64, ring = true }) {
  const [failed, setFailed] = useState(false)
  const src = sofaImg(id)
  const showImg = src && !failed

  const box = {
    width: size, height: size, borderRadius: '50%', flex: `0 0 ${size}px`,
    overflow: 'hidden', background: T.cardHi,
    border: ring ? `2px solid ${T.border}` : 'none',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
  }
  if (showImg) {
    return (
      <div style={box}>
        <img src={src} alt={name || 'player'} loading="lazy" onError={() => setFailed(true)}
          style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'top center' }} />
      </div>
    )
  }
  // ── THE INITIALS AVATAR IS THE REAL ONE ────────────────────────────────────
  // Sofascore answers 403 to every request for a player image — direct, with
  // full browser headers, and through our residential proxy — and the img.*
  // host 404s. There is no headshot source, so this fallback is what every
  // avatar in the app actually renders. It should therefore look deliberate
  // rather than like a failure state.
  //
  // The hue is derived from the NAME, so a given player is always the same
  // colour and a list reads as a set of distinct people instead of a column of
  // identical green discs. Deterministic, so it never changes between renders.
  const hue = (() => {
    let h = 0
    for (const ch of (name || '')) h = (h * 31 + ch.charCodeAt(0)) % 360
    return h
  })()
  const c1 = `hsl(${hue} 70% 58%)`
  const c2 = `hsl(${(hue + 38) % 360} 72% 44%)`
  return (
    <div style={{
      ...box,
      background: `linear-gradient(145deg, ${c1}22, ${c2}10)`,
      border: `2px solid ${c1}55`,
      boxShadow: `inset 0 0 18px ${c1}18`,
    }}>
      <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: size * 0.4,
                     color: c1, letterSpacing: 0.5,
                     textShadow: `0 0 14px ${c1}55` }}>
        {initials(name)}
      </span>
    </div>
  )
}
