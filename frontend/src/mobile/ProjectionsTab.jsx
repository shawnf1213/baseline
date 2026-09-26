import { useState, useMemo, useEffect, useRef } from 'react'
import { T } from './theme'
import { Card, Chip, Spinner, Empty, SectionLabel, PageTitle,
         GlassTabs } from './bits'
import PlayerPhoto from './PlayerPhoto'
import { Reveal, Num, GrowBar, Ring, EdgeScale } from './motion'
import { TeamMark } from './nflviz'
import { usePlayerSearch } from '../hooks/usePlayerSearch'
import { PROP_TYPES, SURFACES, shortProp, hitStrip, fmt,
         matchupLabel, toughness, ordinal } from './data'
import { calcProp, fetchHistory, searchNflPlayers,
         fetchNflPropTypes, projectNfl, fetchNextMatch } from '../utils/api'
import { useTournamentConfig } from '../utils/tournaments'


// The slate names a tournament the way a broadcast does ("Guadalajara, Mexico");
// the court picker speaks COURT_CPR keys ("Guadalajara WTA"). Map one to the
// other so the picker can actually show what the match is being played at, and
// so this screen sends the same court the board sends.
function resolveCourtName(tournament, cfg, tour, surface) {
  const list = cfg?.[tour]?.[surface] || []
  if (!tournament) return ''
  const exact = list.find(c => c.name === tournament)
  if (exact) return exact.name
  const city = tournament.split(',')[0].trim().toLowerCase()
  if (!city) return ''
  const hit = list.find(c => c.name.toLowerCase().startsWith(city))
  return hit ? hit.name : ''
}

// ── SERVE / RETURN STAT BLOCK ────────────────────────────────────────────────
// The gap this closes: Discord's /prop card carries a per-player stat table —
// break points generated, conversion, hold and return rates, serve splits,
// archetype — and the app showed none of it. Same endpoint, same payload; the
// fields were already in the response and simply were not rendered. A projection
// you cannot interrogate is a number to take on faith, which is the opposite of
// the point.
//
// PROP-AWARE, and deliberately the SAME rows as the bot (bot.py::_prop_stats),
// so the two surfaces cannot show different evidence for the same number.
// Precision matches bot.py::_pct / _num EXACTLY \u2014 0 decimals on percentages,
// 1 on counts. Rendering 82.4% here against Discord's 82% would be the same
// disagreement this block exists to remove, only smaller.
const pct = (v) => (typeof v === 'number' ? `${v.toFixed(0)}%` : '\u2014')
const num = (v) => (typeof v === 'number' ? v.toFixed(1) : '\u2014')
const hand = (h) => {
  const u = String(h || '').toUpperCase()
  return u.startsWith('L') ? 'Left-handed' : u.startsWith('R') ? 'Right-handed' : null
}
// >35% earns the marker, same threshold as the bot.
const tbCell = (r) => (typeof r !== 'number' ? '\u2014'
  : `${r.toFixed(0)}%${r > 35 ? '  \u{1F3AF} SPECIALIST' : ''}`)

// Row sets are a LINE-FOR-LINE mirror of bot.py::_prop_stats. Not "similar" --
// identical, because the two surfaces run the same engine and must therefore
// justify a projection with the same evidence. If you change one, change both.
function statRows(prop, res, surface) {
  const ps = res.player_stats || {}
  const os = res.opponent_stats || {}
  const sfx = surface && surface !== 'All' ? ` (${surface})` : ''
  let p, o
  switch (prop) {
    case 'Aces':
      p = [[`Aces/Match${sfx}`, num(ps.aces)], ['1st Serve %', pct(ps.first_serve_pct)],
           ['1st Srv Won', pct(ps.first_serve_pts_won)]]
      o = [[`Aces Conceded/Match${sfx}`, num(res.opponent_ace_against)],
           ['Return 1st Won', pct(os.return_first_serve_pts_won)],
           [`Own Aces/Match${sfx}`, num(os.aces)]]
      break
    case 'Double Faults':
      p = [['DFs/Match', num(ps.double_faults)], ['2nd Srv Won', pct(ps.second_serve_pts_won)],
           ['1st Serve %', pct(ps.first_serve_pct)]]
      o = [['Return 2nd Won', pct(os.return_second_serve_pts_won)],
           ['DFs/Match', num(os.double_faults)]]
      break
    case 'Break Points Won':
      p = [['BP Generated/Match', num(res.bp_generated_per_match)],
           ['BP Gen (Quality-Adj)', num(res.bp_generated_quality_adj)],
           ['BP Conversion', pct(res.bp_blended_conv_pct ?? ps.bp_converted)],
           ['Service Games Won', pct(ps.service_games_won_pct)],
           ['Return Games Won', pct(ps.return_games_won_pct)]]
      o = [['BP Faced/Match', num(res.bp_blended_opp_faced)],
           ['Service Games Won', pct(os.service_games_won_pct)],
           ['Hold Rate', pct(res.opp_hold_rate_pct)],
           ['Server Quality', res.opp_server_quality_tier || res.opp_serve_tier || '\u2014'],
           ['1st Srv Won', pct(os.first_serve_pts_won)],
           ['2nd Srv Won', pct(os.second_serve_pts_won)]]
      break
    case 'Break Points Saved':
      // Drivers of (games broken) x (save rate). Serve-point splits and win rate
      // are deliberately absent here, exactly as in the bot.
      p = [['Service Games Won', pct(ps.service_games_won_pct)],
           ['Hold vs This Opp', pct(res.bps_effective_hold)],
           ['BP Saved', pct(res.bps_save_rate ?? ps.bp_saved)],
           ['BP Faced/Match', num(ps.bp_faced_count)],
           ['Proj. BP Faced', num(res.bps_faced_proj)]]
      o = [['Return Games Won', pct(os.return_games_won_pct)],
           ['BP Created/Match', num(os.return_bp_opportunities)],
           ['BP Conversion', pct(os.bp_converted)],
           ['Service Games Won', pct(os.service_games_won_pct)]]
      break
    case 'Player Total Games Won':
      p = [['Hold Rate', pct(res.player_hold_rate)],
           ['Break Rate vs Opp', pct(res.player_break_rate)]]
      o = [['Hold Rate', pct(res.opp_hold_rate_g)], ['Win Rate', pct(os.win_rate)]]
      break
    case 'Fantasy Score':
      // FANTASY SCORE IS A COMPOSITE, so it has to show what composes it. It
      // used to fall through to the generic serve rows below, which told a
      // reader nothing about where the number came from — while the BOT has
      // always printed "Projected · N aces · N double faults · N sets" on its
      // card. Same engine, same payload, two different amounts of evidence.
      //
      // fs_ace_proj and fs_df_proj are the per-match projections the score is
      // literally built from (each ace and double fault moves FS by ±0.5), and
      // they were already in the response — just never rendered here. The
      // per-match rates sit underneath them so the projection can be read
      // against the player's own baseline rather than taken on faith.
      //
      // The layout differs from the bot's (a table here, a drivers line there)
      // but the EVIDENCE is now the same, which is what the mirror is for.
      p = [['Proj. Aces', num(res.fs_ace_proj)],
           ['Proj. Double Faults', num(res.fs_df_proj)],
           [`Aces/Match${sfx}`, num(ps.aces)],
           ['DFs/Match', num(ps.double_faults)],
           ['1st Serve %', pct(ps.first_serve_pct)],
           ['1st Srv Won', pct(ps.first_serve_pts_won)],
           ['2nd Srv Won', pct(ps.second_serve_pts_won)],
           ['Win Rate', pct(ps.win_rate)]]
      o = [[`Aces Conceded/Match${sfx}`, num(res.opponent_ace_against)],
           ['DFs/Match', num(os.double_faults)],
           ['Return 1st Won', pct(os.return_first_serve_pts_won)],
           ['Return 2nd Won', pct(os.return_second_serve_pts_won)],
           ['1st Srv Won', pct(os.first_serve_pts_won)],
           ['2nd Srv Won', pct(os.second_serve_pts_won)],
           ['Win Rate', pct(os.win_rate)]]
      break
    default:   // Total Games
      p = [['1st Srv Won', pct(ps.first_serve_pts_won)],
           ['2nd Srv Won', pct(ps.second_serve_pts_won)], ['Win Rate', pct(ps.win_rate)]]
      o = [['1st Srv Won', pct(os.first_serve_pts_won)],
           ['2nd Srv Won', pct(os.second_serve_pts_won)], ['Win Rate', pct(os.win_rate)]]
  }
  // Tiebreak rate is suppressed for Break Points Saved: reaching 6-6 is a
  // match-length signal, not a serve-pressure one.
  if (prop !== 'Break Points Saved') {
    if (res.player_tiebreak_rate != null) p.push(['Tiebreak Rate', tbCell(res.player_tiebreak_rate)])
    if (res.opponent_tiebreak_rate != null) o.push(['Tiebreak Rate', tbCell(res.opponent_tiebreak_rate)])
  }
  return [p, o]
}

function StatColumn({ name, rows, arch, hd, serve }) {
  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: T.white, marginBottom: 8,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {name}
      </div>
      {rows.map(([lbl, val]) => (
        <div key={lbl} style={{ display: 'flex', justifyContent: 'space-between',
                                gap: 8, fontSize: 11.5, marginBottom: 5, alignItems: 'baseline' }}>
          <span style={{ color: T.muted2, minWidth: 0, overflow: 'hidden',
                         textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{lbl}</span>
          <span style={{ color: T.white, fontWeight: 700, textAlign: 'right' }}>{val}</span>
        </div>
      ))}
      {serve && <div style={{ fontSize: 11, color: T.muted, marginTop: 6 }}>{'\u{1F3BE} '}{serve}</div>}
      {arch && <div style={{ fontSize: 11, color: T.muted, marginTop: 4, fontStyle: 'italic' }}>{arch}</div>}
      {hd && <div style={{ fontSize: 11, color: T.muted2, marginTop: 2 }}>{'\u270B '}{hd}</div>}
    </div>
  )
}

function StatBlock({ prop, res, surface, playerName, opponentName }) {
  const [pRows, oRows] = statRows(prop, res, surface)
  return (
    <Card style={{ padding: 14, marginBottom: 10 }}>
      <div style={{ fontSize: 9.5, fontFamily: T.cond, fontWeight: 800, letterSpacing: 1,
                    textTransform: 'uppercase', color: T.muted2, marginBottom: 10 }}>
        Serve &amp; return
      </div>
      <div style={{ display: 'flex', gap: 14 }}>
        <StatColumn name={playerName} rows={pRows} arch={res.player_archetype}
                    hd={hand(res.player_handedness)} serve={res.player_serve_profile} />
        <StatColumn name={opponentName} rows={oRows} arch={res.opponent_archetype}
                    hd={hand(res.opponent_handedness)} serve={res.opponent_serve_profile} />
      </div>
    </Card>
  )
}

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

// ── A PLAYER, AS A TILE ──────────────────────────────────────────────────────
// This was a text input with a caption over it, twice, with the word VS wedged
// between them — which is a form pretending to be a matchup. A matchup has two
// faces in it. Empty, the tile is a dashed target that says what to do; filled,
// it is the player, and the search panel opens BELOW the pair rather than
// pushing the second tile down the page, so the block never changes height
// while you are picking.
function PlayerTile({ label, value, onClear, active, onActivate }) {
  if (value) {
    return (
      <div style={{
        flex: 1, minWidth: 0, padding: '14px 10px', borderRadius: T.r2,
        background: 'rgba(255,255,255,0.035)',
        border: `1px solid ${T.glassLine}`, textAlign: 'center',
        position: 'relative',
      }}>
        <button onClick={onClear} aria-label={`Clear ${label}`} style={{
          position: 'absolute', top: 4, right: 6, background: 'transparent',
          border: 'none', color: T.muted2, fontSize: 19, cursor: 'pointer',
          lineHeight: 1, padding: 4,
        }}>×</button>
        <div style={{ display: 'flex', justifyContent: 'center' }}>
          <PlayerPhoto id={value.id} name={value.name} size={52} />
        </div>
        <div style={{ fontSize: 14, fontWeight: 700, color: T.white, marginTop: 8,
                      lineHeight: 1.25, wordBreak: 'break-word' }}>
          {value.name}
        </div>
        <div style={{ fontSize: 10.5, color: T.muted2, marginTop: 2 }}>
          {value.currentRank ? `#${value.currentRank}` : label}
        </div>
      </div>
    )
  }

  return (
    <button onClick={onActivate} style={{
      flex: 1, minWidth: 0, padding: '14px 10px', borderRadius: T.r2,
      background: active ? `${T.green}0F` : 'transparent',
      border: `1.5px dashed ${active ? `${T.green}77` : T.glassLine}`,
      cursor: 'pointer', textAlign: 'center', fontFamily: T.font,
      WebkitTapHighlightColor: 'transparent',
    }}>
      <div style={{
        width: 52, height: 52, borderRadius: 26, margin: '0 auto',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        border: `1.5px dashed ${active ? `${T.green}77` : T.glassLine}`,
        color: active ? T.green : T.muted2, fontSize: 24, lineHeight: 1,
      }}>+</div>
      <div style={{ fontSize: 13, fontWeight: 700, marginTop: 8,
                    color: active ? T.green : T.muted }}>{label}</div>
      <div style={{ fontSize: 10.5, color: T.muted2, marginTop: 2 }}>
        {active ? 'search below' : 'tap to add'}
      </div>
    </button>
  )
}

// The search panel for whichever tile is active. One input for both tiles —
// two always-visible search boxes was the thing that made this read as a form.
function PlayerSearchPanel({ label, tour, onPick, onCancel }) {
  const { query, setQuery, results, loading } = usePlayerSearch(tour)
  return (
    <div style={{ marginTop: T.s2 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8,
                    marginBottom: 6 }}>
        <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 9.5,
                       letterSpacing: 1.2, textTransform: 'uppercase',
                       color: T.green, flex: 1 }}>Choose {label}</span>
        <button onClick={onCancel} style={{
          background: 'transparent', border: 'none', color: T.muted2,
          fontSize: 11.5, cursor: 'pointer', fontFamily: T.font,
        }}>Cancel</button>
      </div>
      <input
        autoFocus
        value={query}
        onChange={e => setQuery(e.target.value)}
        placeholder={`Search ${label.toLowerCase()}…`}
        style={{
          width: '100%', boxSizing: 'border-box', minHeight: 46, padding: '0 14px',
          background: 'rgba(255,255,255,0.04)',
          border: `1px solid ${T.green}55`, borderRadius: T.r1,
          // 16px MINIMUM. Below it, iOS Safari zooms the page on focus and does
          // not zoom back out, so tapping the box left the whole app magnified.
          color: T.white, fontSize: 16, outline: 'none',
        }}
      />
      {loading && <div style={{ padding: 10 }}><Spinner size={16} /></div>}
      {results?.slice(0, 6).map(p => (
        <button key={p.id} onClick={() => {
          const t = p.gender === 'F' ? 'WTA' : p.gender === 'M' ? 'ATP' : tour
          onPick({ id: p.id, name: p.name, tour: t, currentRank: p.currentRank })
        }} style={{
          display: 'flex', alignItems: 'center', gap: 10, width: '100%',
          padding: 9, marginTop: 6, borderRadius: T.r1, cursor: 'pointer',
          background: 'rgba(255,255,255,0.03)',
          border: `1px solid ${T.glassLine}`, fontFamily: T.font,
          textAlign: 'left', WebkitTapHighlightColor: 'transparent',
        }}>
          <PlayerPhoto id={p.id} name={p.name} size={32} />
          <span style={{ flex: 1, fontSize: 14, color: T.white }}>{p.name}</span>
          {p.currentRank ? (
            <span style={{ fontSize: 11, color: T.muted2 }}>#{p.currentRank}</span>
          ) : null}
        </button>
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
function NumField({ label, value, onChange, placeholder, signed, width = 108 }) {
  const clean = (v) => v.replace(signed ? /[^\d.\-]/g : /[^\d.]/g, '')
  return (
    <div style={{
      width, flexShrink: 0, position: 'relative', minHeight: 46,
      borderRadius: T.r1, background: 'rgba(255,255,255,0.03)',
      border: `1px solid ${value ? `${T.green}66` : T.glassLine}`,
    }}>
      <span style={{ position: 'absolute', left: 11, top: 6,
                     fontFamily: T.cond, fontWeight: 700, fontSize: 8.5,
                     letterSpacing: 1.2, color: T.muted2,
                     pointerEvents: 'none', whiteSpace: 'nowrap' }}>{label}</span>
      <input
        value={value}
        onChange={e => onChange(clean(e.target.value))}
        inputMode="decimal"
        placeholder={placeholder}
        style={{
          width: '100%', boxSizing: 'border-box', minHeight: 46,
          padding: '13px 11px 0', background: 'transparent', border: 'none',
          color: T.white, fontSize: 19, fontWeight: 800, outline: 'none',
          fontVariantNumeric: 'tabular-nums',
        }}
      />
    </div>
  )
}

function FieldLabel({ children }) {
  return (
    <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 9.5,
                  letterSpacing: 1.2, textTransform: 'uppercase',
                  color: T.muted2, marginBottom: 5 }}>{children}</div>
  )
}

function Select({ label, value, onChange, options, inline, bare }) {
  // A FRAGMENT HERE IS A LAYOUT BUG. Grid and flex parents lay out a fragment's
  // CHILDREN, not the fragment, so the label and the control became two
  // separate cells — the label stranded in one column with its dropdown in the
  // next. Wrapped, they move as one.
  return (
    <div style={{ minWidth: 0 }}>
      {bare ? null
        : inline ? <FieldLabel>{label}</FieldLabel>
        : <SectionLabel>{label}</SectionLabel>}
      <div style={{ position: 'relative', marginBottom: inline ? 0 : 12 }}>
        <select
          value={value}
          onChange={e => onChange(e.target.value)}
          style={{
            width: '100%', boxSizing: 'border-box', minHeight: 46,
            padding: '0 40px 0 14px',
            background: 'rgba(255,255,255,0.03)',
            border: `1px solid ${T.glassLine}`, borderRadius: T.r1,
            color: T.white, fontSize: 16, fontWeight: 600, fontFamily: T.font,
            outline: 'none', appearance: 'none', WebkitAppearance: 'none',
            textOverflow: 'ellipsis',
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
    </div>
  )
}

// ── HIT-RATE WINDOWS ─────────────────────────────────────────────────────────
// The row PickFinder leads with: the same prop measured over several windows,
// each showing how often it CLEARED and what it averaged. One number in
// isolation ("proj 4.7") tells you nothing about whether that is normal for
// this player; five windows side by side tell you whether the projection sits
// with the trend or against it, which is the actual question.
//
// Rate is always stated for the SIDE WE LEAN. On an UNDER, 20% overs is an 80%
// hit, and showing the raw over-rate would read as the opposite of the truth.
function HitWindows({ hist, lean, line }) {
  if (!hist) return null
  const vals = (hist.values || []).filter(v => typeof v === 'number')
  const side = (arr) => {
    const n = arr.length
    if (!n || line == null) return null
    const hits = arr.filter(v => lean === 'UNDER' ? v < line : v > line).length
    return { pct: Math.round((hits / n) * 100), avg: arr.reduce((a, b) => a + b, 0) / n, n }
  }
  const seasonPct = (() => {
    const o = hist.season?.over, u = hist.season?.under, pu = hist.season?.push
    const tot = (o || 0) + (u || 0) + (pu || 0)
    if (!tot) return null
    return Math.round(((lean === 'UNDER' ? u : o) / tot) * 100)
  })()
  const cells = [
    { k: 'L5', d: side(vals.slice(0, 5)) },
    { k: 'L10', d: side(vals) },
    { k: 'SEASON', d: seasonPct == null ? null
        : { pct: seasonPct, avg: hist.average, n: hist.season?.n } },
  ].filter(c => c.d)
  if (!cells.length) return null
  return (
    <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
      {cells.map(({ k, d }) => {
        const tone = d.pct >= 70 ? T.green : d.pct >= 50 ? T.amber : T.red
        return (
          <div key={k} style={{
            flex: 1, background: T.card, border: `1px solid ${T.border}`,
            borderRadius: 12, padding: '10px 8px', textAlign: 'center',
          }}>
            <div style={{ fontSize: 9.5, fontFamily: T.cond, fontWeight: 800,
                          letterSpacing: 1, color: T.muted2 }}>{k}</div>
            <div style={{ fontSize: 19, fontWeight: 800, color: tone,
                          fontVariantNumeric: 'tabular-nums', lineHeight: 1.25 }}>
              {d.pct}%
            </div>
            <div style={{ fontSize: 10.5, color: T.muted2 }}>
              avg {fmt(d.avg)}{d.n ? ` · ${d.n}g` : ''}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── GAME LOG ─────────────────────────────────────────────────────────────────
// Every game as a labelled bar with the LINE DRAWN THROUGH IT. The line is the
// whole point of the chart — without it a reader has to hold the number in
// their head and compare bar heights by eye. With it, clearing or missing is
// immediate.
function GameChart({ hist, line, lean }) {
  const games = (hist?.games || []).filter(g => typeof g.value === 'number')
  if (!games.length) return null
  const series = [...games].reverse()          // oldest -> newest, left to right
  const top = Math.max(line || 0, ...series.map(g => g.value)) * 1.25 || 1
  const H = 108
  return (
    <div style={{ background: T.card, border: `1px solid ${T.border}`,
                  borderRadius: 14, padding: '14px 12px 10px', marginBottom: 10 }}>
      <div style={{ fontSize: 9.5, fontFamily: T.cond, fontWeight: 800,
                    letterSpacing: 1, color: T.muted2, marginBottom: 12 }}>
        LAST {series.length} · LINE {fmt(line)}
      </div>
      <div style={{ position: 'relative', height: H, display: 'flex',
                    alignItems: 'flex-end', gap: 4 }}>
        {/* the line itself */}
        <div style={{ position: 'absolute', left: 0, right: 0,
                      bottom: `${Math.min(100, (line / top) * 100)}%`,
                      borderTop: `1.5px dashed ${T.muted2}`, opacity: 0.85, zIndex: 2 }} />
        {series.map((g, i) => {
          const cleared = lean === 'UNDER' ? g.value < line : g.value > line
          const h = Math.max(4, (g.value / top) * H)
          return (
            <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column',
                                  alignItems: 'center', justifyContent: 'flex-end' }}>
              <span style={{ fontSize: 10, fontWeight: 800, color: cleared ? T.green : T.red,
                             marginBottom: 3, fontVariantNumeric: 'tabular-nums' }}>
                {g.value % 1 === 0 ? g.value : fmt(g.value)}
              </span>
              <div style={{
                width: '100%', height: h, borderRadius: '4px 4px 2px 2px',
                background: cleared ? 'rgba(0,230,118,0.55)' : 'rgba(255,68,68,0.45)',
                border: `1px solid ${cleared ? T.green : T.red}`,
              }} />
            </div>
          )
        })}
      </div>
      <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
        {series.map((g, i) => (
          <div key={i} style={{ flex: 1, textAlign: 'center', overflow: 'hidden' }}>
            <div style={{ fontSize: 8.5, color: T.muted2, whiteSpace: 'nowrap' }}>
              {(g.date || '').slice(5).replace('-', '/')}
            </div>
            <div style={{ fontSize: 8, color: '#4a4a4a', whiteSpace: 'nowrap',
                          overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {(g.opponent || '').split(' ').slice(-1)[0].slice(0, 6)}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}


// ── THE VERDICT SHELL ────────────────────────────────────────────────────────
// One frame, three answers. Prop, spread and match all come off the SAME
// scenario mixture in one response, so they must look like one family — a
// separate card per mode is how three views of one calculation start reading as
// three different products.
function Verdict({ rgb, children }) {
  return (
    <Reveal>
      <div style={{
        position: 'relative', overflow: 'hidden', marginBottom: T.s3,
        borderRadius: T.r4, border: `1px solid rgba(${rgb},0.28)`,
        background: T.glass,
        boxShadow: `0 0 0 1px rgba(${rgb},0.08), 0 18px 46px rgba(0,0,0,0.5)`,
      }}>
        <div aria-hidden style={{
          position: 'absolute', inset: 0, pointerEvents: 'none',
          background: `radial-gradient(130% 95% at 8% 0%,`
                    + ` rgba(${rgb},0.16), transparent 62%),`
                    + ` radial-gradient(90% 70% at 100% 100%,`
                    + ` rgba(255,255,255,0.035), transparent 60%)`,
        }} />
        <div style={{ position: 'relative', padding: '18px 18px 16px' }}>
          {children}
        </div>
      </div>
    </Reveal>
  )
}

function VerdictHead({ player, opponent, surface, court }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
      <PlayerPhoto id={player.id} name={player.name} size={44} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 19,
                      color: T.white, lineHeight: 1.1, whiteSpace: 'nowrap',
                      overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {player.name}
        </div>
        <div style={{ fontSize: 11.5, color: T.muted2, marginTop: 2,
                      whiteSpace: 'nowrap', overflow: 'hidden',
                      textOverflow: 'ellipsis' }}>
          vs {opponent.name} · {surface}{court ? ` · ${court}` : ''}
        </div>
      </div>
      <PlayerPhoto id={opponent.id} name={opponent.name} size={34} />
    </div>
  )
}

// A row of figures under a verdict, on the shell's own divider.
function VerdictStats({ cells }) {
  return (
    <div style={{
      display: 'grid', gap: T.s2, marginTop: T.s4, paddingTop: T.s3,
      borderTop: `1px solid ${T.glassLine}`,
      gridTemplateColumns: 'repeat(auto-fit, minmax(88px, 1fr))',
    }}>
      {cells.filter(Boolean).map(([k, v, tone, bar], i) => (
        <div key={k}>
          <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5,
                        letterSpacing: 1.2, textTransform: 'uppercase',
                        color: T.muted2 }}>{k}</div>
          <div style={{ fontSize: 19, fontWeight: 800, color: tone || T.white,
                        lineHeight: 1.2, marginTop: 1,
                        fontVariantNumeric: 'tabular-nums' }}>{v}</div>
          {typeof bar === 'number' ? (
            <div style={{ marginTop: 5 }}>
              <GrowBar pct={bar} tone={tone || T.white} height={3}
                       delay={0.25 + i * 0.06} />
            </div>
          ) : null}
        </div>
      ))}
    </div>
  )
}

// ── SPREAD ───────────────────────────────────────────────────────────────────
// The engine has answered this since it was written and nothing ever asked. The
// headline is the probability of covering, because that is the decision; the
// projected margin is the working behind it.
function SpreadVerdict({ res, spread, player, opponent, surface, court }) {
  const cover = typeof res.spread_p_cover === 'number'
    ? res.spread_p_cover * 100 : null
  const margin = res.spread_margin_proj
  const covers = cover != null && cover >= 50
  const tone = cover == null ? T.muted2 : covers ? T.green : T.red
  const rgb = cover == null ? '107,107,107' : covers ? '0,230,118' : '255,68,68'
  const sp = Number(spread)

  return (
    <Verdict rgb={rgb}>
      <VerdictHead player={player} opponent={opponent} surface={surface}
                   court={court} />
      <div style={{ display: 'flex', alignItems: 'center', gap: T.s3,
                    marginTop: T.s4 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 11.5,
                        letterSpacing: 1.6, textTransform: 'uppercase',
                        color: T.muted2 }}>
            Games spread {sp > 0 ? '+' : ''}{sp}
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10,
                        marginTop: 2 }}>
            <Num value={cover} decimals={0} suffix="%" style={{
              fontSize: 'clamp(46px, 13vw, 62px)', fontWeight: 800,
              color: T.white, lineHeight: 1, letterSpacing: -2.5,
              fontVariantNumeric: 'tabular-nums' }} />
            <span style={{
              fontFamily: T.cond, fontWeight: 800, fontSize: 18,
              letterSpacing: 1, color: tone, padding: '3px 10px',
              borderRadius: 8, background: `rgba(${rgb},0.14)`,
              border: `1px solid rgba(${rgb},0.34)`,
            }}>{covers ? 'COVERS' : 'NO COVER'}</span>
          </div>
          <div style={{ fontSize: 12, color: T.muted, marginTop: 8 }}>
            projected margin{' '}
            <b style={{ color: T.white }}>
              {typeof margin === 'number'
                ? `${margin > 0 ? '+' : ''}${margin.toFixed(1)} games` : '—'}
            </b>
          </div>
        </div>
        {cover != null && (
          <Ring pct={cover} size={92} stroke={8} tone={tone} delay={0.15}>
            <Num value={cover} decimals={0} style={{ fontSize: 22,
              fontWeight: 800, color: T.white, lineHeight: 1 }} />
            <span style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9,
                           letterSpacing: 1.2, color: T.muted2,
                           marginTop: 2 }}>COVER</span>
          </Ring>
        )}
      </div>
      <VerdictStats cells={[
        ['Win prob', res.p1_win_prob != null
          ? `${Math.round(res.p1_win_prob)}%` : '—', T.white, res.p1_win_prob],
        ['Exp. sets', res.expected_sets != null
          ? res.expected_sets.toFixed(2) : '—', T.white, null],
        ['Format', res.match_format_label || '—', T.muted, null],
        ['Competitive', res.competitiveness != null
          ? `${Math.round(res.competitiveness)}%` : '—', T.white,
         res.competitiveness],
      ]} />
    </Verdict>
  )
}

// ── MATCH ────────────────────────────────────────────────────────────────────
// No line, no book — just what the model thinks happens. Every figure here was
// already in every response; the tab only ever showed the one the prop needed.
function MatchVerdict({ res, player, opponent, surface, court }) {
  const wp = res.p1_win_prob
  const favoured = wp != null && wp >= 50
  const tone = wp == null ? T.muted2 : favoured ? T.green : T.red
  const rgb = wp == null ? '107,107,107' : favoured ? '0,230,118' : '255,68,68'

  return (
    <Verdict rgb={rgb}>
      <VerdictHead player={player} opponent={opponent} surface={surface}
                   court={court} />
      <div style={{ display: 'flex', alignItems: 'center', gap: T.s3,
                    marginTop: T.s4 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 11.5,
                        letterSpacing: 1.6, textTransform: 'uppercase',
                        color: T.muted2 }}>To win the match</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10,
                        marginTop: 2 }}>
            <Num value={wp} decimals={0} suffix="%" style={{
              fontSize: 'clamp(46px, 13vw, 62px)', fontWeight: 800,
              color: T.white, lineHeight: 1, letterSpacing: -2.5,
              fontVariantNumeric: 'tabular-nums' }} />
            <span style={{
              fontFamily: T.cond, fontWeight: 800, fontSize: 18,
              letterSpacing: 1, color: tone, padding: '3px 10px',
              borderRadius: 8, background: `rgba(${rgb},0.14)`,
              border: `1px solid rgba(${rgb},0.34)`,
            }}>{favoured ? 'FAVOURED' : 'UNDERDOG'}</span>
          </div>
          <div style={{ fontSize: 12, color: T.muted, marginTop: 8 }}>
            {res.match_format_label || 'Best of 3'} ·{' '}
            <b style={{ color: T.white }}>
              {res.expected_sets != null
                ? `${res.expected_sets.toFixed(2)} sets` : '—'}
            </b> expected
          </div>
        </div>
        {wp != null && (
          <Ring pct={wp} size={92} stroke={8} tone={tone} delay={0.15}>
            <Num value={wp} decimals={0} style={{ fontSize: 22,
              fontWeight: 800, color: T.white, lineHeight: 1 }} />
            <span style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9,
                           letterSpacing: 1.2, color: T.muted2,
                           marginTop: 2 }}>WIN</span>
          </Ring>
        )}
      </div>
      <VerdictStats cells={[
        ['Total games', res.tg_model_proj != null
          ? res.tg_model_proj.toFixed(1) : '—', T.white, null],
        ['Competitive', res.competitiveness != null
          ? `${Math.round(res.competitiveness)}%` : '—', T.white,
         res.competitiveness],
        ['Gap', res.win_prob_gap != null
          ? `${Math.round(res.win_prob_gap)}%` : '—', T.muted, null],
        ['Environment', res.environment_label || '—', T.muted, null],
      ]} />
    </Verdict>
  )
}

// ── NFL PROJECTIONS ──────────────────────────────────────────────────────────
// The same builder and the same verdict as tennis, because it is the same
// question asked of a different sport — a second layout here would say the two
// are different products when they are one engine each.
//
// It exists at all because backend/nfl now sits beside the API. Before that the
// model ran only where the bot ran, so the site could show what had already
// been scanned and nothing else.
//
// ONE PLAYER, NOT TWO. A football prop is priced against the defence the
// schedule says he faces, so the opponent is looked up rather than chosen —
// picking one would let a reader ask for a game that is not being played.
function NflProjections({ prefill }) {
  const [avail, setAvail] = useState(null)      // null = still asking
  const [autoRun, setAutoRun] = useState(false)
  const [props, setProps] = useState([])
  const [prop, setProp] = useState('rush_yards')
  const [player, setPlayer] = useState(null)
  const [picking, setPicking] = useState(false)
  const [line, setLine] = useState('')
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    let alive = true
    fetchNflPropTypes()
      .then(d => { if (!alive) return
        setAvail(!!d?.ready); setProps(d?.props || []) })
      .catch(() => { if (alive) setAvail(false) })
    return () => { alive = false }
  }, [])

  // Same handoff as tennis. The NFL board row carries the player, the prop and
  // the line, and the projection endpoint resolves the game itself — so nothing
  // has to be typed.
  useEffect(() => {
    if (!prefill || prefill.sport !== 'nfl') return
    if (prefill.prop) setProp(prefill.prop)
    if (prefill.line != null) setLine(String(prefill.line))
    setPlayer(prefill.player
      ? { name: prefill.player, team: prefill.team } : null)
    setRes(null); setErr(null)
    setAutoRun(Boolean(prefill.player))
  }, [prefill?._at])   // eslint-disable-line react-hooks/exhaustive-deps

  const ready = !!player && !!prop && line !== '' && !isNaN(Number(line))
  const missing = !player ? 'Add a player'
    : !line ? 'Enter the book line' : 'Run projection'

  const run = async () => {
    if (!ready) return
    setBusy(true); setErr(null); setRes(null)
    try {
      const d = await projectNfl({ player: player.name, prop,
                                   line: Number(line), team: player.team })
      if (d?.ok === false) setErr(d.reason || 'Could not price this player.')
      else setRes(d)
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || 'Projection failed')
    } finally { setBusy(false) }
  }


  // AFTER run(), NOT BEFORE IT. `run` is a const arrow function, so referencing
  // it from an effect declared above it sits in the temporal dead zone — the
  // same shape as the 'w' before initialization crash, which is why
  // no-use-before-define is enabled. It happens to work because effects run
  // after render, and that is exactly the kind of accident that breaks later.
  useEffect(() => {
    if (autoRun && ready && !busy) { setAutoRun(false); run() }
  }, [autoRun, ready])   // eslint-disable-line react-hooks/exhaustive-deps

  // A deploy without the package says so, rather than showing a form whose
  // button can only ever fail.
  if (avail === false) {
    return <Empty icon="🏈" title="NFL pricing unavailable"
                  hint="This deploy does not carry the NFL model. The board and
                        player pages still work." />
  }

  const proj = typeof res?.projection === 'number' ? res.projection : null
  const ln = Number(line)
  const edge = proj != null && !isNaN(ln)
    ? Math.round((proj - ln) * 10) / 10 : null
  const lean = res?.lean || (edge == null ? null
    : edge > 0 ? 'OVER' : edge < 0 ? 'UNDER' : null)
  const tone = lean === 'OVER' ? T.green : lean === 'UNDER' ? T.red : T.muted2
  const rgb = lean === 'OVER' ? '0,230,118'
            : lean === 'UNDER' ? '255,68,68' : '107,107,107'
  const g = res?.game || {}

  return (
    <>
      <Card style={{ padding: 16, marginBottom: T.s3 }}>
        <div style={{ display: 'flex', alignItems: 'stretch', gap: T.s2 }}>
          {player ? (
            <div style={{
              flex: 1, minWidth: 0, padding: '14px 10px', borderRadius: T.r2,
              background: 'rgba(255,255,255,0.035)',
              border: `1px solid ${T.glassLine}`, textAlign: 'center',
              position: 'relative',
            }}>
              <button onClick={() => { setPlayer(null); setRes(null); setErr(null) }}
                      style={{
                position: 'absolute', top: 4, right: 6, background: 'transparent',
                border: 'none', color: T.muted2, fontSize: 19, cursor: 'pointer',
                lineHeight: 1, padding: 4,
              }}>×</button>
              <div style={{ display: 'flex', justifyContent: 'center' }}>
                <TeamMark abbr={player.team} size={52} />
              </div>
              <div style={{ fontSize: 14, fontWeight: 700, color: T.white,
                            marginTop: 8, lineHeight: 1.25 }}>{player.name}</div>
              <div style={{ fontSize: 10.5, color: T.muted2, marginTop: 2 }}>
                {[player.position, player.team].filter(Boolean).join(' · ')}
              </div>
            </div>
          ) : (
            <button onClick={() => setPicking(true)} style={{
              flex: 1, padding: '14px 10px', borderRadius: T.r2,
              background: picking ? `${T.green}0F` : 'transparent',
              border: `1.5px dashed ${picking ? `${T.green}77` : T.glassLine}`,
              cursor: 'pointer', textAlign: 'center', fontFamily: T.font,
            }}>
              <div style={{
                width: 52, height: 52, borderRadius: 26, margin: '0 auto',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                border: `1.5px dashed ${picking ? `${T.green}77` : T.glassLine}`,
                color: picking ? T.green : T.muted2, fontSize: 24,
              }}>+</div>
              <div style={{ fontSize: 13, fontWeight: 700, marginTop: 8,
                            color: picking ? T.green : T.muted }}>Player</div>
              <div style={{ fontSize: 10.5, color: T.muted2, marginTop: 2 }}>
                {picking ? 'search below' : 'tap to add'}
              </div>
            </button>
          )}

          {/* THE OPPONENT IS A FACT, NOT A CHOICE — it comes from the schedule
              once a player is picked. Offering it as a second tile would let a
              reader build a matchup that is not on the calendar. */}
          <div style={{
            flex: 1, minWidth: 0, padding: '14px 10px', borderRadius: T.r2,
            background: 'rgba(255,255,255,0.02)',
            border: `1px dashed ${T.glassLine}`, textAlign: 'center',
            display: 'flex', flexDirection: 'column', alignItems: 'center',
            justifyContent: 'center',
          }}>
            {g.opponent_team ? (
              <>
                <TeamMark abbr={g.opponent_team} size={52} />
                <div style={{ fontSize: 14, fontWeight: 700, color: T.white,
                              marginTop: 8 }}>{g.opponent_team}</div>
                <div style={{ fontSize: 10.5, color: T.muted2, marginTop: 2 }}>
                  {g.matchup || 'opponent'}
                </div>
              </>
            ) : (
              <>
                <div style={{ fontSize: 22, color: T.muted2 }}>🏈</div>
                <div style={{ fontSize: 12.5, color: T.muted2, marginTop: 8,
                              lineHeight: 1.4 }}>
                  Opponent comes<br />from the schedule
                </div>
              </>
            )}
          </div>
        </div>

        {picking && (
          <NflSearchPanel onCancel={() => setPicking(false)}
                          onPick={p => { setPlayer(p); setPicking(false) }} />
        )}

        <div style={{ height: 1, background: T.glassLine,
                      margin: `${T.s3}px 0` }} />

        <div style={{ display: 'flex', gap: T.s2, alignItems: 'stretch' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Select value={prop} onChange={setProp} inline bare
                    options={props.map(p => ({ value: p.key, label: p.label }))} />
          </div>
          <NumField label="LINE" value={line} onChange={setLine}
                    placeholder="50.5" />
        </div>

        <button onClick={run} disabled={!ready || busy} style={{
          width: '100%', minHeight: 54, borderRadius: T.r2,
          border: ready && !busy ? 'none' : `1px dashed ${T.glassLine}`,
          background: ready && !busy
            ? `linear-gradient(135deg, ${T.green}, #00B85C)` : 'transparent',
          color: ready && !busy ? '#04240f' : T.muted2,
          fontFamily: T.cond, fontWeight: 800, fontSize: 17, letterSpacing: 1.2,
          textTransform: 'uppercase',
          cursor: ready && !busy ? 'pointer' : 'default', marginTop: T.s4,
          boxShadow: ready && !busy ? `0 10px 30px ${T.green}3D` : 'none',
        }}>{busy ? 'Projecting…' : ready ? 'Run projection' : missing}</button>
      </Card>

      {busy && (
        <Card style={{ padding: 24, textAlign: 'center' }}>
          <Spinner />
          <div style={{ color: T.muted2, fontSize: 12, marginTop: 10 }}>
            First run of the day pulls the nflverse datasets — give it a moment.
          </div>
        </Card>
      )}

      {err && !busy && <Empty title="Could not project" hint={String(err)} />}

      {/* `player` guarded for the same reason as the tennis verdict: this
          card is built from the player it was run for and reads his name
          and his club straight off it. */}
      {res && !busy && player && (
        <>
          <Verdict rgb={rgb}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
              <TeamMark abbr={player.team} size={44} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 19,
                              color: T.white, lineHeight: 1.1 }}>
                  {player.name}
                </div>
                <div style={{ fontSize: 11.5, color: T.muted2, marginTop: 2 }}>
                  {res.position || player.position}
                  {g.matchup ? ` · ${g.matchup}` : ''}
                  {g.kickoff ? ` · ${String(g.kickoff).slice(5, 10)}` : ''}
                </div>
              </div>
              {g.opponent_team ? <TeamMark abbr={g.opponent_team} size={34} /> : null}
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: T.s3,
                          marginTop: T.s4 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 11.5,
                              letterSpacing: 1.6, textTransform: 'uppercase',
                              color: T.muted2 }}>
                  {(props.find(p => p.key === prop) || {}).label || prop}
                </div>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 10,
                              marginTop: 2 }}>
                  <Num value={proj} decimals={1} style={{
                    fontSize: 'clamp(46px, 13vw, 62px)', fontWeight: 800,
                    color: T.white, lineHeight: 1, letterSpacing: -2.5,
                    fontVariantNumeric: 'tabular-nums' }} />
                  <span style={{
                    fontFamily: T.cond, fontWeight: 800, fontSize: 20,
                    letterSpacing: 1, color: tone, padding: '3px 10px',
                    borderRadius: 8, background: `rgba(${rgb},0.14)`,
                    border: `1px solid rgba(${rgb},0.34)`,
                  }}>{lean || '—'}</span>
                </div>
                <div style={{ fontSize: 12, color: T.muted, marginTop: 8 }}>
                  book line <b style={{ color: T.white }}>{fmt(ln)}</b>
                  {edge != null ? (
                    <> · edge <b style={{ color: tone }}>
                      {edge > 0 ? '+' : ''}{fmt(edge)}</b></>
                  ) : null}
                </div>
              </div>
              {res.confidence != null && (
                <Ring pct={res.confidence} size={92} stroke={8} tone={tone}
                      delay={0.15}>
                  <Num value={res.confidence} decimals={0} style={{
                    fontSize: 24, fontWeight: 800, color: T.white,
                    lineHeight: 1 }} />
                  <span style={{ fontFamily: T.cond, fontWeight: 700,
                                 fontSize: 9, letterSpacing: 1.2,
                                 color: T.muted2, marginTop: 2 }}>CONF</span>
                </Ring>
              )}
            </div>

            <div style={{ marginTop: T.s3 }}>
              <EdgeScale line={ln} proj={proj} tone={tone} rgb={rgb} />
            </div>

            <VerdictStats cells={[
              ['P(over)', res.p_over != null
                ? `${Math.round(res.p_over * 100)}%` : '—', T.white,
               res.p_over != null ? res.p_over * 100 : null],
              ['Spread', g.player_spread != null
                ? `${g.player_spread > 0 ? '+' : ''}${g.player_spread}` : '—',
               T.white, null],
              ['Total', g.total != null ? String(g.total) : '—', T.white, null],
              ['Games', res.games_in_window != null
                ? String(res.games_in_window) : '—', T.muted, null],
            ]} />
          </Verdict>

          <NflDrivers res={res} />
        </>
      )}
    </>
  )
}

// The inputs behind the number. A football projection is volume × rate with an
// opponent applied to the rate, and every one of those terms is in the
// response — showing the answer without them is asking for faith.
function NflDrivers({ res }) {
  const d = res.drivers || {}
  const v = res.volume || {}
  const rows = Object.entries(d).filter(([, x]) => typeof x === 'number')
  if (!rows.length) return null
  const label = (k) => k.replace(/_/g, ' ')
  return (
    <Reveal i={1}>
      <Card style={{ padding: 14, marginBottom: T.s2 }}>
        <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 11,
                      letterSpacing: 1.6, textTransform: 'uppercase',
                      color: T.muted2, marginBottom: 10 }}>
          What the number is built from
        </div>
        <div style={{ display: 'grid', gap: T.s2,
                      gridTemplateColumns: 'repeat(auto-fit, minmax(104px, 1fr))' }}>
          {rows.map(([k, x]) => (
            <div key={k}>
              <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5,
                            letterSpacing: 1, textTransform: 'uppercase',
                            color: T.muted2 }}>{label(k)}</div>
              <div style={{ fontSize: 17, fontWeight: 800, color: T.white,
                            fontVariantNumeric: 'tabular-nums' }}>
                {x < 1 && x > 0 ? `${Math.round(x * 100)}%` : x.toFixed(2)}
              </div>
            </div>
          ))}
        </div>
        <div style={{ color: T.muted2, fontSize: 11, marginTop: 11, paddingTop: 9,
                      borderTop: `1px solid ${T.glassLine}`, lineHeight: 1.5 }}>
          {res.opponent
            ? <>Opponent <b style={{ color: T.white }}>{res.opponent}</b>
                {res.opponent_rank?.rank
                  ? <> — <b style={{ color: T.white }}>
                        {ordinal(res.opponent_rank.rank)} of {res.opponent_rank.of}
                      </b> {matchupLabel(res.prop)} ({toughness(res.opponent_rank.rank,
                        res.opponent_rank.of)}), allowing {res.opponent_rank.raw}
                      {' '}{res.opponent_rank.raw_label}</>
                  : null} — applied to the RATE at ×{res.opponent_factor}, never
                to volume, which the spread already reflects.</>
            : res.opponent_basis}
          {res.window ? <> · usage window: {res.window}.</> : null}
          {v.market_known === false
            ? ' No market for this game yet, so the script mixture is unweighted.'
            : ''}
        </div>
      </Card>
    </Reveal>
  )
}

function NflSearchPanel({ onPick, onCancel }) {
  const [q, setQ] = useState('')
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (q.trim().length < 2) { setRows([]); return }
    let alive = true
    setLoading(true)
    const t = setTimeout(() => {
      searchNflPlayers(q.trim())
        .then(d => { if (alive) setRows(d?.players || []) })
        .catch(() => { if (alive) setRows([]) })
        .finally(() => { if (alive) setLoading(false) })
    }, 220)
    return () => { alive = false; clearTimeout(t) }
  }, [q])

  return (
    <div style={{ marginTop: T.s2 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8,
                    marginBottom: 6 }}>
        <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 9.5,
                       letterSpacing: 1.2, textTransform: 'uppercase',
                       color: T.green, flex: 1 }}>Choose player</span>
        <button onClick={onCancel} style={{ background: 'transparent',
          border: 'none', color: T.muted2, fontSize: 11.5, cursor: 'pointer',
          fontFamily: T.font }}>Cancel</button>
      </div>
      <input autoFocus value={q} onChange={e => setQ(e.target.value)}
        placeholder="Search any NFL player…"
        style={{
          width: '100%', boxSizing: 'border-box', minHeight: 46,
          padding: '0 14px', background: 'rgba(255,255,255,0.04)',
          border: `1px solid ${T.green}55`, borderRadius: T.r1,
          color: T.white, fontSize: 16, outline: 'none',
        }} />
      {loading && <div style={{ padding: 10 }}><Spinner size={16} /></div>}
      {rows.slice(0, 6).map(p => (
        <button key={`${p.name}-${p.team}`} onClick={() => onPick(p)} style={{
          display: 'flex', alignItems: 'center', gap: 10, width: '100%',
          padding: 9, marginTop: 6, borderRadius: T.r1, cursor: 'pointer',
          background: 'rgba(255,255,255,0.03)',
          border: `1px solid ${T.glassLine}`, fontFamily: T.font,
          textAlign: 'left',
        }}>
          <TeamMark abbr={p.team} size={32} />
          <span style={{ flex: 1, fontSize: 14, color: T.white }}>{p.name}</span>
          <span style={{ fontSize: 11, color: T.muted2 }}>
            {[p.position, p.team].filter(Boolean).join(' · ')}
          </span>
        </button>
      ))}
    </div>
  )
}

export default function ProjectionsTab({ prefill }) {
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

  const tournamentConfig = useTournamentConfig()
  // Courts are TOUR-specific as well as surface-specific. COURTS_BY_SURFACE —
  // which this used — is labelled "legacy flat list (backward compat)" in
  // constants.js and has no tour dimension, so selecting WTA still offered
  // Vienna, Basel and ATP Finals Turin: men's events a woman cannot play.
  // The tour-split map is the real one, and every name in it exists in the
  // backend's COURT_CPR, so these resolve rather than silently falling back to
  // generic. It is FETCHED (see useTournamentConfig) so this picker and the
  // bot's cannot drift apart again.
  const courtOptions = useMemo(() => {
    const list = tournamentConfig?.[tour]?.[surface] || []
    return [{ value: '', label: 'Generic (no venue)' }]
      .concat(list.map(c => ({ value: c.name, label: c.name })))
  }, [tournamentConfig, tour, surface])
  const [line, setLine] = useState('')
  // Which tile the one shared search panel is filling.
  const [picking, setPicking] = useState(null)
  const [sport, setSport] = useState('tennis')
  const [mode, setMode] = useState('prop')   // prop | spread | match
  const [spread, setSpread] = useState('')
  // set when a board tap has populated the form; cleared once it runs
  const [autoRun, setAutoRun] = useState(false)
  // Names the ONE thing still missing, in the order a reader fills them.
  const missingLabel = !player ? 'Add a player'
    : !opponent ? 'Add an opponent'
    : mode === 'prop' && !line ? 'Enter the book line'
    : mode === 'spread' && !spread ? 'Enter the game spread'
    : 'Run projection'
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState(null)
  const [hist, setHist] = useState(null)
  const [err, setErr] = useState(null)

  // Clearing a selection must clear the ANSWER TOO. A verdict card is built
  // entirely from the two players it was run for — it prints their names and
  // their photos — so leaving one on screen after a player is removed is both
  // wrong (it describes a matchup that is no longer selected) and fatal: the
  // card reads player.name, and player is now null.
  //
  // That is the crash. Run an ATP projection, switch to WTA, press × on one of
  // the ATP players, and the whole app goes down on a null dereference.
  const clearResult = () => { setRes(null); setHist(null); setErr(null) }
  const clearPlayer = () => { setPlayer(null); clearResult() }
  const clearOpponent = () => { setOpponent(null); clearResult() }

  // SWITCHING TOUR CLEARS BOTH PLAYERS. An ATP player cannot be part of a WTA
  // matchup, so leaving them selected offers a projection that cannot be run
  // and, worse, one that LOOKS runnable.
  const switchTour = (t) => {
    if (t === tour) return
    setTour(t); setCourt(''); setPicking(null)
    setPlayer(null); setOpponent(null); clearResult()
  }

  // Readiness follows the MODE. A match question needs no line at all, and
  // gating that screen on a number it never uses would be asking for input to
  // throw away.
  const num = (v) => v !== '' && !isNaN(Number(v))
  // ── ARRIVED FROM A BOARD TAP ─────────────────────────────────────────────
  // The board already knows the matchup, the surface, the prop and the line —
  // and, because it priced the row, the resolved player ids. Re-typing all of
  // that is the step people abandon, so it is filled in and run for them.
  // Keyed on prefill._at so tapping the SAME prop twice re-runs.
  useEffect(() => {
    if (prefill?.sport === 'nfl') { setSport('nfl'); return }
    if (!prefill || prefill.sport !== 'tennis') return
    setSport('tennis')
    setMode('prop')
    if (prefill.tour) setTour(prefill.tour)
    if (prefill.surface) setSurface(prefill.surface)
    setCourt(prefill.court || '')
    if (prefill.prop) setProp(prefill.prop)
    if (prefill.line != null) setLine(String(prefill.line))
    setPlayer(prefill.playerId
      ? { id: prefill.playerId, name: prefill.player, tour: prefill.tour }
      : null)
    setOpponent(prefill.opponentId
      ? { id: prefill.opponentId, name: prefill.opponent, tour: prefill.tour }
      : null)
    setRes(null); setErr(null); setHist(null)
    // Only auto-run when the ids came through. Without them the form still
    // arrives filled, the reader picks the players, and nothing fires a
    // projection against someone we guessed at.
    setAutoRun(Boolean(prefill.playerId && prefill.opponentId))
  }, [prefill?._at])   // eslint-disable-line react-hooks/exhaustive-deps

  // ── THE SURFACE COMES FROM THE MATCH, NOT FROM THE PICKER ────────────────
  // This screen let the surface chip be whatever it was last set to, and then
  // projected on it. Pick Samsonova vs Kostyuk while the chip still says Clay
  // and you get 2.3 UNDER on a match that is actually being played on hard in
  // Guadalajara, where the model says 6.4 OVER. Same prop, same line, two
  // opposite calls — and the board was right, because the board has always
  // taken the surface from the scheduled match rather than from a control.
  //
  // So take it from the same place. The chips stay, because asking "what would
  // this be on clay" is a real question, but they start at reality instead of
  // at whatever the last question happened to be. Runs once per matchup, so a
  // deliberate change afterwards sticks.
  const [scheduled, setScheduled] = useState(null)
  const alignedFor = useRef(null)
  useEffect(() => {
    if (sport !== 'tennis' || !player?.id || !opponent?.id) { setScheduled(null); return undefined }
    const pairKey = `${player.id}|${opponent.id}`
    if (alignedFor.current === pairKey) return undefined
    let alive = true
    fetchNextMatch(String(player.id), player.tour || tour)
      .then((nm) => {
        // Only trust it when it is THIS matchup — the endpoint answers with the
        // player's next match, which after a withdrawal may be someone else.
        if (!alive || !nm || String(nm.opponent_id || '') !== String(opponent.id)) return
        alignedFor.current = pairKey
        setScheduled({ surface: nm.surface || '', tournament: nm.tournament || '' })
        if (nm.surface && nm.surface !== surface) {
          setSurface(nm.surface)
          clearResult()   // whatever is on screen was computed on the wrong court
        }
        setCourt(resolveCourtName(nm.tournament, tournamentConfig,
                                  player.tour || tour, nm.surface || surface))
      })
      .catch(() => { /* leave the picker as the reader left it */ })
    return () => { alive = false }
  }, [player?.id, opponent?.id, sport])   // eslint-disable-line react-hooks/exhaustive-deps

  const ready = !!player && !!opponent && (
    mode === 'prop' ? (!!prop && num(line))
    : mode === 'spread' ? num(spread)
    : true)

  const run = async () => {
    if (!ready || busy) return
    setBusy(true); setErr(null); setRes(null); setHist(null)
    const ln = Number(line)
    try {
      const data = await calcProp({
        player_id: String(player.id), opponent_id: String(opponent.id),
        player_name: player.name, opponent_name: opponent.name,
        tour: player.tour || tour, surface, court,
        // Total Games is the family the match and spread answers settle on, so
        // those modes ask for it and read the match-level fields off the same
        // response rather than inventing a second call.
        prop_type: mode === 'prop' ? prop : 'Total Games',
        prop_line: mode === 'prop' ? ln : 0,
        ...(mode === 'spread' ? { spread: Number(spread) } : {}),
      })
      setRes(data)
      // The game log is what turns a number into something you can argue with,
      // so it is fetched alongside rather than behind another tap.
      // Only some props have an over/under log — PROP_TYPES.history says which.
      if (mode === 'prop' && PROP_TYPES.find(p => p.key === prop)?.history) {
        fetchHistory(String(player.id), player.tour || tour, prop, surface, ln)
          .then(h => setHist({
            ...hitStrip(h, ln),
            // The per-game rows and the season counts drive the chart and the
            // window row; hitStrip alone flattens both away.
            games: Array.isArray(h?.last10) ? h.last10 : [],
            season: { over: h?.over, under: h?.under, push: h?.push,
                      n: h?.player_matches },
          }))
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

  // AFTER run(), NOT BEFORE IT. `run` is a const arrow function, so referencing
  // it from an effect declared above it sits in the temporal dead zone — the
  // same shape as the 'w' before initialization crash, which is why
  // no-use-before-define is enabled. It happens to work because effects run
  // after render, and that is exactly the kind of accident that breaks later.
  useEffect(() => {
    if (autoRun && ready && !busy) { setAutoRun(false); run() }
  }, [autoRun, ready])   // eslint-disable-line react-hooks/exhaustive-deps

  const leanTone = lean === 'OVER' ? T.green : lean === 'UNDER' ? T.red : T.muted2
  const leanRgb = lean === 'OVER' ? '0,230,118'
                : lean === 'UNDER' ? '255,68,68' : '107,107,107'

  const hitPct = useMemo(() => {
    const t = hist?.l10
    if (!t?.n) return null
    const side = lean === 'UNDER' ? t.u : t.o
    return Math.round((side / t.n) * 100)
  }, [hist, lean])

  return (
    <div style={{ padding: '0 0 90px' }}>
      <PageTitle>Price any matchup</PageTitle>

      <GlassTabs value={sport} onChange={setSport} style={{ marginBottom: T.s2 }}
                 options={[{ key: 'tennis', label: '🎾 Tennis' },
                           { key: 'nfl', label: '🏈 NFL' }]} />

      {sport === 'nfl' ? <NflProjections prefill={prefill} /> : (
      <>
      <GlassTabs value={mode} onChange={setMode} style={{ marginBottom: T.s3 }}
                 options={[{ key: 'prop', label: 'Prop' },
                           { key: 'spread', label: 'Spread' },
                           { key: 'match', label: 'Match' }]} />

      {/* ── THE MATCHUP BUILDER ──────────────────────────────────────────
          This was eight controls stacked down the page — tour, player,
          opponent, prop, surface, court, line, button — each full width, each
          with its own section label, so setting up one projection meant
          scrolling a form. It is ONE question ("price this matchup"), so it is
          now one card: the two players side by side across a VS, and the
          settings on a single row beneath them. */}
      <Card style={{ padding: 16, marginBottom: T.s3 }}>
        <GlassTabs value={tour} style={{ marginBottom: T.s3 }}
                   onChange={switchTour}
                   options={[{ key: 'ATP', label: 'ATP' },
                             { key: 'WTA', label: 'WTA' }]} />

        <div style={{ display: 'flex', alignItems: 'stretch', gap: T.s2 }}>
          <PlayerTile label="Player" value={player}
                      active={picking === 'player'}
                      onActivate={() => setPicking('player')}
                      onClear={clearPlayer} />
          {/* The VS medallion. Two inputs with a word between them is a form;
              this is the thing a matchup actually looks like. */}
          <div style={{
            alignSelf: 'center', flexShrink: 0, width: 34, height: 34,
            borderRadius: 17, display: 'flex', alignItems: 'center',
            justifyContent: 'center', background: 'rgba(255,255,255,0.05)',
            border: `1px solid ${T.glassLine}`, fontFamily: T.cond,
            fontWeight: 800, fontSize: 12, letterSpacing: 0.6, color: T.muted,
          }}>VS</div>
          <PlayerTile label="Opponent" value={opponent}
                      active={picking === 'opponent'}
                      onActivate={() => setPicking('opponent')}
                      onClear={clearOpponent} />
        </div>

        {picking ? (
          <PlayerSearchPanel
            label={picking === 'player' ? 'Player' : 'Opponent'}
            tour={tour}
            onCancel={() => setPicking(null)}
            onPick={p => {
              if (picking === 'player') {
                setPlayer(p)
                // Not switchTour: that clears both players, and this fires
                // BECAUSE one was just chosen.
                if (p.tour && p.tour !== tour) { setTour(p.tour); setCourt('') }
              } else setOpponent(p)
              setPicking(null)
            }} />
        ) : null}

        <div style={{ height: 1, background: T.glassLine, margin: `${T.s3}px 0` }} />

        {/* ── WHAT WE ARE PRICING ────────────────────────────────────────
            One statement, not two labelled fields — and it CHANGES WITH THE
            MODE. A spread mode showing a prop dropdown, or a match mode asking
            for a line the answer never uses, is asking for input to throw
            away. */}
        {mode === 'prop' ? (
          <div style={{ display: 'flex', gap: T.s2, alignItems: 'stretch' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <Select value={prop} onChange={setProp} inline bare
                      options={PROP_TYPES.map(p => ({ value: p.key, label: p.short }))} />
            </div>
            <NumField label="LINE" value={line} onChange={setLine}
                      placeholder="4.5" />
          </div>
        ) : mode === 'spread' ? (
          <div style={{ display: 'flex', gap: T.s3, alignItems: 'center' }}>
            <NumField label="GAMES SPREAD" value={spread} onChange={setSpread}
                      placeholder="-4.5" signed width={132} />
            <span style={{ color: T.muted2, fontSize: 11.5, lineHeight: 1.45,
                           flex: 1, minWidth: 0 }}>
              Negative lays games, positive receives — from{' '}
              {player ? player.name.split(' ').slice(-1)[0] : 'the player'}'s
              side.
            </span>
          </div>
        ) : (
          <div style={{ color: T.muted2, fontSize: 12, lineHeight: 1.5 }}>
            No line needed. This prices the match itself — who wins, how long it
            runs, and how close it should be.
          </div>
        )}

        <div style={{ marginTop: T.s3 }}>
          <div style={{ display: 'flex', gap: 6, marginBottom: T.s2,
                        flexWrap: 'wrap' }}>
            {SURFACES.map(sf => (
              <Chip key={sf} active={surface === sf}
                    onClick={() => { setSurface(sf); setCourt('') }}>{sf}</Chip>
            ))}
          </div>
          <Select value={court} onChange={setCourt} inline bare
                  options={courtOptions} />
          {/* SAY WHAT THE MATCH ACTUALLY IS. The surface drives the projection
              harder than anything else on this screen, and a chip that silently
              disagrees with the scheduled court produces a confident number for
              a match that is not being played. */}
          {scheduled?.surface && (
            <div style={{ marginTop: 6, fontSize: 11.5, lineHeight: 1.45,
                          color: scheduled.surface === surface ? T.muted2 : T.amber }}>
              {scheduled.surface === surface
                ? <>Scheduled: {scheduled.surface}{scheduled.tournament ? ` · ${scheduled.tournament}` : ''}</>
                : <>⚠ This match is scheduled on <strong>{scheduled.surface}</strong>
                    {scheduled.tournament ? ` · ${scheduled.tournament}` : ''} — you are
                    projecting it on {surface}.</>}
            </div>
          )}
        </div>

        <button onClick={run} disabled={!ready || busy} style={{
          width: '100%', minHeight: 54, borderRadius: T.r2,
          border: ready && !busy ? 'none' : `1px dashed ${T.glassLine}`,
          background: ready && !busy
            ? `linear-gradient(135deg, ${T.green}, #00B85C)` : 'transparent',
          color: ready && !busy ? '#04240f' : T.muted2,
          fontFamily: T.cond, fontWeight: 800, fontSize: 17, letterSpacing: 1.2,
          textTransform: 'uppercase',
          cursor: ready && !busy ? 'pointer' : 'default',
          marginTop: T.s4,
          boxShadow: ready && !busy ? `0 10px 30px ${T.green}3D` : 'none',
          transition: 'background 200ms ease, box-shadow 200ms ease',
        }}>
          {busy ? 'Projecting…' : ready ? 'Run projection' : missingLabel}
        </button>
      </Card>

      {busy && (
        <Card style={{ padding: 24, textAlign: 'center' }}>
          <Spinner />
          <div style={{ color: T.muted2, fontSize: 12, marginTop: 10 }}>
            A player we have not seen today can take a minute to pull.
          </div>
        </Card>
      )}

      {err && !busy && <Empty title="Could not project" hint={String(err)} />}

      {res && !busy && player && opponent && (
        <>
          {mode === 'spread' ? (
            <SpreadVerdict res={res} spread={spread} player={player}
                           opponent={opponent} surface={surface} court={court} />
          ) : mode === 'match' ? (
            <MatchVerdict res={res} player={player} opponent={opponent}
                          surface={surface} court={court} />
          ) : (
          <>
          {/* ── THE PROP VERDICT ────────────────────────────────────────────
              This was the number at 40px in the corner of a grey rectangle,
              with a gauge bolted on beside it and a four-cell strip underneath
              in a second identical rectangle. Everything the screen had to say
              was the same weight as everything else.

              The projection is the ONE thing this screen exists to produce, so
              it gets the size, a lit ground in its own direction, and it counts
              up to itself. The supporting numbers sit inside the same card
              rather than in another box below it — they are the working for
              this number, not a separate subject. */}
          <Reveal>
            <div style={{
              position: 'relative', overflow: 'hidden', marginBottom: T.s3,
              borderRadius: T.r4, border: `1px solid rgba(${leanRgb},0.28)`,
              background: T.glass,
              boxShadow: `0 0 0 1px rgba(${leanRgb},0.08), 0 18px 46px rgba(0,0,0,0.5)`,
            }}>
              <div aria-hidden style={{
                position: 'absolute', inset: 0, pointerEvents: 'none',
                background: `radial-gradient(130% 95% at 8% 0%,`
                          + ` rgba(${leanRgb},0.16), transparent 62%),`
                          + ` radial-gradient(90% 70% at 100% 100%,`
                          + ` rgba(255,255,255,0.035), transparent 60%)`,
              }} />

              <div style={{ position: 'relative', padding: '18px 18px 16px' }}>
                {/* Who, with both faces and the tournament. */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
                  <PlayerPhoto id={player.id} name={player.name} size={44} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontFamily: T.cond, fontWeight: 800,
                                  fontSize: 19, color: T.white, lineHeight: 1.1,
                                  whiteSpace: 'nowrap', overflow: 'hidden',
                                  textOverflow: 'ellipsis' }}>{player.name}</div>
                    <div style={{ fontSize: 11.5, color: T.muted2, marginTop: 2,
                                  whiteSpace: 'nowrap', overflow: 'hidden',
                                  textOverflow: 'ellipsis' }}>
                      vs {opponent.name} · {surface}{court ? ` · ${court}` : ''}
                    </div>
                  </div>
                  <PlayerPhoto id={opponent.id} name={opponent.name} size={34} />
                </div>

                {/* The number, at the size of the answer it is. */}
                <div style={{ display: 'flex', alignItems: 'center', gap: T.s3,
                              marginTop: T.s4 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontFamily: T.cond, fontWeight: 800,
                                  fontSize: 11.5, letterSpacing: 1.6,
                                  textTransform: 'uppercase', color: T.muted2 }}>
                      {shortProp(prop)}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'baseline',
                                  gap: 10, marginTop: 2 }}>
                      <Num value={proj} decimals={1} style={{
                        fontSize: 'clamp(46px, 13vw, 62px)', fontWeight: 800,
                        color: T.white, lineHeight: 1, letterSpacing: -2.5,
                        fontVariantNumeric: 'tabular-nums' }} />
                      <span style={{
                        fontFamily: T.cond, fontWeight: 800, fontSize: 20,
                        letterSpacing: 1, color: leanTone,
                        padding: '3px 10px', borderRadius: 8,
                        background: `rgba(${leanRgb},0.14)`,
                        border: `1px solid rgba(${leanRgb},0.34)`,
                      }}>{lean || '—'}</span>
                    </div>
                    <div style={{ fontSize: 12, color: T.muted, marginTop: 8 }}>
                      book line <b style={{ color: T.white }}>{fmt(ln)}</b>
                      {edge != null ? (
                        <> · edge{' '}
                          <b style={{ color: leanTone }}>
                            {edge > 0 ? '+' : ''}{fmt(edge)}
                          </b>
                        </>
                      ) : null}
                    </div>
                  </div>

                  {/* THE SAME NUMBER THE BOARD AND THE BOT SHOW — now the RAW
                      model score on all three, so the projection screen, the
                      board and Discord cannot disagree about the same prop.
                      See data.calibratedConfidence for what that trades. */}
                  {res.confidence != null && (
                    <Ring pct={res.confidence} size={92} stroke={8}
                          tone={leanTone} delay={0.15}>
                      <Num value={res.confidence} decimals={0} style={{
                        fontSize: 24, fontWeight: 800, color: T.white,
                        lineHeight: 1 }} />
                      <span style={{ fontFamily: T.cond, fontWeight: 700,
                                     fontSize: 9, letterSpacing: 1.2,
                                     color: T.muted2, marginTop: 2 }}>CONF</span>
                    </Ring>
                  )}
                </div>

                {/* The working, inside the same card. */}
                <div style={{
                  display: 'grid', gap: T.s2, marginTop: T.s4, paddingTop: T.s3,
                  borderTop: `1px solid ${T.glassLine}`,
                  gridTemplateColumns: 'repeat(auto-fit, minmax(88px, 1fr))',
                }}>
                  {[
                    ['L10 hit', hitPct != null ? `${hitPct}%` : '—',
                     hitPct >= 70 ? T.green : hitPct >= 50 ? T.amber
                       : hitPct != null ? T.red : T.muted2, hitPct],
                    ['Win prob', res.p1_win_prob != null
                      ? `${Math.round(res.p1_win_prob)}%` : '—', T.white,
                     res.p1_win_prob],
                    ['Season avg', hist?.average != null
                      ? fmt(hist.average) : '—', T.white, null],
                    ['Sample', hist?.player_matches != null
                      ? `${hist.player_matches}` : '—', T.muted, null],
                  ].map(([k, v, tone, bar], i) => (
                    <div key={k}>
                      <div style={{ fontFamily: T.cond, fontWeight: 700,
                                    fontSize: 9.5, letterSpacing: 1.2,
                                    textTransform: 'uppercase',
                                    color: T.muted2 }}>{k}</div>
                      <div style={{ fontSize: 19, fontWeight: 800, color: tone,
                                    lineHeight: 1.2, marginTop: 1,
                                    fontVariantNumeric: 'tabular-nums' }}>{v}</div>
                      {typeof bar === 'number' ? (
                        <div style={{ marginTop: 5 }}>
                          <GrowBar pct={bar} tone={tone} height={3}
                                   delay={0.25 + i * 0.06} />
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </Reveal>
          </>
          )}

          {/* The evidence behind the number — the same rows Discord shows.
              PROP ONLY: a serve/return table under a match verdict is evidence
              for a question that verdict did not answer. */}
          {mode === 'prop' && (
            <>
              <StatBlock prop={prop} res={res} surface={surface}
                         playerName={player?.name || 'Player'}
                         opponentName={opponent?.name || 'Opponent'} />
              <HitWindows hist={hist} lean={lean} line={ln} />
              <GameChart hist={hist} line={ln} lean={lean} />
            </>
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
        <Empty title="Pick a matchup"
               hint="Choose both players, a prop and the book line." />
      )}
      </>
      )}
    </div>
  )
}
