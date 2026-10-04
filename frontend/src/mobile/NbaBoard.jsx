import { useEffect, useMemo, useState } from 'react'
import { T } from './theme'
import { Spinner, Empty, SectionLabel, Pager, PAGE_SIZE } from './bits'
import { BoardSummary, TopPlays, PlayerGroup, groupByPlayer } from './BoardTab'
import { useBookmarks } from './useBookmarks'
import { fetchNbaBoard, fetchNbaRecord } from '../utils/api'

// ── NBA BOARD ────────────────────────────────────────────────────────────────
// Renders the SAME cards as the tennis and NFL boards — PlayerGroup,
// BoardSummary and TopPlays, imported, not reimplemented. A lookalike drifts
// the moment any sport is touched, and all three boards are the same idea: a
// player, the props we priced on him, our number against the book's, and how
// confident we are.
//
// PUSHED, NOT PULLED, exactly like NFL. The NBA model deploys with the BOT —
// pricing in the web backend would mean holding a season of game logs on the
// service that has to cold-start for the app, and stats.nba.com refuses the
// datacenter range so every call needs the proxy. So the bot scans on a
// schedule, publishes the whole board to nba_board, and this reads it.
//
// ONE DIFFERENCE FROM NFL, AND IT MATTERS FOR THE CONFIDENCE NUMBER. NFL stores
// a probability in `confidence` and NflBoard multiplies by 100 to get the 0-100
// scale the cards want. NBA stores an EVR-graded SCORE that is ALREADY 0-100
// (see nba/confidence.py), so multiplying here would send every play to 7500
// and paint the whole board as elite. Passed straight through, deliberately.

const PROP_LABEL = {
  pts: 'Points', reb: 'Rebounds', ast: 'Assists', fg3m: '3PM',
  pra: 'Pts+Reb+Ast', pr: 'Pts+Reb', pa: 'Pts+Ast', ra: 'Reb+Ast',
  nba_fantasy_pts: 'Fantasy',
}

// ET, to match the slates the bot stamps. Local time would put a west-coast
// reader on the wrong slate for most of the evening.
const etToday = () =>
  new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' })

function prettyDate(iso) {
  if (!iso) return ''
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(undefined,
    { weekday: 'short', month: 'numeric', day: 'numeric', timeZone: 'UTC' })
}

// NBA row -> the row shape the shared board components speak.
function toRow(p) {
  return {
    key: `nba-${p.id}`,
    player: p.player,
    opponent: p.opponent || '—',
    surface: '',
    team: p.team,
    tour: 'NBA',
    propType: PROP_LABEL[p.prop_type] || p.prop_type,
    line: p.line,
    projection: p.model_projection,
    edge: p.edge,
    // ALREADY 0-100 — see the note at the top of this file.
    confidence: typeof p.confidence === 'number' ? p.confidence : null,
    startTs: null,
    _state: 'done',
    _pick: p,
  }
}

// The bottom-left slot: a result once there is one, the caveat until then.
function footNoteFor(p) {
  if (!p) return ''
  const res = p.result
  const star = p.is_star ? '⭐ ' : ''
  if (res === 'W' || res === 'L') {
    const tone = res === 'W' ? '#3FB950' : '#E5534B'
    return (
      <span style={{ color: tone, fontWeight: 800, fontSize: 11 }}>
        {star}{res === 'W' ? '✅' : '❌'} {res}
        {typeof p.result_value === 'number' ? ` · ${p.result_value}` : ''}
      </span>
    )
  }
  if (res === 'PUSH') {
    return <span style={{ color: T.muted2, fontSize: 11 }}>⚪ PUSH</span>
  }
  if (res === 'VOID') {
    return <span style={{ color: T.muted2, fontSize: 11 }}>VOID · did not play</span>
  }
  // Minutes and rotation are the NBA's version of the NFL prior-season caveat,
  // and the same lesson applies: a warning on every card is wallpaper. Only the
  // genuinely volatile rotations say anything here.
  const bits = []
  if (star) bits.push(star.trim())
  if (p.rotation === 'volatile') bits.push('rotation risk')
  if (typeof p.minutes === 'number') bits.push(`${p.minutes.toFixed(0)} min`)
  return bits.length
    ? <span style={{ fontSize: 11, color: T.muted2 }}>{bits.join(' · ')}</span>
    : ''
}

export default function NbaBoard({ book = 'prizepicks', onMeta, onProject }) {
  const [picks, setPicks] = useState(null)
  const [err, setErr] = useState(null)
  const [posted, setPosted] = useState([])
  const [openKey, setOpenKey] = useState(null)
  const [page, setPage] = useState(0)
  const { has, toggle } = useBookmarks()

  // Switching books changes how long the board is; a reader on page 4 of
  // PrizePicks would otherwise land past the end of a shorter Underdog board.
  useEffect(() => { setPage(0) }, [book])

  useEffect(() => {
    let alive = true
    // TWO SOURCES, ON PURPOSE. The board is the whole scanned market; the
    // record is the handful that were posted to Discord and graded. Showing
    // only the record would make this a copy of the Discord post, not a board.
    Promise.all([
      fetchNbaBoard().then(d => d?.rows || []).catch(() => []),
      fetchNbaRecord().then(d => d?.picks || []).catch(() => []),
    ]).then(([rows, rec]) => {
      if (!alive) return
      setPicks(rows)
      setPosted(rec)
    }).catch(e => {
      if (alive) setErr(e?.message || 'Could not load the NBA board')
    })
    return () => { alive = false }
  }, [])

  const slates = useMemo(
    () => [...new Set((picks || []).map(p => p.slate_date).filter(Boolean))]
      .sort().reverse(), [picks])

  // THE LIVE SLATE, chosen for the reader rather than offered as tabs — today
  // if there is one, otherwise the next, and only fall back to the most recent
  // when nothing upcoming is published.
  const active = useMemo(() => {
    const today = etToday()
    if (slates.includes(today)) return today
    const upcoming = slates.filter(s => s > today).sort()
    return upcoming[0] || slates[0]
  }, [slates])

  // Merge the record INTO the board: a row that was posted carries its ⭐ and,
  // once the game is played, its result. Keyed on player+prop+slate because
  // that is what makes a play the same play across the two tables.
  const byPosted = useMemo(() => {
    const m = new Map()
    for (const p of posted) m.set(`${p.slate_date}|${p.player}|${p.prop_type}`, p)
    return m
  }, [posted])

  const rows = useMemo(
    () => (picks || [])
      .filter(p => p.slate_date === active && (!p.book || p.book === book))
      .map(p => {
        const hit = byPosted.get(`${p.slate_date}|${p.player}|${p.prop_type}`)
        return hit ? { ...p, result: hit.result, result_value: hit.result_value,
                       is_star: hit.is_star } : p
      })
      .sort((a, b) => ((b.is_star || 0) - (a.is_star || 0))
        || ((b.confidence || 0) - (a.confidence || 0))),
    [picks, active, byPosted, book])

  const viewRows = useMemo(() => rows.map(toRow), [rows])
  const groups = useMemo(() => groupByPlayer(viewRows), [viewRows])
  const pages = Math.max(1, Math.ceil(groups.length / PAGE_SIZE))
  const pageGroups = useMemo(
    () => groups.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE),
    [groups, page])

  const tally = useMemo(() => {
    const w = rows.filter(p => p.result === 'W').length
    const l = rows.filter(p => p.result === 'L').length
    return { w, l }
  }, [rows])

  useEffect(() => {
    if (!onMeta) return
    onMeta({ count: viewRows.length,
             label: active ? `${prettyDate(active)} slate` : '' })
  }, [onMeta, viewRows.length, active])

  if (err) return <Empty icon="⚠️" title="NBA board unavailable" hint={err} />
  if (!picks) {
    return <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}>
      <Spinner size={28} />
    </div>
  }
  if (!picks.length) {
    return <Empty icon="🏀" title="No NBA board right now"
                  hint="The board is published in the afternoon and refreshes
                        through the evening. The season opens 21 October." />
  }

  return (
    <div>
      <BoardSummary rows={viewRows} />

      <TopPlays rows={viewRows}
                onOpen={r => onProject?.({ sport: 'nba', player: r.player,
                                           prop: r._pick?.prop_type,
                                           line: r._pick?.line })}
                saved={r => has(r.key)}
                onSave={(r, e) => { e.stopPropagation(); toggle(r.key) }} />

      <SectionLabel right={
        tally.w + tally.l > 0
          ? `${tally.w}-${tally.l} · ${groups.length} players`
          : `${groups.length} players · ${rows.length} lines`
      }>Full board</SectionLabel>

      {/* The SAME wrapper tennis and NFL use — CSS multi-column, 2 up wide and
          3 very wide. Reusing the class keeps all three boards laying out
          identically at every breakpoint. */}
      <div className="baseline-cols">
        {pageGroups.map((g, i) => (
          <PlayerGroup key={g.key} g={g} index={i}
                       open={openKey === g.key}
                       onToggle={() => setOpenKey(k => (k === g.key ? null : g.key))}
                       saved={has(g.rows[0].key)}
                       onSave={() => toggle(g.rows[0].key)}
                       onOpen={(r) => { const row = r || g.rows[0]
                                        onProject?.({ sport: 'nba',
                                                      player: row.player,
                                                      prop: row._pick?.prop_type,
                                                      line: row._pick?.line }) }}
                       footNoteFor={r => footNoteFor(r._pick)} />
        ))}
      </div>

      <Pager page={page} pages={pages} onPage={setPage}
             total={groups.length} label="players" />

      <div style={{ color: T.muted2, fontSize: 11.5, textAlign: 'center',
                    padding: '16px 12px 4px', lineHeight: 1.5 }}>
        Every {book === 'underdog' ? 'Underdog' : 'PrizePicks'} NBA line the
        model can price, with Baseline's projection. Edge = projection − line.
        ⭐ marks a posted play. Combo props (PRA and the pairs) are compounded
        from several projections and are held to a stricter bar.
        Model projections, not betting advice.
      </div>
    </div>
  )
}
