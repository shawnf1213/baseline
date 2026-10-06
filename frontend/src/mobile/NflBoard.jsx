import { useEffect, useMemo, useState } from 'react'
import { T } from './theme'
import { Spinner, Empty, SectionLabel, Pager, PAGE_SIZE } from './bits'
import { BoardSummary, TopPlays, PlayerGroup,
         groupByPlayer } from './BoardTab'
import { useBookmarks } from './useBookmarks'
import { fetchNflBoard, fetchNflRecord } from '../utils/api'
import NflPlayerSheet from './NflPlayerSheet'

// ── NFL BOARD ────────────────────────────────────────────────────────────────
// Renders the SAME cards as the tennis board — PlayerGroup, BoardSummary and
// TopPlays, imported, not reimplemented. A lookalike drifts the moment either
// sport is touched, and the two boards are the same idea: a player, the props
// we priced on him, our number against the book's, and how confident we are.
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

// "Thu" / "Sun" / "Mon" — the ET weekday of a kickoff.
function etWeekday(iso) {
  const t = Date.parse(iso || '')
  if (!Number.isFinite(t)) return ''
  return new Date(t).toLocaleDateString('en-US',
    { weekday: 'short', timeZone: 'America/New_York' })
}

// NFL pick -> the row shape the shared board components speak. `multiDay` is
// set when the board spans more than one game day, so each card says which
// day its game is on.
function toRow(p, multiDay = false) {
  const ko = Date.parse(p.kickoff || '')
  const day = multiDay ? etWeekday(p.kickoff) : ''
  return {
    key: `nfl-${p.id}`,
    player: p.player,
    opponent: (p.opponent || '—') + (day ? ` · ${day}` : ''),
    surface: '',
    // Carried through so a card can draw the club crest.
    team: p.team,
    tour: 'NFL',
    propType: PROP_LABEL[p.prop_type] || p.prop_type,
    line: p.line,
    projection: p.model_projection,
    edge: p.edge,
    // PropRow's confidence is a 0-100 scale (it drives tier() and ConfBar);
    // the stored value is a probability. Passing 0.81 straight through would
    // render every NFL play as the lowest possible tier.
    confidence: typeof p.confidence === 'number' ? p.confidence * 100 : null,
    // Kickoff, so the open card shows the game time like a tennis card does.
    startTs: Number.isFinite(ko) ? Math.floor(ko / 1000) : null,
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

export default function NflBoard({ book = 'prizepicks', onMeta, onProject }) {
  const [picks, setPicks] = useState(null)
  const [err, setErr] = useState(null)
  const { has, toggle } = useBookmarks()

  const [posted, setPosted] = useState([])
  // Tapping a card opens the player's other props and his posted record —
  // the tennis board does the same thing with PlayerDashboard.
  const [open, setOpen] = useState(null)
  const [focusProp, setFocusProp] = useState(null)
  const [openKey, setOpenKey] = useState(null)
  const [page, setPage] = useState(0)

  // Switching books changes how long the board is; a reader on page 4 of
  // PrizePicks would otherwise land past the end of a shorter Underdog board.
  useEffect(() => { setPage(0) }, [book])

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

  // EVERY GAME DAY STILL TO COME, in one board — still no date tabs. Tennis
  // has no date picker; it shows every line that is live now, and so does
  // this. The bot prices every line the books list for the coming week and
  // stores each game day as its own slate (operator, 2026-10-06: NFL lines
  // available and projected every day, not just on game days), so a Tuesday
  // carries Thursday's, Sunday's and Monday's lines. Only when nothing is
  // upcoming does it fall back to the most recent slate.
  const shown = useMemo(() => {
    const today = etToday()
    const upcoming = slates.filter(s => s >= today).sort()
    return upcoming.length ? upcoming : slates.slice(0, 1)
  }, [slates])

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

  // ONE BOOK AT A TIME, like the tennis board. /api/nfl/board is fetched
  // without a `book` so this component holds the whole market and the switch
  // is instant — but rendering it unfiltered put both books' rows in one list
  // and every player appeared once per book. A row with no book at all is kept
  // rather than dropped, so an older published row cannot silently vanish.
  const rows = useMemo(
    () => (picks || [])
      .filter(p => shown.includes(p.slate_date) && (!p.book || p.book === book))
      .map(p => {
        const hit = byPosted.get(`${p.slate_date}|${p.player}|${p.prop_type}`)
        return hit ? { ...p, result: hit.result, result_value: hit.result_value,
                       is_potd: hit.is_potd } : p
      })
      .sort((a, b) => ((b.is_potd || 0) - (a.is_potd || 0))
        || ((b.confidence || 0) - (a.confidence || 0))),
    [picks, shown, byPosted, book])

  // The SAME row shape the tennis board uses, built once. The summary, the top
  // plays and the grid all read it, so none of them can describe a different
  // board than the others.
  const viewRows = useMemo(() => {
    const multiDay = new Set(rows.map(p => p.slate_date)).size > 1
    return rows.map(p => toRow(p, multiDay))
  }, [rows])

  // The SAME grouping the tennis board uses — 168 props across roughly sixty
  // players, so the board is sixty cards instead of a hundred and sixty-eight
  // with the same faces repeated four times each.
  const groups = useMemo(() => groupByPlayer(viewRows), [viewRows])
  const pages = Math.max(1, Math.ceil(groups.length / PAGE_SIZE))
  const pageGroups = useMemo(
    () => groups.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE),
    [groups, page])

  const tally = useMemo(() => {
    const w = rows.filter(p => p.result === 'W').length
    const l = rows.filter(p => p.result === 'L').length
    const live = rows.filter(p => !p.result || p.result === 'PENDING').length
    return { w, l, live }
  }, [rows])

  useEffect(() => {
    if (!onMeta) return
    const first = shown[0], last = shown[shown.length - 1]
    onMeta({ count: viewRows.length,
             label: !first ? ''
               : first === last ? `${prettyDate(first)} slate`
               : `${prettyDate(first)} – ${prettyDate(last)}` })
  }, [onMeta, viewRows.length, shown])

  if (err) return <Empty icon="⚠️" title="NFL board unavailable" hint={err} />
  if (!picks) {
    return <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}>
      <Spinner size={28} />
    </div>
  }
  if (!picks.length) {
    return <Empty icon="🏈" title="No NFL lines right now"
                  hint="Lines show up here, priced, as soon as PrizePicks or Underdog list the next games." />
  }

  return (
    <div>
      <BoardSummary rows={viewRows} />

      <TopPlays rows={viewRows}
                onOpen={r => { setOpen(r._pick)
                               setFocusProp(r._pick?.prop_type || null) }}
                saved={r => has(r.key)}
                onSave={(r, e) => { e.stopPropagation(); toggle(r.key) }} />

      <SectionLabel right={
        tally.w + tally.l > 0
          ? `${tally.w}-${tally.l} · ${groups.length} players`
          : `${groups.length} players · ${rows.length} lines`
      }>Full board</SectionLabel>

      {/* The SAME wrapper the tennis board uses — CSS multi-column, 2 up on a
          wide screen and 3 on a very wide one, gated on the .baseline-wide
          ancestor. Reusing the class rather than a new grid keeps the two
          boards laying out identically at every breakpoint. */}
      <div className="baseline-cols">
        {pageGroups.map((g, i) => (
          <PlayerGroup key={g.key} g={g} index={i}
                       open={openKey === g.key}
                       onToggle={() => setOpenKey(k => (k === g.key ? null : g.key))}
                       saved={has(g.rows[0].key)}
                       onSave={() => toggle(g.rows[0].key)}
                       // The tapped row's prop, not always the group's first.
                       // This hardcoded rows[0], so on a player with several
                       // props every row opened the same one.
                       onOpen={(r) => { const row = r || g.rows[0]
                                        setOpen(row._pick)
                                        setFocusProp(row._pick?.prop_type || null) }}
                       footNoteFor={r => footNoteFor(r._pick)} />
        ))}
      </div>

      <Pager page={page} pages={pages} onPage={setPage}
             total={groups.length} label="players" />

      {open && (
        <NflPlayerSheet player={open} rows={picks} posted={posted}
                        focusProp={focusProp} onProject={onProject}
                        onClose={() => { setOpen(null); setFocusProp(null) }} />
      )}

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
