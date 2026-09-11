import { useEffect, useMemo, useState } from 'react'
import { T } from './theme'
import { Chip, Spinner, Empty, SectionLabel } from './bits'
import { PropRow } from './BoardTab'
import { useBookmarks } from './useBookmarks'
import { fetchNflBoard, fetchNflRecord } from '../utils/api'

// ── NFL BOARD ────────────────────────────────────────────────────────────────
// Renders the SAME card as the tennis board — PropRow, imported, not
// reimplemented. A lookalike card drifts the moment either sport is touched,
// and the two boards are the same idea: a player, a prop, a line, our number,
// and how confident we are.
//
// THE FULL SCANNED MARKET, like tennis — every line the model could price, not
// the eight that went to Discord. 171 props across 13 games on a Sunday.
//
// It gets there by a different route than tennis, and the difference is worth
// knowing. Tennis prices each row in the browser via /api/prop/calculate. The
// NFL model cannot run in the web backend — nfl/ deploys with the BOT, and
// pricing there would mean pyarrow plus a few-hundred-megabyte parquet on the
// service that has to cold-start for the app. So the bot scans on a schedule,
// publishes the whole board, and this reads it. Same content, pushed rather
// than pulled.
//
// The prior-season caveat is stated ONCE in the footer rather than on every
// card. It applies to nearly every row this early in the season, and a warning
// that appears 171 times is wallpaper — it stops being read exactly when it
// matters. The flag still rides on the row for anything that wants it.

const PROP_LABEL = {
  pass_yards: 'Pass Yards',
  rush_yards: 'Rush Yards',
  receiving_yards: 'Rec Yards',
  receptions: 'Receptions',
}

// ET, to match the slates the bot stamps. Local time would put a west-coast
// user on the wrong slate for most of the evening.
const etToday = () =>
  new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' })

function prettyDate(iso) {
  if (!iso) return ''
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(undefined,
    { weekday: 'short', month: 'numeric', day: 'numeric', timeZone: 'UTC' })
}

// NFL pick -> the shape PropRow already speaks.
function toRow(p) {
  return {
    key: `nfl-${p.id}`,
    player: p.player,
    opponent: p.opponent || '—',
    surface: '',
    tour: 'NFL',
    propType: PROP_LABEL[p.prop_type] || p.prop_type,
    line: p.line,
    projection: p.model_projection,
    edge: p.edge,
    // PropRow's confidence is a 0-100 scale (it drives tier() and ConfBar);
    // the stored value is a probability. Passing 0.81 straight through would
    // render every NFL play as the lowest possible tier.
    confidence: typeof p.confidence === 'number' ? p.confidence * 100 : null,
    startTs: null,
    _state: 'done',
    _pick: p,
  }
}

// The bottom-left slot: a result once there is one, the caveat until then.
function footNoteFor(p) {
  const res = p.result
  const star = p.is_potd ? '⭐ ' : ''
  if (res === 'W' || res === 'L') {
    const tone = res === 'W' ? '#3FB950' : '#E5534B'
    return (
      <span style={{ color: tone, fontWeight: 800, fontSize: 11 }}>
        {star}{res === 'W' ? '✅' : '❌'} {res}
        {typeof p.result_value === 'number' ? ` · ${p.result_value}` : ''}
      </span>
    )
  }
  if (res === 'VOID') {
    return <span style={{ color: T.muted2, fontSize: 11 }}>VOID · did not play</span>
  }
  // The prior-season caveat was REMOVED from the card (user, 2026-09-13). It
  // sat on almost every early-season row, which made it wallpaper rather than a
  // warning, and it crowded a card built for a start time. The flag is still
  // carried on the row and still stated in the footer note below, so the
  // information survives without shouting from 171 cards at once.
  return star ? <span style={{ fontSize: 11 }}>{star}posted</span> : ''
}

export default function NflBoard() {
  const [picks, setPicks] = useState(null)
  const [err, setErr] = useState(null)
  const [slate, setSlate] = useState(null)
  const { has, toggle } = useBookmarks()

  const [posted, setPosted] = useState([])

  useEffect(() => {
    let alive = true
    // TWO SOURCES, ON PURPOSE. The board is the whole scanned market; the
    // record is the handful that were posted to Discord and graded. Showing
    // only the record made this a copy of the Discord post rather than a board.
    Promise.all([
      fetchNflBoard().then(d => d?.rows || []).catch(() => []),
      fetchNflRecord().then(d => d?.picks || []).catch(() => []),
    ]).then(([rows, rec]) => {
      if (!alive) return
      setPicks(rows)
      setPosted(rec)
      if (!rows.length && !rec.length) setErr(null)
    }).catch(e => {
      if (alive) setErr(e?.message || 'Could not load the NFL board')
    })
    return () => { alive = false }
  }, [])

  const slates = useMemo(
    () => [...new Set((picks || []).map(p => p.slate_date).filter(Boolean))]
      .sort().reverse(), [picks])

  // Default to TODAY's slate when there is one. Landing on whichever slate
  // sorts first is how a board shows last week's games to someone checking
  // tonight.
  const active = slate || (slates.includes(etToday()) ? etToday() : slates[0])

  // Merge the record INTO the board: a row that was posted to Discord carries
  // its ⭐ and, once the game is played, its result. Keyed on player+prop+slate
  // because that is what makes a play the same play across the two tables.
  const byPosted = useMemo(() => {
    const m = new Map()
    for (const p of posted) {
      m.set(`${p.slate_date}|${p.player}|${p.prop_type}`, p)
    }
    return m
  }, [posted])

  const rows = useMemo(
    () => (picks || [])
      .filter(p => p.slate_date === active)
      .map(p => {
        const hit = byPosted.get(`${p.slate_date}|${p.player}|${p.prop_type}`)
        return hit ? { ...p, result: hit.result, result_value: hit.result_value,
                       is_potd: hit.is_potd } : p
      })
      .sort((a, b) => ((b.is_potd || 0) - (a.is_potd || 0))
        || ((b.confidence || 0) - (a.confidence || 0))),
    [picks, active, byPosted])

  const tally = useMemo(() => {
    const w = rows.filter(p => p.result === 'W').length
    const l = rows.filter(p => p.result === 'L').length
    const live = rows.filter(p => !p.result || p.result === 'PENDING').length
    return { w, l, live }
  }, [rows])

  if (err) return <Empty icon="⚠️" title="NFL board unavailable" hint={err} />
  if (!picks) {
    return <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}>
      <Spinner size={28} />
    </div>
  }
  if (!picks.length) {
    return <Empty icon="🏈" title="No NFL board right now"
                  hint="The board refreshes through the day. Sunday slates load Friday." />
  }

  return (
    <div>
      {slates.length > 1 && (
        <div style={{ display: 'flex', gap: 6, overflowX: 'auto',
                      paddingBottom: 10, marginBottom: 2 }}>
          {slates.map(s => (
            <Chip key={s} active={s === active} onClick={() => setSlate(s)}>
              {prettyDate(s)}
            </Chip>
          ))}
        </div>
      )}

      <SectionLabel right={
        tally.w + tally.l > 0
          ? `${tally.w}-${tally.l} · ${rows.length} props`
          : `${rows.length} props`
      }>
        {prettyDate(active)} board
      </SectionLabel>

      {/* The SAME wrapper the tennis board uses — CSS multi-column, 2 up on a
          wide screen and 3 on a very wide one, gated on the .baseline-wide
          ancestor. Reusing the class rather than a new grid keeps the two
          boards laying out identically at every breakpoint. */}
      <div className="baseline-cols">
        {rows.map((p, i) => {
          const r = toRow(p)
          return (
            <PropRow key={r.key} r={r} index={i}
                     saved={has(r.key)} onSave={() => toggle(r.key)}
                     footNote={footNoteFor(p)} />
          )
        })}
      </div>

      <div style={{ color: T.muted2, fontSize: 11.5, textAlign: 'center',
                    padding: '16px 12px 4px', lineHeight: 1.5 }}>
        Every PrizePicks NFL line the model can price, with Baseline's
        projection. Edge = projection − line. ⭐ marks a posted play.
        Early-season numbers run on last season's usage until enough games are
        played. Model projections, not betting advice.
      </div>
    </div>
  )
}
