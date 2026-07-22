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
  return (
    <div style={box}>
      <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: size * 0.38, color: T.green, letterSpacing: 0.5 }}>
        {initials(name)}
      </span>
    </div>
  )
}
