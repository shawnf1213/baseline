import { useEffect, useMemo, useState } from 'react'
import { T, SAFE_TOP } from './theme'
import { Card, SectionLabel, Empty, Spinner, sideTone, tier, TierBadge,
         SideRail, ConfBar, BigStat, tierCardStyle } from './bits'
import { team, TeamMark, PlayerHead, DefenseMeter } from './nflviz'
import { GameLogChart, HitRing, FormStrip, StatPill, ShareBars } from './viz'
import { fetchNflPlayer } from '../utils/api'

// ── NFL PLAYER SHEET ─────────────────────────────────────────────────────────
// What opens when an NFL card is tapped, and the NFL counterpart of
// PlayerDashboard — but deliberately NOT that component. PlayerDashboard is
// tennis all the way down: fetchForm, fetchStats, fetchNextMatch and
// fetchHistory are tennis endpoints, and its content is surface splits and
// serve/return numbers. Pointing NFL at it would mean four failing requests and
// a screen of empty tennis sections.
//
// WHAT THIS CAN SHOW, and why it is drawn from props rather than fetched: the
// NFL model runs in the BOT, not the web backend, so there is no
// /api/nfl/player endpoint to call. Everything here comes from the board the
// bot already published — which is enough for the question a reader actually
// has when they tap a card: what else is priced on this player, how big is the
// disagreement, and has he been posted before.

const PROP_LABEL = {
  pass_yards: 'Pass Yards',
  rush_yards: 'Rush Yards',
  receiving_yards: 'Rec Yards',
  receptions: 'Receptions',
}

const fmt = (v, nd = 1) => (typeof v === 'number' ? v.toFixed(nd) : '—')

// Which game-log column settles each prop. Mirrors nfl/recap.py::RESULT_COL —
// the same field the resolver grades on, so what the chart shows and what the
// record counts can never disagree.
const FORM_FIELD = {
  pass_yards: 'passing_yards',
  rush_yards: 'rushing_yards',
  receiving_yards: 'receiving_yards',
  receptions: 'receptions',
}

function tally(vals, line) {
  const o = vals.filter(v => v > line).length
  const u = vals.filter(v => v < line).length
  return { o, u, p: vals.length - o - u, n: vals.length }
}

const ordinal = (n) => {
  const s = ['th', 'st', 'nd', 'rd'], v = n % 100
  return n + (s[(v - 20) % 10] || s[v] || s[0])
}

// What the defensive rating for each prop family is actually measuring, in
// words a reader does not have to decode.
const MATCHUP_LABEL = {
  pass_yards: 'vs the pass',
  receiving_yards: 'vs the pass',
  rush_yards: 'vs the run',
  receptions: 'vs the catch',
}

// RANK 1 ALLOWS LEAST — see nfl/ratings.py::defense_table. Saying "32nd" without
// saying which end is which is how a reader adjusts a number the wrong way.
const toughness = (rank, of) => {
  const q = rank / (of || 32)
  return q <= 0.25 ? 'tough' : q <= 0.5 ? 'above avg'
       : q <= 0.75 ? 'below avg' : 'soft'
}

// ── THE DEFENCE HE IS FACING ─────────────────────────────────────────────────
// The card could only ever answer "how has he done against this team before",
// and 71% of board rows have no such game — most opponents are faced once a
// season or not at all. This is what that defence allows EVERYONE, which is the
// version of the question with a season of evidence behind it. It is also the
// exact rating the projection applied, so this is the model's own working, not
// a decorative stat bolted on beside it.
function MatchupBlock({ m, prop, over, opponent }) {
  if (!m || !m.rank) return null
  const { rank, of, factor, raw, raw_label: rawLabel } = m
  // Above 1.00 means the defence allows MORE than average, which helps an OVER
  // and hurts an UNDER. Colouring by "good defence" instead would light up the
  // wrong half of the board.
  const favours = typeof factor === 'number'
    ? (over ? factor > 1 : factor < 1) : null
  const tone = favours == null ? T.muted : favours ? T.green : T.red

  return (
    <div style={{ marginTop: 12, paddingTop: 11,
                  borderTop: `1px solid ${T.border}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 11,
                       letterSpacing: 0.9, textTransform: 'uppercase',
                       color: T.muted, flex: 1 }}>
          {opponent} defence {MATCHUP_LABEL[prop] || ''}
        </span>
        <span style={{ fontFamily: T.cond, fontSize: 9.5, fontWeight: 800,
                       letterSpacing: 1, textTransform: 'uppercase', color: tone,
                       padding: '2.5px 7px', borderRadius: 6,
                       background: `${tone}1F`, border: `1px solid ${tone}66` }}>
          {toughness(rank, of)}
        </span>
      </div>

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8,
                    marginTop: 6, flexWrap: 'wrap' }}>
        <span style={{ color: tone, fontSize: 26, fontWeight: 800,
                       lineHeight: 1, letterSpacing: -0.5,
                       }}>
          {ordinal(rank)}
        </span>
        <span style={{ color: T.muted2, fontSize: 12, fontWeight: 700 }}>
          of {of}
        </span>
        {typeof raw === 'number' ? (
          <span style={{ color: T.muted, fontSize: 12, marginLeft: 'auto' }}>
            <b style={{ color: T.white, fontSize: 14 }}>{raw}</b> {rawLabel}
          </span>
        ) : null}
      </div>

      <DefenseMeter rank={rank} of={of} tone={tone} abbr={opponent} />
    </div>
  )
}

// ── ONE PROP, IN DETAIL ──────────────────────────────────────────────────────
// The card used to say OVER 4.5 · proj 5.7 and stop, which is a claim with no
// working shown. This is the working: every game against the line, how often he
// cleared it, what the defence allows, and his average.
//
// IT SPEAKS THE SAME VISUAL LANGUAGE AS THE BOARD — tierCardStyle, SideRail,
// BigStat, ConfBar — rather than inventing a flat one of its own. A card that
// looked like a spreadsheet next to a board that looks like a product is not a
// small inconsistency; it reads as two different apps.
//
// All the game-log maths is computed on the client rather than fetched: the log
// is already here, and a hit rate is only meaningful against the specific line
// being offered, which the server does not know.
function PropDetail({ r, posted, form, matchups, index = 0 }) {
  const matchup = (matchups && matchups[r.prop_type]) || null
  const lean = String(r.lean || '').toUpperCase()
  const over = lean === 'OVER'
  const { tone, rgb } = sideTone(lean)
  const conf = typeof r.confidence === 'number'
    ? (r.confidence <= 1 ? r.confidence * 100 : r.confidence) : null
  const field = FORM_FIELD[r.prop_type]
  const games = (form && form.games) || []
  const line = typeof r.line === 'number' ? r.line : null

  const played = games
    .filter(g => field && typeof g[field] === 'number')
    .map(g => ({ v: g[field], opp: g.opponent_team, wk: g.week }))

  const vals = played.map(g => g.v)
  const all = line != null ? tally(vals, line) : null
  const vsOpp = played.filter(g => g.opp && r.opponent && g.opp === r.opponent)
  const vsTally = line != null && vsOpp.length
    ? tally(vsOpp.map(g => g.v), line) : null
  const avg = vals.length
    ? vals.reduce((a, b) => a + b, 0) / vals.length : null
  // The side we are ON, so a hit rate reads as "our side landed", never as a
  // bare over-rate the reader has to invert in their head.
  const hits = all ? (over ? all.o : all.u) : null

  const res = posted && posted.result

  return (
    <Card index={index} style={{
      padding: '13px 14px 14px 17px', marginBottom: 10,
      position: 'relative', overflow: 'hidden',
      ...tierCardStyle(conf, rgb),
    }}>
      <SideRail rgb={rgb} weight={tier(conf).weight} />

      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 14,
                       letterSpacing: 1, textTransform: 'uppercase',
                       color: T.white, flex: 1 }}>
          {PROP_LABEL[r.prop_type] || r.prop_type}
        </span>
        <TierBadge conf={conf} tone={tone} rgb={rgb} />
        {posted && posted.is_potd ? <span style={{ fontSize: 13 }}>⭐</span> : null}
        {res === 'W' || res === 'L' ? (
          <span style={{ fontSize: 10.5, fontWeight: 800, padding: '2.5px 7px',
                         borderRadius: 6,
                         color: res === 'W' ? T.green : T.red,
                         background: res === 'W' ? 'rgba(0,230,118,0.14)'
                                                 : 'rgba(255,68,68,0.14)',
                         border: `1px solid ${res === 'W' ? 'rgba(0,230,118,0.5)'
                                                          : 'rgba(255,68,68,0.5)'}` }}>
            {res}{typeof posted.result_value === 'number'
              ? ` · ${posted.result_value}` : ''}
          </span>
        ) : null}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12,
                    marginTop: 9, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
          <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 25,
                         letterSpacing: 0.5, color: tone,
                         }}>{lean || '—'}</span>
          <span style={{ fontSize: 25, fontWeight: 800, color: T.white,
                         fontVariantNumeric: 'tabular-nums',
                         letterSpacing: -0.5 }}>{fmt(r.line)}</span>
        </div>
        <div style={{ marginLeft: 'auto' }}>
          <BigStat value={typeof r.edge === 'number'
                     ? `${r.edge > 0 ? '+' : ''}${fmt(r.edge)}` : '—'}
                   label="EDGE" proj={fmt(r.model_projection)}
                   tone={tone} rgb={rgb} />
        </div>
      </div>

      {conf != null && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginTop: 9 }}>
          <span style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5,
                         letterSpacing: 1.1, color: T.muted2 }}>CONFIDENCE</span>
          <ConfBar conf={conf} tone={tone} max={200} />
        </div>
      )}

      {played.length ? (
        <>
          <div style={{ marginTop: 13, padding: '4px 2px 0',
                        borderTop: `1px solid ${T.border}` }}>
            <div style={{ display: 'flex', justifyContent: 'space-between',
                          alignItems: 'baseline', marginTop: 9, marginBottom: 2 }}>
              <span style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 10,
                             letterSpacing: 1.1, color: T.muted2 }}>
                {form && form.season ? `${form.season} GAME LOG` : 'GAME LOG'}
              </span>
              <span style={{ fontSize: 10.5, color: tone, fontWeight: 700 }}>
                — — line {fmt(line)}
              </span>
            </div>
            <GameLogChart games={played} line={line} over={over} accent={tone} />
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 14,
                        marginTop: 10 }}>
            <HitRing hits={hits} n={all ? all.n : 0} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5,
                            letterSpacing: 1.1, color: T.muted2,
                            marginBottom: 5 }}>LAST 8</div>
              <FormStrip games={played} line={line} over={over} />
              {avg != null && line != null ? (
                <div style={{ marginTop: 9, fontSize: 12, color: T.muted }}>
                  Averages <b style={{ color: T.white, fontSize: 14 }}>
                    {avg.toFixed(1)}</b>{' '}
                  <span style={{ color: avg > line ? T.green : T.red,
                                 fontWeight: 700 }}>
                    ({avg > line ? '+' : ''}{(avg - line).toFixed(1)} vs line)
                  </span>
                </div>
              ) : null}
            </div>
          </div>
        </>
      ) : (
        <div style={{ color: T.muted2, fontSize: 11.5, marginTop: 10 }}>
          No game log published for this player yet.
        </div>
      )}

      {/* OUTSIDE the game-log branch on purpose. What the defence allows does
          not depend on us having his log — a rookie with no published games is
          exactly the card where the matchup is the only evidence there is. */}
      <MatchupBlock m={matchup} prop={r.prop_type} over={over}
                    opponent={r.opponent} />

      {vsOpp.length ? (
        <div style={{ marginTop: 11, paddingTop: 9,
                      borderTop: `1px solid ${T.border}`,
                      display: 'flex', alignItems: 'center', gap: 9 }}>
          <TeamMark abbr={r.opponent} size={30} />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5,
                          letterSpacing: 1.1, color: T.muted2 }}>
              HEAD TO HEAD
            </div>
            <div style={{ color: T.white, fontSize: 12.5, marginTop: 2 }}>
              {vsTally ? (
                <b style={{ color: (over ? vsTally.o : vsTally.u) > vsTally.n / 2
                              ? T.green : T.red }}>
                  {over ? vsTally.o : vsTally.u}/{vsTally.n} cleared
                </b>
              ) : null}
              {vsTally ? ' · ' : ''}
              {vsOpp.map(g => `wk${g.wk} ${g.v}`).join('  ·  ')}
            </div>
          </div>
        </div>
      ) : null}
    </Card>
  )
}

const pct1 = (v) => (typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—')

// Stats as tinted wells rather than a 2×2 of label-over-value, which was the
// most spreadsheet-like thing on the page. Auto-fit so four stats sit 2-up on a
// phone and 4-up on a desktop without a breakpoint.
function StatGrid({ rows, accent }) {
  return (
    <div style={{ display: 'grid', gap: 7, marginBottom: 9,
                  gridTemplateColumns: 'repeat(auto-fit, minmax(138px, 1fr))' }}>
      {rows.map(s => (
        <StatPill key={s.label} label={s.label} value={s.value} pct={s.pct}
                  of={s.of} accent={accent} />
      ))}
    </div>
  )
}

function Block({ label, children }) {
  return (
    <Card style={{ padding: 13, marginBottom: 8 }}>
      <div style={{ color: T.muted2, fontSize: 10.5, letterSpacing: 0.6,
                    textTransform: 'uppercase', marginBottom: 4 }}>{label}</div>
      {children}
    </Card>
  )
}

// The published profile — usage, role, matchup splits and recent form. Computed
// by the BOT (nfl/queries.py) and served from nfl_players, because the backend
// cannot import the model.
//
// THE PROPS ON THE CARD DECIDE WHICH STATS APPEAR — not the position. A running
// back's target share is a real stat and a useless one beside an UNDER on his
// rushing yards: it is not an input to that number and cannot move it. Position
// is only the fallback for a player with nothing priced.
// `pct` fills the pill's bar and `of` says what it is a share OF — the part a
// bare percentage always leaves out. Counts and rates get no bar, because there
// is no maximum to measure 4.02 yards a carry against.
const RUSH_STATS = (p) => [
  { label: 'Carries/game', value: fmt(p.carries_per_game) },
  { label: 'Yards/carry', value: fmt(p.yards_per_carry, 2) },
  ...(typeof p.snap_season === 'number'
    ? [{ label: 'Snap share', value: pct1(p.snap_season),
         pct: p.snap_season, of: "of the offence's snaps" }] : []),
  { label: 'Games', value: String(p.games ?? '—') },
]
const REC_STATS = (p) => [
  { label: 'Targets/game', value: fmt(p.targets_per_game) },
  { label: 'Target share', value: pct1(p.target_share),
    pct: p.target_share, of: "of the team's targets" },
  { label: 'Catch rate', value: pct1(p.catch_rate),
    pct: p.catch_rate, of: 'of targets caught' },
  { label: 'Yards/target', value: fmt(p.yards_per_target, 2) },
]
const PASS_STATS = (p) => [
  { label: 'Pass att/game', value: fmt(p.pass_att_per_game) },
  { label: 'Yards/attempt', value: fmt(p.yards_per_attempt, 2) },
  { label: 'Completion %', value: pct1(p.completion_pct),
    pct: p.completion_pct, of: 'of passes completed' },
  { label: 'Games', value: String(p.games ?? '—') },
]

function statsFor(p, props) {
  const has = (...xs) => xs.some(x => props.has(x))
  const out = []
  if (has('pass_yards')) out.push(...PASS_STATS(p))
  if (has('rush_yards')) out.push(...RUSH_STATS(p))
  if (has('receiving_yards', 'receptions')) out.push(...REC_STATS(p))
  if (out.length) {
    // An RB with both a rushing and a receiving prop gets both sets, so drop
    // the labels the two share rather than printing Games twice.
    const seen = new Set()
    return out.filter(s => !seen.has(s.label) && seen.add(s.label))
  }
  const pos = String(p.position || '').toUpperCase()
  return pos === 'QB' ? PASS_STATS(p)
       : (pos === 'RB' || pos === 'FB') ? RUSH_STATS(p) : REC_STATS(p)
}

// The role in a sentence, assembled only from figures that are actually there.
// Every clause is dropped when its number is missing rather than filled with a
// hedge, so this never asserts something the profile does not know.
function roleSentence(p) {
  const bits = []
  const snap = p.snap_season
  if (typeof snap === 'number') {
    bits.push(snap >= 0.7 ? 'A full-time role'
            : snap >= 0.45 ? 'A rotational role'
            : 'A committee role')
    bits.push(`${Math.round(snap * 100)}% of snaps`)
  }
  if (typeof p.target_share === 'number' && p.target_share > 0.02) {
    bits.push(`${Math.round(p.target_share * 100)}% of targets`)
  }
  if (typeof p.carries_per_game === 'number' && p.carries_per_game >= 1) {
    bits.push(`${p.carries_per_game.toFixed(1)} carries a game`)
  }
  if (typeof p.pass_att_per_game === 'number' && p.pass_att_per_game >= 1) {
    bits.push(`${p.pass_att_per_game.toFixed(1)} attempts a game`)
  }
  // role_change is the demotion flag from nfl/usage.py — worth saying out loud,
  // because it is the one thing here that changes what the model does.
  const rc = p.role_change || {}
  if (rc.factor && rc.factor < 0.97) {
    bits.push(`usage scaled down ${Math.round((1 - rc.factor) * 100)}% on a`
              + ' depth-chart demotion')
  }
  return bits.length ? bits.join(' · ') : ''
}

function ProfileBlock({ prof, props, teamAbbr }) {
  const p = prof.profile || {}
  const pos = String(p.position || '').toUpperCase()
  const priced = props || new Set()
  const stats = statsFor(p, priced)
  // Who he shares TARGETS with answers a receiving question. Beside a rushing
  // prop it is the same irrelevance as target share, so it goes with it.
  const showReceiving = !priced.size
    || priced.has('receiving_yards') || priced.has('receptions')

  const sp = p.splits || {}
  const comp = p.competition || {}
  const tg = p.targets || {}
  const form = prof.form || {}
  const acc = team(teamAbbr).c1
  const [allGames, setAllGames] = useState(false)
  // Six is enough to see a trend; seventeen is a table. The rest is one tap
  // away rather than a wall the reader has to scroll past every time.
  const shownGames = allGames ? form.games || [] : (form.games || []).slice(-6)
  // n is printed when an opponent was faced more than once: most are faced
  // once in a 17-game season, and a single game is a fact about that Sunday
  // rather than a matchup problem.
  //
  // Chips in club colours rather than a run-on line of "SEA 12 · SF 16 · GB 17",
  // which is a spreadsheet row wearing a label. Colour is the club's, not a
  // verdict — the section heading already says which end is which, and tinting
  // these green and red would double up on that while fighting the crest.
  const splitChips = (xs) => (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
      {(xs || []).map(x => {
        const tc = team(x.opp)
        return (
          <span key={x.opp} style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '4px 10px 4px 5px', borderRadius: 20,
            background: `${tc.c1}16`, border: `1px solid ${tc.c1}55`,
          }}>
            <TeamMark abbr={x.opp} size={20} plain />
            <b style={{ color: T.white, fontSize: 13.5,
                        fontVariantNumeric: 'tabular-nums' }}>{x.mean}</b>
            {x.n > 1 ? (
              <span style={{ color: T.muted2, fontSize: 10 }}>{x.n}g</span>
            ) : null}
          </span>
        )
      })}
    </div>
  )

  // Special-teams slots are never the role. nfl/usage.py::depth_rank now prefers
  // the offensive row, but a profile published before that fix would still say
  // "KR3" for a starting back, so the badge refuses one here too.
  const ST = ['KR', 'PR', 'LS', 'H', 'PK', 'P']
  const dp = String(p.depth_pos || '').toUpperCase()
  const role = (dp && !ST.includes(dp))
    ? `${dp}${p.depth_rank ?? ''}` : (pos || 'Role')

  return (
    <>
      {/* THE SEASON, not "prior season only". The window phrase is the model's
          own vocabulary and means nothing a reader can check — and what it
          refers to silently changes every September. */}
      <Card style={{ padding: '13px 14px', marginBottom: 9,
                     display: 'flex', alignItems: 'center', gap: 13 }}>
        <PlayerHead espnId={p.espn_id} abbr={teamAbbr} size={58} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7,
                        flexWrap: 'wrap' }}>
            <span style={{
              fontFamily: T.cond, fontWeight: 800, fontSize: 14,
              letterSpacing: 1.2, color: acc, padding: '2.5px 10px',
              borderRadius: 7, background: `${acc}18`,
              border: `1px solid ${acc}66`,
            }}>{role}</span>
            <span style={{ color: T.muted2, fontSize: 11.5 }}>
              {p.games ? `${p.games} games` : ''}
              {p.seasons ? ` · ${p.seasons}` : ''}
            </span>
          </div>
          {/* WHAT THE ROLE IS DOING, in a sentence. The badge says RB1 and the
              pills say 7.7 and 45.8%, but nothing said what that adds up to —
              and a reader who has to assemble it themselves reads a table. */}
          <div style={{ color: T.muted, fontSize: 12, marginTop: 6,
                        lineHeight: 1.4 }}>
            {roleSentence(p)}
          </div>
        </div>
      </Card>
      <StatGrid rows={stats} accent={acc} />

      {sp.worst && sp.worst.length ? (
        <Card style={{ padding: 13, marginBottom: 9 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 11,
                           letterSpacing: 1, textTransform: 'uppercase',
                           color: T.muted, flex: 1 }}>
              Matchup splits · {sp.label || ''}
            </span>
            {typeof sp.mean === 'number' ? (
              <span style={{ fontSize: 11.5, color: T.muted2 }}>
                season avg <b style={{ color: T.white, fontSize: 13 }}>{sp.mean}</b>
              </span>
            ) : null}
          </div>

          <div style={{ marginTop: 8 }}>
            <span style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5,
                           letterSpacing: 1.1, color: T.red }}>TOUGHEST</span>
            {splitChips(sp.worst)}
          </div>
          {sp.best && sp.best.length ? (
            <div style={{ marginTop: 10 }}>
              <span style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 9.5,
                             letterSpacing: 1.1, color: T.green }}>BEST</span>
              {splitChips(sp.best)}
            </div>
          ) : null}
        </Card>
      ) : null}

      {showReceiving && tg.targets && tg.targets.length ? (
        <Block label="Throws to">
          <ShareBars rows={tg.targets.map(t => ({
            key: t.player, label: t.player, share: t.share,
            right: `${t.targets} tgt · ${t.yards} yds`,
          }))} accent={acc} />
        </Block>
      ) : null}

      {showReceiving && comp.targets && comp.targets.length && comp.rank ? (
        <Block label={`Target share on ${comp.team} — #${comp.rank} of ${comp.of}`}>
          <ShareBars rows={comp.targets.map(t => ({
            key: t.player, label: t.player, share: t.share, me: t.is_player,
          }))} accent={acc} />
        </Block>
      ) : null}

      {form.games && form.games.length ? (
        <>
          <SectionLabel right={String(form.season || '')}>Game log</SectionLabel>
          <Card style={{ padding: '6px 0', marginBottom: 9, overflow: 'hidden' }}>
            {shownGames.map((gm, i) => {
              const tc = team(gm.opponent_team)
              return (
                <div key={i} style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '7px 13px',
                  // Zebra striping rather than 17 identical lines — the eye
                  // needs somewhere to rest when scanning a long log.
                  background: i % 2 ? T.cardHi : 'transparent',
                }}>
                  <span style={{ color: T.muted2, fontSize: 10.5, minWidth: 24,
                                 fontFamily: T.cond, fontWeight: 700 }}>
                    W{gm.week}
                  </span>
                  <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 12,
                                 color: tc.c1, minWidth: 32, letterSpacing: 0.4 }}>
                    {gm.opponent_team || '—'}
                  </span>
                  <span style={{ color: T.white, fontSize: 12.5, flex: 1,
                                 textAlign: 'right',
                                 fontVariantNumeric: 'tabular-nums' }}>
                    {gm.attempts
                      ? `${gm.completions || 0}/${gm.attempts} · ${gm.passing_yards || 0} yds`
                      : ''}
                    {gm.carries
                      ? `${gm.attempts ? '   ' : ''}${gm.carries} car · ${gm.rushing_yards || 0} yds`
                      : ''}
                    {gm.targets != null && !gm.attempts
                      ? `${gm.carries ? '   ' : ''}${gm.receptions || 0}/${gm.targets} · ${gm.receiving_yards || 0} yds`
                      : ''}
                  </span>
                </div>
              )
            })}
            {form.games.length > 6 ? (
              <button onClick={() => setAllGames(v => !v)} style={{
                width: '100%', minHeight: 40, background: 'transparent',
                border: 'none', borderTop: `1px solid ${T.border}`,
                color: T.muted, fontFamily: T.cond, fontWeight: 700,
                fontSize: 11.5, letterSpacing: 1, textTransform: 'uppercase',
                cursor: 'pointer', marginTop: 4,
              }}>
                {allGames ? 'Show less' : `All ${form.games.length} games`}
              </button>
            ) : null}
          </Card>
        </>
      ) : null}
    </>
  )
}


export default function NflPlayerSheet({ player, rows, posted, onClose }) {
  const [prof, setProf] = useState(null)
  const [loadingProf, setLoadingProf] = useState(true)

  useEffect(() => {
    let alive = true
    if (!player || !player.player) return
    setLoadingProf(true)
    fetchNflPlayer(player.player)
      .then(d => {
        if (!alive) return
        // A profile is a SNAPSHOT taken at publish time, and rows accumulate
        // one slate at a time, so a name query returns several. Take the one
        // published for the slate that was tapped; fall back to the newest.
        // Taking whatever came back first silently served a weeks-old game log
        // — and a short log cannot support an honest hit rate.
        const ps = (d && d.players) || []
        const exact = player.slate_date
          && ps.find(p => p.slate_date === player.slate_date)
        const newest = [...ps].sort(
          (a, b) => String(b.slate_date).localeCompare(String(a.slate_date)))[0]
        setProf(exact || newest || null)
      })
      .catch(() => { if (alive) setProf(null) })
      .finally(() => { if (alive) setLoadingProf(false) })
    return () => { alive = false }
  }, [player])

  // Everything priced on this player for the slate that was tapped — the
  // question "what else is on him" is the first one a reader has, and the
  // board itself cannot answer it because one-prop-per-player hides the rest.
  const mine = useMemo(
    () => (rows || []).filter(r => r.player === player?.player
                                && r.slate_date === player?.slate_date)
      .sort((a, b) => (b.confidence || 0) - (a.confidence || 0)),
    [rows, player])

  // Which prop families are actually priced on this card. The stat block keys
  // off this rather than off the position, so a back with only a rushing prop
  // is not handed his target share.
  const pricedProps = useMemo(
    () => new Set(mine.map(r => r.prop_type).filter(Boolean)), [mine])

  const postedFor = useMemo(() => {
    const m = new Map()
    for (const p of (posted || [])) {
      if (p.player === player?.player) m.set(`${p.slate_date}|${p.prop_type}`, p)
    }
    return m
  }, [posted, player])

  // Every time this player has been POSTED, not just today — the record is the
  // only thing here that is evidence rather than estimate.
  const history = useMemo(
    () => (posted || []).filter(p => p.player === player?.player
                                  && ['W', 'L', 'PUSH'].includes(p.result))
      .sort((a, b) => String(b.slate_date).localeCompare(String(a.slate_date))),
    [posted, player])

  const rec = useMemo(() => {
    const w = history.filter(p => p.result === 'W').length
    const l = history.filter(p => p.result === 'L').length
    return { w, l }
  }, [history])

  if (!player) return null
  const head = mine[0] || {}
  const myTeam = team(head.team || player.team)
  const oppTeam = team(head.opponent)
  // Kickoff is published as a UTC instant; show it in the reader's own zone
  // rather than as a Z-suffixed string only the scan cares about.
  let kickoff = ''
  try {
    if (head.kickoff) {
      kickoff = new Date(head.kickoff).toLocaleString(undefined, {
        weekday: 'short', hour: 'numeric', minute: '2-digit' })
    }
  } catch { kickoff = '' }

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 100, background: T.bg,
      overflowY: 'auto', WebkitOverflowScrolling: 'touch',
      paddingTop: SAFE_TOP, paddingBottom: 40,
    }}>
      <div style={{ maxWidth: 760, margin: '0 auto', padding: '12px 14px' }}>
        <button onClick={onClose} style={{
          minHeight: 44, padding: '0 16px', marginBottom: 14,
          background: T.card, color: T.white, border: `1px solid ${T.border}`,
          borderRadius: 12, fontFamily: T.cond, fontWeight: 700, fontSize: 14,
          letterSpacing: 0.8, textTransform: 'uppercase', cursor: 'pointer',
        }}>← Back</button>

        {/* A HEADER THAT LOOKS LIKE SPORT. Name over a wash of his club's
            colours, both crests, and the kickoff — the cues every scoreboard
            uses, and the fastest way to say "this is a game" rather than "this
            is a row". Colours come from the club, never from the model: tinting
            a header by confidence would sell the pick before the card does. */}
        <Card style={{
          padding: '15px 16px', marginBottom: 11, position: 'relative',
          overflow: 'hidden',
          background: `linear-gradient(135deg, ${myTeam.c1}26 0%,`
                    + ` ${myTeam.c2}14 42%, ${T.card} 78%)`,
          border: `1px solid ${myTeam.c1}44`,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 13 }}>
            <PlayerHead espnId={prof && prof.profile && prof.profile.espn_id}
                        abbr={head.team || player.team} size={54} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 31,
                            color: T.white, letterSpacing: 0.3, lineHeight: 1.02,
                            }}>
                {player.player}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7,
                            marginTop: 6, flexWrap: 'wrap' }}>
                <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 13,
                               letterSpacing: 0.8, color: myTeam.c1 }}>
                  {head.team || player.team}
                </span>
                {head.opponent ? (
                  <>
                    <span style={{ color: T.muted2, fontSize: 11 }}>vs</span>
                    <span style={{ fontFamily: T.cond, fontWeight: 800,
                                   fontSize: 13, letterSpacing: 0.8,
                                   color: oppTeam.c1 }}>{head.opponent}</span>
                  </>
                ) : null}
                {kickoff ? (
                  <span style={{ color: T.muted2, fontSize: 11.5,
                                 paddingLeft: 4 }}>· {kickoff}</span>
                ) : null}
              </div>
            </div>
            {head.opponent ? <TeamMark abbr={head.opponent} size={40} /> : null}
          </div>

          {rec.w + rec.l > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8,
                          marginTop: 12, paddingTop: 10,
                          borderTop: `1px solid ${T.border}` }}>
              <span style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 10,
                             letterSpacing: 1.1, color: T.muted2 }}>
                POSTED RECORD
              </span>
              <span style={{ fontSize: 15, fontWeight: 800, color: T.white,
                             fontVariantNumeric: 'tabular-nums' }}>
                {rec.w}<span style={{ color: T.muted2 }}>-</span>{rec.l}
              </span>
              <span style={{ fontSize: 12, fontWeight: 700,
                             color: rec.w > rec.l ? T.green : T.red }}>
                {Math.round((rec.w / (rec.w + rec.l)) * 100)}%
              </span>
            </div>
          )}
        </Card>

        {loadingProf && (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 20 }}>
            <Spinner size={20} />
          </div>
        )}

        <SectionLabel right={`${mine.length} priced`}>Props on this game</SectionLabel>
        {mine.length
          ? mine.map((r, i) => (
              <PropDetail key={`${r.prop_type}-${r.id}`} r={r} index={i}
                          posted={postedFor.get(`${r.slate_date}|${r.prop_type}`)}
                          form={prof && prof.form}
                          matchups={((prof && prof.profile
                                      && prof.profile.matchup) || {}).by_prop} />
            ))
          : <Empty icon="🏈" title="No priced props"
                   hint="Nothing on this player for that slate." />}

        {/* AFTER the props, not before them. The picks are why the card was
            tapped; role and usage are the supporting case for them, and putting
            a stat block first made the reader scroll past the answer to reach
            the question. */}
        {!loadingProf && prof && prof.profile && (
          <>
            <SectionLabel right={prof.profile.position || ''}>
              Player profile
            </SectionLabel>
            <ProfileBlock prof={prof} props={pricedProps}
                          teamAbbr={head.team || player.team} />
          </>
        )}

        {history.length > 0 && (
          <>
            <SectionLabel right={`${rec.w}-${rec.l}`}>Previously posted</SectionLabel>
            <Card style={{ padding: '5px 0', marginBottom: 9,
                           overflow: 'hidden' }}>
              {history.map((p, i) => {
                const w = p.result === 'W'
                const tn = w ? T.green : p.result === 'L' ? T.red : T.muted2
                const st = sideTone(p.lean)
                return (
                  <div key={p.id} style={{
                    display: 'flex', alignItems: 'center', gap: 9,
                    padding: '8px 13px',
                    background: i % 2 ? T.cardHi : 'transparent',
                  }}>
                    <span style={{
                      width: 20, height: 20, borderRadius: 5, flexShrink: 0,
                      display: 'flex', alignItems: 'center',
                      justifyContent: 'center', fontSize: 10, fontWeight: 800,
                      color: tn, background: `${tn}22`,
                      border: `1px solid ${tn}88`,
                    }}>{p.result}</span>
                    <span style={{ color: T.muted2, fontSize: 11, minWidth: 62,
                                   fontVariantNumeric: 'tabular-nums' }}>
                      {p.slate_date}
                    </span>
                    <span style={{ fontSize: 12.5, color: T.white, flex: 1 }}>
                      <b style={{ color: st.tone, fontFamily: T.cond,
                                  fontSize: 13, letterSpacing: 0.5 }}>
                        {(p.lean || '').toUpperCase()}
                      </b>{' '}{fmt(p.line)}{' '}
                      <span style={{ color: T.muted }}>
                        {PROP_LABEL[p.prop_type] || p.prop_type}
                      </span>
                    </span>
                    {typeof p.result_value === 'number' ? (
                      <span style={{ fontSize: 12.5, fontWeight: 800, color: tn,
                                     fontVariantNumeric: 'tabular-nums' }}>
                        {p.result_value}
                      </span>
                    ) : null}
                  </div>
                )
              })}
            </Card>
          </>
        )}

        <div style={{ color: T.muted2, fontSize: 11, textAlign: 'center',
                      padding: '18px 8px 4px', lineHeight: 1.5 }}>
          Edge = projection − line. Model projections, not betting advice.
        </div>
      </div>
    </div>
  )
}
