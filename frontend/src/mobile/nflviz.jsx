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

// A club monogram. No crest images: 32 logos are licensed marks, they would
// have to be bundled or hot-linked, and a wrong or missing one looks far worse
// than two letters in the right colours.
export function TeamMark({ abbr, size = 42 }) {
  const t = team(abbr)
  const label = String(abbr || '').toUpperCase().slice(0, 3)
  return (
    <div style={{
      width: size, height: size, borderRadius: size / 2, flexShrink: 0,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: `linear-gradient(145deg, ${t.c1}2E, ${t.c2}22)`,
      border: `1.5px solid ${t.c1}88`,
      boxShadow: `0 0 18px ${t.c1}33, inset 0 1px 0 ${t.c1}22`,
      fontFamily: T.cond, fontWeight: 800, color: t.c1,
      fontSize: size * 0.34, letterSpacing: 0.5,
    }}>{label}</div>
  )
}

// ── THE GAME LOG ─────────────────────────────────────────────────────────────
// The centrepiece, and the thing a 7px grey bar strip could never be. Every
// game against the line, with the bar coloured by WHETHER OUR SIDE WOULD HAVE
// CASHED that week — not by whether he went over. On an UNDER those are
// opposites, and colouring by "over" would show a wall of red for a play we
// like, which is the single most misleading thing this chart could do.
// HTML AND CSS, NOT SVG. An SVG sized `width:100%` with a fixed viewBox either
// letterboxes or, with preserveAspectRatio="none", scales x and y by different
// factors — which stretches every label. This card runs from a 360px phone to a
// 760px column, so the text would have been visibly squashed on one and smeared
// on the other. Divs scale without distorting their type.
export function GameLogChart({ games, line, over, accent = T.green, height = 116 }) {
  const pts = (games || []).filter(g => typeof g.v === 'number')
  if (!pts.length) return null

  const hi = Math.max(...pts.map(p => p.v), line ?? 0)
  const top = hi * 1.16 || 1
  const h = (v) => `${(Math.max(0, v) / top) * 100}%`
  // Values crowd badly past a dozen bars on a phone, so a long log keeps the
  // opponent and drops the number. The bars still carry the shape, and the
  // exact figures are in the game log below.
  const showVals = pts.length <= 12
  const cashed = (v) => (line == null || v === line ? null
    : over ? v > line : v < line)

  return (
    <div style={{ width: '100%' }}>
      <div style={{ position: 'relative', height, display: 'flex',
                    alignItems: 'flex-end', gap: 3 }}>
        {/* The line, drawn THROUGH the bars rather than beside them — the whole
            question is which bars finish above it. */}
        {line != null && (
          <div style={{
            position: 'absolute', left: 0, right: 0, height: 0,
            bottom: `${Math.min(100, (line / top) * 100)}%`,
            borderTop: `1.5px dashed ${accent}`, opacity: 0.9,
            boxShadow: `0 0 12px ${accent}55`, pointerEvents: 'none', zIndex: 2,
          }} />
        )}

        {pts.map((p, i) => {
          const c = cashed(p.v)
          const tone = c == null ? T.amber : c ? T.green : T.red
          return (
            <div key={i} title={`${p.opp || ''} ${p.v}`} style={{
              flex: 1, minWidth: 0, height: '100%', display: 'flex',
              flexDirection: 'column', justifyContent: 'flex-end',
              alignItems: 'center',
            }}>
              {showVals && (
                <div style={{ fontSize: 9.5, fontWeight: 700, color: T.muted,
                              marginBottom: 2, lineHeight: 1,
                              fontVariantNumeric: 'tabular-nums' }}>{p.v}</div>
              )}
              <div style={{
                width: '100%', maxWidth: 26, height: h(p.v), minHeight: 2,
                borderRadius: '4px 4px 2px 2px',
                background: `linear-gradient(180deg, ${tone}F2, ${tone}3D)`,
                border: `1px solid ${tone}8C`, borderBottom: 'none',
                boxShadow: c ? `0 0 10px ${tone}44` : 'none',
              }} />
            </div>
          )
        })}
      </div>

      <div style={{ display: 'flex', gap: 3, marginTop: 4 }}>
        {pts.map((p, i) => (
          <div key={i} style={{
            flex: 1, minWidth: 0, textAlign: 'center', fontSize: 8.5,
            color: T.muted2, whiteSpace: 'nowrap', overflow: 'hidden',
          }}>{p.opp || (p.wk != null ? `w${p.wk}` : '')}</div>
        ))}
      </div>
    </div>
  )
}

// ── HIT RATE, AS A SHAPE ─────────────────────────────────────────────────────
// "13/16 · 81%" is a cell in a table. This is the same fact you can read at a
// glance from across the room, which is the whole difference between a tool and
// a spreadsheet.
export function HitRing({ hits, n, size = 74, label = 'HIT RATE' }) {
  if (!n) return null
  const pct = Math.round((hits / n) * 100)
  const r = (size - 9) / 2
  const c = 2 * Math.PI * r
  // Banded against the break-even a prop actually has to clear, not against
  // 50%: a 52% hit rate is not a green light, and colouring it like one is how
  // an interface flatters a model.
  const tone = pct >= 70 ? T.green : pct >= 58 ? '#9ACD32'
             : pct >= 50 ? T.amber : T.red
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center',
                  gap: 4 }}>
      <div style={{ position: 'relative', width: size, height: size }}>
        <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
          <circle cx={size / 2} cy={size / 2} r={r} fill="none"
                  stroke="#1c1c1c" strokeWidth="6.5" />
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={tone}
                  strokeWidth="6.5" strokeLinecap="round"
                  strokeDasharray={`${(c * pct) / 100} ${c}`}
                  style={{ filter: `drop-shadow(0 0 6px ${tone}88)` }} />
        </svg>
        <div style={{ position: 'absolute', inset: 0, display: 'flex',
                      flexDirection: 'column', alignItems: 'center',
                      justifyContent: 'center', gap: 0 }}>
          <span style={{ fontSize: size * 0.30, fontWeight: 800, color: tone,
                         lineHeight: 1, fontVariantNumeric: 'tabular-nums' }}>
            {pct}
          </span>
          <span style={{ fontSize: 9, color: T.muted2, fontWeight: 700 }}>
            {hits}/{n}
          </span>
        </div>
      </div>
      <span style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5,
                     letterSpacing: 1.1, color: T.muted2 }}>{label}</span>
    </div>
  )
}

// The last n games as a form strip — the read every football table uses,
// because a run of five reds says something an average never will.
export function FormStrip({ games, line, over, max = 8 }) {
  const pts = (games || []).filter(g => typeof g.v === 'number').slice(-max)
  if (!pts.length || line == null) return null
  return (
    <div style={{ display: 'flex', gap: 3.5 }}>
      {pts.map((p, i) => {
        const c = p.v === line ? null : over ? p.v > line : p.v < line
        const tone = c == null ? T.amber : c ? T.green : T.red
        return (
          <div key={i} title={`${p.opp || ''} ${p.v}`} style={{
            width: 15, height: 19, borderRadius: 4,
            background: `${tone}26`, border: `1px solid ${tone}99`,
            color: tone, fontSize: 8.5, fontWeight: 800,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>{c == null ? 'P' : c ? 'W' : 'L'}</div>
        )
      })}
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
              background: here ? tone : '#232323',
              boxShadow: here ? `0 0 12px ${tone}` : 'none',
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

// A stat, as something with weight. The 2×2 label/value grid was the most
// spreadsheet-like thing on the page; this gives each number a tinted well and
// a size worth reading.
export function StatPill({ label, value, tone = T.white, sub }) {
  return (
    <div style={{
      padding: '9px 11px', borderRadius: 11, background: '#141414',
      border: `1px solid ${T.border}`, minWidth: 0,
    }}>
      <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5,
                    letterSpacing: 1, color: T.muted2, textTransform: 'uppercase',
                    whiteSpace: 'nowrap', overflow: 'hidden',
                    textOverflow: 'ellipsis' }}>{label}</div>
      <div style={{ color: tone, fontSize: 19, fontWeight: 800, lineHeight: 1.15,
                    fontVariantNumeric: 'tabular-nums' }}>{value}</div>
      {sub ? (
        <div style={{ color: T.muted2, fontSize: 10 }}>{sub}</div>
      ) : null}
    </div>
  )
}
