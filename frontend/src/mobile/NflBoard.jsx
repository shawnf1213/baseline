import { useEffect, useMemo, useState } from 'react'
import { T } from './theme'
import { Chip, Spinner, Empty, SectionLabel } from './bits'
import { PropRow } from './BoardTab'
import { useBookmarks } from './useBookmarks'
import { fetchNflRecord } from '../utils/api'

// ── NFL BOARD ────────────────────────────────────────────────────────────────
// Renders the SAME card as the tennis board — PropRow, imported, not
// reimplemented. A lookalike card drifts the moment either sport is touched,
// and the two boards are the same idea: a player, a prop, a line, our number,
// and how confident we are.
//
// DELIBERATELY NOT A LIVE PROJECTION TOOL. Tennis prices the book's board
// through /api/prop/calculate and will quote anything you ask. There is no NFL
// equivalent, because the nfl/ package deploys with the BOT and the backend
// cannot import it. What the backend has is the record — every play the bot
// posted, with line, projection, lean and result — so that is what this shows.
//
// EVERY ROW SAYS WHAT THE MODEL KNEW. prior_season_only rides all the way from
// the projection to the card: an early-season NFL number built on last season's
// usage measured +25% error against the market, and a card that cannot say so
// reads more confident than it is.

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
  if (res === 'W' || res === 'L') {
    const tone = res === 'W' ? '#3FB950' : '#E5534B'
    return (
      <span style={{ color: tone, fontWeight: 800, fontSize: 11 }}>
        {res === 'W' ? '✅' : '❌'} {res}
        {typeof p.result_value === 'number' ? ` · ${p.result_value}` : ''}
      </span>
    )
  }
  if (res === 'VOID') {
    return <span style={{ color: T.muted2, fontSize: 11 }}>VOID · did not play</span>
  }
  if (p.prior_season_only) {
    return <span style={{ color: '#D29922', fontSize: 11 }}>⚠️ prior-season usage</span>
  }
  return ''
}

export default function NflBoard() {
  const [picks, setPicks] = useState(null)
  const [err, setErr] = useState(null)
  const [slate, setSlate] = useState(null)
  const { has, toggle } = useBookmarks()

  useEffect(() => {
    let alive = true
    fetchNflRecord()
      .then(d => { if (alive) setPicks(d?.picks || []) })
      .catch(e => { if (alive) setErr(e?.message || 'Could not load the NFL board') })
    return () => { alive = false }
  }, [])

  const slates = useMemo(
    () => [...new Set((picks || []).map(p => p.slate_date).filter(Boolean))]
      .sort().reverse(), [picks])

  // Default to TODAY's slate when there is one. Landing on whichever slate
  // sorts first is how a board shows last week's games to someone checking
  // tonight.
  const active = slate || (slates.includes(etToday()) ? etToday() : slates[0])

  const rows = useMemo(
    () => (picks || [])
      .filter(p => p.slate_date === active)
      .sort((a, b) => (b.is_potd - a.is_potd)
        || ((b.confidence || 0) - (a.confidence || 0))),
    [picks, active])

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
    return <Empty icon="🏈" title="No NFL plays yet"
                  hint="Boards post Fridays for Sunday, and Sunday night for Monday." />
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
          ? `${tally.w}-${tally.l}${tally.live ? ` · ${tally.live} live` : ''}`
          : `${tally.live} live`
      }>
        {prettyDate(active)} board
      </SectionLabel>

      {rows.map((p, i) => {
        const r = toRow(p)
        return (
          <PropRow key={r.key} r={r} index={i}
                   saved={has(r.key)} onSave={() => toggle(r.key)}
                   footNote={footNoteFor(p)} />
        )
      })}

      <div style={{ color: T.muted2, fontSize: 11.5, textAlign: 'center',
                    padding: '16px 12px 4px', lineHeight: 1.5 }}>
        Plays the board posted, with results. Edge = projection − line.
        Model projections, not betting advice.
      </div>
    </div>
  )
}
