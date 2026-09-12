import { useState } from 'react'
import { T } from './theme'

// ── NFL VISUAL LANGUAGE ──────────────────────────────────────────────────────
// The player sheet had turned into a spreadsheet: every fact a label and a
// number in a grid, all the same size, all the same colour. Nothing was hard to
// read and nothing was worth looking at, which for a product whose whole claim
// is "we found an edge" is the wrong impression to give.
//
// These are the pieces that fix it. They live here rather than in bits.jsx
// because they are NFL-specific — a game log, a defensive rank, a team crest —
// whereas bits.jsx holds the language every sport shares (tiers, side colours,
// confidence bars). Those are still used; this sits on top of them.

// ── TEAM COLOURS ─────────────────────────────────────────────────────────────
// `c1` is the ACCENT, picked to be legible on a black background, which is not
// always the club's true primary: the Bears' navy, the Raiders' black and the
// Texans' steel blue all disappear on #0a0a0a, so those take the second colour.
// `c2` is a supporting tone for the gradient wash behind the header.
const TEAM = {
  ARI: { n: 'Cardinals', c1: '#C8355B', c2: '#000000' },
  ATL: { n: 'Falcons', c1: '#D62B45', c2: '#A5ACAF' },
  BAL: { n: 'Ravens', c1: '#7B5BD6', c2: '#9E7C0C' },
  BUF: { n: 'Bills', c1: '#4A83E8', c2: '#C60C30' },
  CAR: { n: 'Panthers', c1: '#0085CA', c2: '#BFC0BF' },
  CHI: { n: 'Bears', c1: '#E8662B', c2: '#0B162A' },
  CIN: { n: 'Bengals', c1: '#FB4F14', c2: '#000000' },
  CLE: { n: 'Browns', c1: '#FF6A2B', c2: '#311D00' },
  DAL: { n: 'Cowboys', c1: '#4C7FE0', c2: '#869397' },
  DEN: { n: 'Broncos', c1: '#FB4F14', c2: '#002244' },
  DET: { n: 'Lions', c1: '#3DA5E0', c2: '#B0B7BC' },
  GB:  { n: 'Packers', c1: '#FFB612', c2: '#2E5C46' },
  HOU: { n: 'Texans', c1: '#D8324B', c2: '#03202F' },
  IND: { n: 'Colts', c1: '#4B87D6', c2: '#A2AAAD' },
  JAX: { n: 'Jaguars', c1: '#D7A22A', c2: '#006778' },
  KC:  { n: 'Chiefs', c1: '#E31837', c2: '#FFB81C' },
  LA:  { n: 'Rams', c1: '#4C7FE0', c2: '#FFA300' },
  LAC: { n: 'Chargers', c1: '#0080C6', c2: '#FFC20E' },
  LV:  { n: 'Raiders', c1: '#C6CDD0', c2: '#000000' },
  MIA: { n: 'Dolphins', c1: '#00C2CE', c2: '#FC4C02' },
  MIN: { n: 'Vikings', c1: '#8355C9', c2: '#FFC62F' },
  NE:  { n: 'Patriots', c1: '#4573A8', c2: '#C60C30' },
  NO:  { n: 'Saints', c1: '#D3BC8D', c2: '#101820' },
  NYG: { n: 'Giants', c1: '#3A62C4', c2: '#A71930' },
  NYJ: { n: 'Jets', c1: '#2E9E6E', c2: '#000000' },
  PHI: { n: 'Eagles', c1: '#1E8C8C', c2: '#A5ACAF' },
  PIT: { n: 'Steelers', c1: '#FFB612', c2: '#101820' },
  SEA: { n: 'Seahawks', c1: '#69BE28', c2: '#002244' },
  SF:  { n: '49ers', c1: '#D13030', c2: '#B3995D' },
  TB:  { n: 'Buccaneers', c1: '#E8352F', c2: '#FF7900' },
  TEN: { n: 'Titans', c1: '#4B92DB', c2: '#0C2340' },
  WAS: { n: 'Commanders', c1: '#C4913E', c2: '#5A1414' },
}
// nflverse is not consistent across seasons, and a relocated club appears under
// its old code in older game logs. An unmapped team must fall back to the
// neutral grey rather than colouring the page by accident.
const ALIAS = { LAR: 'LA', STL: 'LA', SD: 'LAC', OAK: 'LV', WSH: 'WAS',
                ARZ: 'ARI', BLT: 'BAL', CLV: 'CLE', HST: 'HOU', JAC: 'JAX' }

export function team(abbr) {
  const k = String(abbr || '').toUpperCase()
  return TEAM[ALIAS[k] || k] || { n: k || '', c1: T.muted2, c2: '#1e1e1e' }
}

// The club crest, from ESPN's CDN — the same source nflverse publishes in its
// own teams table. Served through ESPN's resizer (`h`/`w`), which returns a 3KB
// PNG instead of the 36KB 500px original: thirty of these on a board is the
// difference between 90KB and 1MB.
//
// THE MONOGRAM IS STILL HERE, as the fallback. A hot-linked image can 404 on a
// club we mapped wrong, be blocked, or simply be unavailable with the app
// offline — and an empty box where a crest should be is worse than two letters
// in the right colours. `onError` swaps to it silently.
const logoUrl = (abbr, px) =>
  'https://a.espncdn.com/combiner/i?img=/i/teamlogos/nfl/500/'
  + `${String(abbr || '').toLowerCase()}.png&h=${px}&w=${px}`

export function TeamMark({ abbr, size = 42, plain = false }) {
  const t = team(abbr)
  const [failed, setFailed] = useState(false)
  const label = String(abbr || '').toUpperCase().slice(0, 3)
  // Ask for 2x so the crest stays sharp on a phone's retina screen.
  const px = Math.round(size * 2)

  const box = {
    width: size, height: size, borderRadius: size / 2, flexShrink: 0,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    overflow: 'hidden',
    background: plain ? 'transparent'
      : `linear-gradient(145deg, ${t.c1}1A, ${t.c2}0D)`,
    border: plain ? 'none' : `1.5px solid ${t.c1}59`,
    boxShadow: plain ? 'none' : T.shadow,
  }

  if (abbr && !failed) {
    return (
      <div style={box}>
        <img src={logoUrl(abbr, px)} alt={label} loading="lazy"
             onError={() => setFailed(true)}
             style={{ width: '76%', height: '76%', objectFit: 'contain',
                      filter: `drop-shadow(0 1px 4px ${t.c1}55)` }} />
      </div>
    )
  }
  return (
    <div style={{ ...box, fontFamily: T.cond, fontWeight: 800, color: t.c1,
                  fontSize: size * 0.34, letterSpacing: 0.5 }}>{label}</div>
  )
}

// The player's face, from ESPN by the id nflverse carries on the depth chart
// (nfl/queries.py publishes it as `espn_id`). Same resizer as the crest — the
// full headshot is ~250KB and this needs 120.
//
// Falls back to the club crest, never to a generic silhouette: a stand-in face
// is a claim about who someone is, and the crest at least says something true.
export function PlayerHead({ espnId, abbr, size = 62 }) {
  const [failed, setFailed] = useState(false)
  const t = team(abbr)
  const px = Math.round(size * 2)
  const src = espnId
    ? 'https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/'
      + `${espnId}.png&h=${px}&w=${px}&scale=crop`
    : null

  return (
    <div style={{
      width: size, height: size, borderRadius: size / 2, flexShrink: 0,
      overflow: 'hidden', display: 'flex', alignItems: 'center',
      justifyContent: 'center', position: 'relative',
      background: `linear-gradient(160deg, ${t.c1}1F, ${t.c2}0F)`,
      border: `2px solid ${t.c1}66`,
      boxShadow: T.shadow,
    }}>
      {src && !failed ? (
        <img src={src} alt="" loading="lazy" onError={() => setFailed(true)}
             style={{ width: '112%', height: '112%', objectFit: 'cover',
                      objectPosition: 'top center' }} />
      ) : (
        <TeamMark abbr={abbr} size={size * 0.62} plain />
      )}
    </div>
  )
}


// ── THE DEFENCE, ON A SCALE ──────────────────────────────────────────────────
// 32 ticks, one per club, with this one lit. A rank only means something
// against the field, and a number on its own makes the reader supply the field
// from memory.
export function DefenseMeter({ rank, of = 32, tone, abbr }) {
  if (!rank) return null
  const t = team(abbr)
  return (
    <div style={{ marginTop: 9 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 22 }}>
        {Array.from({ length: of }, (_, i) => {
          const here = i + 1 === rank
          return (
            <div key={i} style={{
              flex: 1, borderRadius: 1.5,
              height: here ? 22 : 8 + (i / of) * 5,
              background: here ? tone : T.border,
              boxShadow: 'none',
            }} />
          )
        })}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between',
                    color: T.muted2, fontSize: 9, marginTop: 4 }}>
        <span>TOUGHEST</span>
        <span style={{ color: t.c1, fontWeight: 800 }}>{String(abbr || '')}</span>
        <span>SOFTEST</span>
      </div>
    </div>
  )
}


