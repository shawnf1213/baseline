import { useEffect, useMemo, useState } from 'react'
import { T } from './theme'
import { Card, Chip, Spinner, Empty, SectionLabel, ResultBadge } from './bits'
import { fetchNflRecord } from '../utils/api'

// ── NFL BOARD ────────────────────────────────────────────────────────────────
// The NFL plays the bot posted, and how they turned out.
//
// DELIBERATELY NOT A LIVE PROJECTION TOOL. The tennis Projections tab calls
// /api/prop/calculate and prices anything you ask for; there is no NFL
// equivalent yet, because the nfl/ package is deployed with the BOT and not
// with the backend, so the backend cannot run the model. What the backend does
// have is the record — every play the bot posted, with its line, its
// projection, its lean and its result — and that is what this renders.
//
// So this shows what was ACTUALLY POSTED rather than what could be priced. That
// is the honest half, and it is the half a subscriber cares about: the board
// they were pinged for, and whether it cashed.
//
// EVERY ROW SAYS WHAT THE MODEL KNEW. prior_season_only is carried all the way
// from the projection to this card, because an early-season NFL number built on
// last season's usage measured +25% error against the market, and a row that
// cannot say so is a row that reads more confident than it is.

const PROP_LABEL = {
  pass_yards: 'Pass Yards',
  rush_yards: 'Rush Yards',
  receiving_yards: 'Rec Yards',
  receptions: 'Receptions',
}

const fmt = (v, nd = 1) => (typeof v === 'number' ? v.toFixed(nd) : '—')

// ET date, to match the slates the bot stamps. Doing this in local time would
// put a west-coast user on the wrong slate for most of the evening.
function etToday() {
  const s = new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' })
  return s   // en-CA gives YYYY-MM-DD
}

function prettyDate(iso) {
  if (!iso) return ''
  const [y, m, d] = iso.split('-').map(Number)
  const dt = new Date(Date.UTC(y, m - 1, d))
  return dt.toLocaleDateString(undefined,
    { weekday: 'short', month: 'numeric', day: 'numeric', timeZone: 'UTC' })
}

function resultTone(r) {
  if (r === 'W') return 'win'
  if (r === 'L') return 'loss'
  if (r === 'VOID') return 'void'
  return 'pending'
}

function PlayRow({ p }) {
  const lean = (p.lean || '').toUpperCase()
  const over = lean === 'OVER'
  const res = p.result || 'PENDING'
  const decided = res === 'W' || res === 'L'
  return (
    <Card style={{ padding: 13, marginBottom: 8,
                   // A settled play is dimmed slightly so the live ones lead.
                   opacity: decided ? 0.92 : 1 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        {p.is_potd ? <span style={{ fontSize: 13 }}>⭐</span> : null}
        <div style={{ fontSize: 13.5, fontWeight: 700, color: T.white,
                      flex: 1, minWidth: 0, overflow: 'hidden',
                      textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {p.player}
        </div>
        {decided && (
          <span style={{ fontSize: 11, fontWeight: 800,
                         color: res === 'W' ? '#3FB950' : '#E5534B' }}>
            {res}
          </span>
        )}
        {res === 'VOID' && (
          <span style={{ fontSize: 10.5, color: T.muted2 }}>VOID</span>
        )}
      </div>

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6,
                    marginTop: 5, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12 }}>{over ? '🟢' : '🔴'}</span>
        <span style={{ fontSize: 12.5, fontWeight: 800, color: T.white,
                       textTransform: 'uppercase' }}>
          {lean} {fmt(p.line)} {PROP_LABEL[p.prop_type] || p.prop_type}
        </span>
        <span style={{ fontSize: 11.5, color: T.muted }}>
          · Proj {fmt(p.model_projection)}
        </span>
        {typeof p.confidence === 'number' && (
          <span style={{ fontSize: 11.5, color: T.muted }}>
            · {(p.confidence * 100).toFixed(0)}%
          </span>
        )}
        {typeof p.result_value === 'number' && (
          <span style={{ fontSize: 11.5, color: T.white, fontWeight: 700 }}>
            · actual {fmt(p.result_value)}
          </span>
        )}
      </div>

      <div style={{ display: 'flex', gap: 8, marginTop: 5, alignItems: 'center',
                    flexWrap: 'wrap' }}>
        {(p.team || p.opponent) && (
          <span style={{ fontSize: 11, color: T.muted2 }}>
            {p.team}{p.opponent ? ` vs ${p.opponent}` : ''}
          </span>
        )}
        {p.prior_season_only ? (
          <span style={{ fontSize: 10.5, color: '#D29922' }}>
            ⚠️ prior-season usage
          </span>
        ) : null}
      </div>
    </Card>
  )
}

export default function NflBoard() {
  const [picks, setPicks] = useState(null)
  const [err, setErr] = useState(null)
  const [slate, setSlate] = useState(null)

  useEffect(() => {
    let alive = true
    fetchNflRecord()
      .then(d => { if (alive) setPicks(d?.picks || []) })
      .catch(e => { if (alive) setErr(e?.message || 'Could not load the NFL board') })
    return () => { alive = false }
  }, [])

  const slates = useMemo(() => {
    const s = [...new Set((picks || []).map(p => p.slate_date).filter(Boolean))]
    return s.sort().reverse()
  }, [picks])

  // Default to TODAY's slate when there is one, otherwise the most recent.
  // Landing on an old slate because it happens to sort first is how a board
  // shows last week's games to someone checking tonight's.
  const active = slate || (slates.includes(etToday()) ? etToday() : slates[0])

  const rows = useMemo(
    () => (picks || [])
      .filter(p => p.slate_date === active)
      .sort((a, b) => (b.is_potd - a.is_potd)
        || ((b.confidence || 0) - (a.confidence || 0))),
    [picks, active])

  const rec = useMemo(() => {
    const w = rows.filter(p => p.result === 'W').length
    const l = rows.filter(p => p.result === 'L').length
    const pend = rows.filter(p => !p.result || p.result === 'PENDING').length
    return { w, l, pend }
  }, [rows])

  if (err) return <Empty title="NFL board unavailable" hint={err} />
  if (!picks) return <div style={{ padding: 40, textAlign: 'center' }}><Spinner /></div>
  if (!picks.length) {
    return <Empty title="No NFL plays yet"
                  hint="Boards post Fridays for Sunday, and Sunday night for Monday." />
  }

  return (
    <div>
      {slates.length > 1 && (
        <div style={{ display: 'flex', gap: 6, overflowX: 'auto',
                      paddingBottom: 10, marginBottom: 4 }}>
          {slates.map(s => (
            <Chip key={s} active={s === active} onClick={() => setSlate(s)}>
              {prettyDate(s)}
            </Chip>
          ))}
        </div>
      )}

      <SectionLabel right={
        rec.w + rec.l > 0
          ? `${rec.w}-${rec.l}${rec.pend ? ` · ${rec.pend} live` : ''}`
          : `${rec.pend} live`
      }>
        {prettyDate(active)} board
      </SectionLabel>

      {rows.map(p => <PlayRow key={p.id} p={p} />)}

      <div style={{ fontSize: 10.5, color: T.muted2, textAlign: 'center',
                    padding: '14px 8px 4px', lineHeight: 1.5 }}>
        Model projections, not betting advice.
      </div>
    </div>
  )
}
