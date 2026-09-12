import { T } from './theme'

// ── SHARED VISUAL LANGUAGE FOR PROP CARDS ────────────────────────────────────
// These were written for the NFL sheet and then wanted by tennis, which is the
// moment to move them rather than copy them. Everything here is SPORT-AGNOSTIC:
// it takes a list of past values, a line, and which side we are on. It knows
// nothing about downs, sets or surfaces.
//
// The division of labour: bits.jsx holds the language every SCREEN shares
// (tiers, side colours, confidence bars), this holds the language every PROP
// CARD shares, and nflviz.jsx holds what is genuinely NFL — club colours,
// crests, a defensive rank out of 32.
//
// A "game" here is { v, opp, wk } — value, opponent label, and a period number.
// Tennis passes a match and a tournament; nothing in this file cares which.

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

// A stat, as something with weight. The 2×2 label/value grid was the most
// spreadsheet-like thing on the page; this gives each number a tinted well, a
// size worth reading, and — where the stat is a SHARE of something — a bar, so
// "45.8%" is a length before it is a number.
//
// `pct` is the fraction to fill, and `of` names what it is a fraction OF, which
// is the part a bare percentage always leaves out. No bar is drawn for a count
// or a rate: a bar under "4.02 yards per carry" would be measuring against a
// maximum that does not exist.
export function StatPill({ label, value, pct, of, accent = T.green, sub }) {
  const hasBar = typeof pct === 'number' && pct >= 0
  return (
    <div style={{
      padding: '10px 12px 11px', borderRadius: 12, minWidth: 0,
      background: 'linear-gradient(158deg, #181818 0%, #121212 70%)',
      border: `1px solid ${T.border}`,
      boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.035)',
    }}>
      <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5,
                    letterSpacing: 1, color: T.muted2, textTransform: 'uppercase',
                    whiteSpace: 'nowrap', overflow: 'hidden',
                    textOverflow: 'ellipsis' }}>{label}</div>
      <div style={{ color: T.white, fontSize: 21, fontWeight: 800,
                    lineHeight: 1.2, letterSpacing: -0.3,
                    fontVariantNumeric: 'tabular-nums' }}>{value}</div>
      {hasBar ? (
        <>
          <div style={{ height: 4, borderRadius: 3, background: '#000',
                        overflow: 'hidden', marginTop: 6 }}>
            <div style={{ width: `${Math.min(100, pct * 100)}%`, height: '100%',
                          borderRadius: 3, background: accent,
                          boxShadow: `0 0 8px ${accent}99` }} />
          </div>
          {of ? (
            <div style={{ color: T.muted2, fontSize: 9.5, marginTop: 3 }}>{of}</div>
          ) : null}
        </>
      ) : sub ? (
        <div style={{ color: T.muted2, fontSize: 10, marginTop: 3 }}>{sub}</div>
      ) : null}
    </div>
  )
}

// A share, as a bar. "McMillan 25% Legette 14% Coker 10%" is four facts in a
// sentence; this is four facts you can compare without reading any of them.
export function ShareBars({ rows, accent = T.green }) {
  const top = Math.max(...rows.map(r => r.share || 0), 0.0001)
  return (
    <div style={{ marginTop: 5 }}>
      {rows.map((r, i) => (
        <div key={r.key || i} style={{ marginTop: i ? 8 : 2 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8,
                        fontSize: 12.5, marginBottom: 3 }}>
            <span style={{ color: r.me ? T.white : T.muted,
                           fontWeight: r.me ? 800 : 500, flex: 1,
                           whiteSpace: 'nowrap', overflow: 'hidden',
                           textOverflow: 'ellipsis' }}>{r.label}</span>
            {r.right ? (
              <span style={{ color: T.muted2, fontSize: 11 }}>{r.right}</span>
            ) : null}
            <b style={{ color: r.me ? accent : T.white, fontSize: 13,
                        fontVariantNumeric: 'tabular-nums' }}>
              {Math.round((r.share || 0) * 100)}%
            </b>
          </div>
          <div style={{ height: 6, borderRadius: 4, background: '#191919',
                        overflow: 'hidden' }}>
            <div style={{
              width: `${((r.share || 0) / top) * 100}%`, height: '100%',
              borderRadius: 4,
              background: r.me ? accent : '#2f2f2f',
              boxShadow: r.me ? `0 0 10px ${accent}66` : 'none',
            }} />
          </div>
        </div>
      ))}
    </div>
  )
}
