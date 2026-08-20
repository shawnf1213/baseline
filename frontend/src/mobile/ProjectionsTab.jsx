import { useState, useMemo } from 'react'
import { T } from './theme'
import { Card, Chip, Spinner, Empty, SectionLabel, MiniBars } from './bits'
import PlayerPhoto from './PlayerPhoto'
import ConfidenceGauge from '../components/ConfidenceGauge'
import { usePlayerSearch } from '../hooks/usePlayerSearch'
import { PROP_TYPES, SURFACES, shortProp, hitStrip, fmt } from './data'
import { calcProp, fetchHistory } from '../utils/api'
import { TOURNAMENT_CONFIG } from '../utils/constants'

// ── PROJECTIONS ──────────────────────────────────────────────────────────────
// The bot's /prop command, in the app. Same inputs (player, opponent, prop,
// surface, line), same engine (/api/prop/calculate), same answer — so a number
// read here and a number read in Discord cannot disagree.
//
// Laid out the way props.cash and Pick Finder present a prop: the verdict first
// at a size you can read without focusing, the supporting evidence under it,
// and the raw game log at the bottom. Discord has to lead with a title and a
// field list; a phone does not, so the projection and the lean carry the top of
// the card and everything else is support.
//
// THE SCANNER THAT USED TO BE IN THIS SLOT CRASHED THE APP, and the cause is
// worth not repeating: its resolve effect depended on a `visible` array rebuilt
// on every render while also calling setState, so each render scheduled the
// effect that caused the next render. Here the projection runs from an explicit
// button press and nothing derived feeds an effect, which makes that class of
// loop unreachable rather than merely absent.

function PlayerSlot({ label, value, onPick, tour, onClear }) {
  const [open, setOpen] = useState(false)
  const { query, setQuery, results, loading } = usePlayerSearch(tour)

  if (value && !open) {
    return (
      <Card onClick={() => { setOpen(true); setQuery('') }}
            style={{ display: 'flex', alignItems: 'center', gap: 10, padding: 10 }}>
        <PlayerPhoto id={value.id} name={value.name} size={38} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 9.5, fontFamily: T.cond, fontWeight: 800, letterSpacing: 1,
                        textTransform: 'uppercase', color: T.muted2 }}>{label}</div>
          <div style={{ fontSize: 15, fontWeight: 700, color: T.white, whiteSpace: 'nowrap',
                        overflow: 'hidden', textOverflow: 'ellipsis' }}>{value.name}</div>
        </div>
        <button onClick={(e) => { e.stopPropagation(); onClear() }}
                style={{ background: 'transparent', border: 'none', color: T.muted2,
                         fontSize: 20, cursor: 'pointer', padding: '0 6px' }}>×</button>
      </Card>
    )
  }

  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ fontSize: 9.5, fontFamily: T.cond, fontWeight: 800, letterSpacing: 1,
                    textTransform: 'uppercase', color: T.muted2, marginBottom: 5 }}>{label}</div>
      <input
        autoFocus={open}
        value={query}
        onChange={e => setQuery(e.target.value)}
        placeholder={`Search ${label.toLowerCase()}…`}
        style={{
          width: '100%', boxSizing: 'border-box', minHeight: 46, padding: '0 14px',
          background: T.card, border: `1px solid ${T.border}`, borderRadius: 12,
          // 16px MINIMUM. Below it, iOS Safari zooms the page on focus and does
          // not zoom back out, so tapping the search box left the whole app
          // magnified until you pinched out by hand.
          color: T.white, fontSize: 16, outline: 'none',
        }}
      />
      {loading && <div style={{ padding: 10 }}><Spinner size={16} /></div>}
      {results?.slice(0, 6).map(p => (
        <Card key={p.id} onClick={() => {
          const t = p.gender === 'F' ? 'WTA' : p.gender === 'M' ? 'ATP' : tour
          onPick({ id: p.id, name: p.name, tour: t, currentRank: p.currentRank })
          setOpen(false); setQuery('')
        }} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: 9, marginTop: 6 }}>
          <PlayerPhoto id={p.id} name={p.name} size={32} />
          <span style={{ flex: 1, fontSize: 14, color: T.white }}>{p.name}</span>
          {p.currentRank && <span style={{ fontSize: 11, color: T.muted2 }}>#{p.currentRank}</span>}
        </Card>
      ))}
    </div>
  )
}

// Native <select>, deliberately not a custom menu. A horizontally scrolling chip
// row put "Break Pts Saved" and "Madrid Open" off the right edge of the screen
// with nothing indicating they were there. iOS renders a select as the system
// wheel picker, which shows every option, is reachable one-handed, and needs no
// scroll affordance of our own.
//
// fontSize MUST stay >= 16px: below that, Safari zooms the whole page when the
// control takes focus and the user is left pinched in on a form they were only
// trying to tap.
function Select({ label, value, onChange, options }) {
  return (
    <>
      <SectionLabel>{label}</SectionLabel>
      <div style={{ position: 'relative', marginBottom: 12 }}>
        <select
          value={value}
          onChange={e => onChange(e.target.value)}
          style={{
            width: '100%', boxSizing: 'border-box', minHeight: 50,
            padding: '0 40px 0 14px',
            background: T.card, border: `1px solid ${T.border}`, borderRadius: 12,
            color: T.white, fontSize: 16, fontWeight: 600, fontFamily: T.font,
            outline: 'none', appearance: 'none', WebkitAppearance: 'none',
          }}
        >
          {options.map(o => (
            <option key={o.value} value={o.value}
                    style={{ background: '#111', color: '#fff' }}>{o.label}</option>
          ))}
        </select>
        <span style={{ position: 'absolute', right: 15, top: '50%',
                       transform: 'translateY(-50%)', pointerEvents: 'none' }}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke={T.muted}
               strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M6 9l6 6 6-6" />
          </svg>
        </span>
      </div>
    </>
  )
}

function Stat({ label, value, tone }) {
  return (
    <div style={{ textAlign: 'center', flex: 1 }}>
      <div style={{ fontSize: 20, fontWeight: 800, color: tone || T.white,
                    fontVariantNumeric: 'tabular-nums', lineHeight: 1.1 }}>{value}</div>
      <div style={{ fontSize: 9, fontFamily: T.cond, fontWeight: 700, letterSpacing: 0.8,
                    textTransform: 'uppercase', color: T.muted2, marginTop: 3 }}>{label}</div>
    </div>
  )
}

export default function ProjectionsTab() {
  const [tour, setTour] = useState('ATP')
  const [player, setPlayer] = useState(null)
  const [opponent, setOpponent] = useState(null)
  // PROP_TYPES holds {key, short, history} objects — state is the KEY string.
  const [prop, setProp] = useState(PROP_TYPES[0].key)
  const [surface, setSurface] = useState('Hard')
  // '' = generic, matching the bot's court=None. Court names are
  // surface-specific, so switching surface must clear it or the request
  // carries a clay venue on a hard-court projection.
  const [court, setCourt] = useState('')

  // Courts are TOUR-specific as well as surface-specific. COURTS_BY_SURFACE —
  // which this used — is labelled "legacy flat list (backward compat)" in
  // constants.js and has no tour dimension, so selecting WTA still offered
  // Vienna, Basel and ATP Finals Turin: men's events a woman cannot play.
  // TOURNAMENT_CONFIG is the real map, split ATP/WTA, and every one of its 54
  // WTA names already exists in the backend's COURT_CPR, so these resolve
  // rather than silently falling back to generic.
  const courtOptions = useMemo(() => {
    const list = TOURNAMENT_CONFIG?.[tour]?.[surface] || []
    return [{ value: '', label: 'Generic (no venue)' }]
      .concat(list.map(c => ({ value: c.name, label: c.name })))
  }, [tour, surface])
  const [line, setLine] = useState('')
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState(null)
  const [hist, setHist] = useState(null)
  const [err, setErr] = useState(null)

  const ready = player && opponent && prop && line !== '' && !isNaN(Number(line))

  const run = async () => {
    if (!ready || busy) return
    setBusy(true); setErr(null); setRes(null); setHist(null)
    const ln = Number(line)
    try {
      const data = await calcProp({
        player_id: String(player.id), opponent_id: String(opponent.id),
        player_name: player.name, opponent_name: opponent.name,
        tour: player.tour || tour, surface, court,
        prop_type: prop, prop_line: ln,
      })
      setRes(data)
      // The game log is what turns a number into something you can argue with,
      // so it is fetched alongside rather than behind another tap.
      // Only some props have an over/under log — PROP_TYPES.history says which.
      if (PROP_TYPES.find(p => p.key === prop)?.history) {
        fetchHistory(String(player.id), player.tour || tour, prop, surface, ln)
          .then(h => setHist(hitStrip(h, ln)))
          .catch(() => {})
      }
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || 'Projection failed')
    } finally {
      setBusy(false)
    }
  }

  const proj = typeof res?.model_projection === 'number' ? res.model_projection : null
  const ln = Number(line)
  const edge = proj != null && !isNaN(ln) ? Math.round((proj - ln) * 10) / 10 : null
  // The lean is the SIGN OF THE EDGE unless the backend states one — the
  // scenario-mixture props (Fantasy Score, Games Won, Break Points) take their
  // lean from P(over), not from mean-vs-line, and that answer wins.
  const lean = (res?.lean || (edge == null ? null : edge > 0 ? 'OVER' : edge < 0 ? 'UNDER' : null))
  const leanTone = lean === 'OVER' ? T.green : lean === 'UNDER' ? T.red : T.muted2

  const hitPct = useMemo(() => {
    const t = hist?.l10
    if (!t?.n) return null
    const side = lean === 'UNDER' ? t.u : t.o
    return Math.round((side / t.n) * 100)
  }, [hist, lean])

  return (
    <div style={{ padding: '0 0 90px' }}>
      <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 26, color: T.white,
                    letterSpacing: 0.5, marginBottom: 4 }}>Projections</div>
      <div style={{ fontSize: 12, color: T.muted2, marginBottom: 14 }}>
        The same engine the bot's /prop command runs.
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        {['ATP', 'WTA'].map(t => (
          <Chip key={t} active={tour === t}
                onClick={() => { setTour(t); setCourt('') }}>{t}</Chip>
        ))}
      </div>

      <PlayerSlot label="Player" value={player} tour={tour}
                  onPick={p => {
                    setPlayer(p)
                    if (p.tour && p.tour !== tour) { setTour(p.tour); setCourt('') }
                  }}
                  onClear={() => setPlayer(null)} />
      <PlayerSlot label="Opponent" value={opponent} tour={tour}
                  onPick={setOpponent} onClear={() => setOpponent(null)} />

      <Select label="Prop" value={prop} onChange={setProp}
              options={PROP_TYPES.map(p => ({ value: p.key, label: p.short }))} />

      <SectionLabel>Surface</SectionLabel>
      <div style={{ display: 'flex', gap: 6, paddingBottom: 10 }}>
        {SURFACES.map(s => (
          <Chip key={s} active={surface === s}
                onClick={() => { setSurface(s); setCourt('') }}>{s}</Chip>
        ))}
      </div>

      <Select label="Tournament court" value={court} onChange={setCourt}
              options={courtOptions} />

      <SectionLabel>Book line</SectionLabel>
      <input
        value={line}
        onChange={e => setLine(e.target.value.replace(/[^\d.]/g, ''))}
        inputMode="decimal"
        placeholder="e.g. 4.5"
        style={{
          width: '100%', boxSizing: 'border-box', minHeight: 48, padding: '0 14px',
          background: T.card, border: `1px solid ${T.border}`, borderRadius: 12,
          color: T.white, fontSize: 17, fontWeight: 700, outline: 'none', marginBottom: 12,
        }}
      />

      <button onClick={run} disabled={!ready || busy} style={{
        width: '100%', minHeight: 50, borderRadius: 13, border: 'none',
        background: ready && !busy ? T.green : T.card,
        color: ready && !busy ? '#062' : T.muted2,
        fontFamily: T.cond, fontWeight: 800, fontSize: 16, letterSpacing: 1,
        textTransform: 'uppercase', cursor: ready && !busy ? 'pointer' : 'default',
        marginBottom: 16,
      }}>
        {busy ? 'Projecting…' : 'Run projection'}
      </button>

      {busy && (
        <Card style={{ padding: 24, textAlign: 'center' }}>
          <Spinner />
          <div style={{ color: T.muted2, fontSize: 12, marginTop: 10 }}>
            A player we have not seen today can take a minute to pull.
          </div>
        </Card>
      )}

      {err && !busy && <Empty title="Could not project" hint={String(err)} />}

      {res && !busy && (
        <>
          {/* Verdict — the props.cash move: the answer, at a glance, first. */}
          <Card style={{ padding: 16, marginBottom: 10 }}>
            <div style={{ fontSize: 11, color: T.muted2, marginBottom: 10 }}>
              {player.name} vs {opponent.name} · {surface}{court ? ` · ${court}` : ''}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 13,
                              letterSpacing: 1, textTransform: 'uppercase',
                              color: T.muted, marginBottom: 4 }}>
                  {shortProp(prop)}
                </div>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                  <span style={{ fontSize: 40, fontWeight: 800, color: T.white,
                                 lineHeight: 1, fontVariantNumeric: 'tabular-nums' }}>
                    {proj != null ? fmt(proj) : '—'}
                  </span>
                  <span style={{ fontSize: 15, fontWeight: 800, color: leanTone }}>
                    {lean || ''}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: T.muted2, marginTop: 6 }}>
                  vs line {fmt(ln)}
                </div>
              </div>
              {res.confidence != null && (
                <ConfidenceGauge confidence={Math.round(res.confidence)} size={86} showLabel={false} />
              )}
            </div>
          </Card>

          <Card style={{ display: 'flex', padding: '14px 10px', marginBottom: 10 }}>
            <Stat label="Edge" value={edge != null ? (edge > 0 ? `+${fmt(edge)}` : fmt(edge)) : '—'}
                  tone={edge > 0 ? T.green : edge < 0 ? T.red : T.white} />
            <Stat label="Confidence" value={res.confidence != null ? Math.round(res.confidence) : '—'}
                  tone={res.confidence >= 75 ? T.green : res.confidence >= 60 ? T.amber : T.muted} />
            <Stat label="L10 hit" value={hitPct != null ? `${hitPct}%` : '—'}
                  tone={hitPct >= 70 ? T.green : hitPct >= 50 ? T.amber : hitPct != null ? T.red : T.muted} />
            <Stat label="Win prob"
                  value={res.p1_win_prob != null ? `${Math.round(res.p1_win_prob)}%` : '—'} />
          </Card>

          {/* Game log — Pick Finder's bottom half: show the actual results. */}
          {hist?.values?.length > 0 && (
            <Card style={{ padding: 14, marginBottom: 10 }}>
              <div style={{ fontSize: 9.5, fontFamily: T.cond, fontWeight: 800, letterSpacing: 1,
                            textTransform: 'uppercase', color: T.muted2, marginBottom: 8 }}>
                Last 10 vs this line
              </div>
              <MiniBars values={hist.values} refLine={ln} />
              {hist.average != null && (
                <div style={{ fontSize: 11, color: T.muted2, marginTop: 8 }}>
                  Average {fmt(hist.average)} over {hist.sample || hist.l10.n} matches
                </div>
              )}
            </Card>
          )}

          {res.explanation && (
            <Card style={{ padding: 14 }}>
              <div style={{ fontSize: 9.5, fontFamily: T.cond, fontWeight: 800, letterSpacing: 1,
                            textTransform: 'uppercase', color: T.muted2, marginBottom: 6 }}>Read</div>
              <div style={{ fontSize: 13, color: T.muted, lineHeight: 1.5 }}>{res.explanation}</div>
            </Card>
          )}

          <div style={{ fontSize: 10.5, color: T.muted2, textAlign: 'center', marginTop: 14 }}>
            Baseline · Model projections, not betting advice
          </div>
        </>
      )}

      {!res && !busy && !err && (
        <Empty title="Pick a matchup" hint="Choose both players, a prop and the book line." />
      )}
    </div>
  )
}
