import { useEffect, useMemo, useState } from 'react'
import { T, SAFE_TOP } from './theme'
import { Card, SectionLabel, Empty, Spinner, MiniBars } from './bits'
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
  const tone = favours == null ? T.muted : favours ? T.green : '#E5534B'
  const pos = Math.max(0, Math.min(1, (rank - 1) / Math.max(1, (of || 32) - 1)))

  return (
    <div style={{ marginTop: 10, paddingTop: 9,
                  borderTop: `1px solid ${T.border}` }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
        <span style={{ color: T.muted2, fontSize: 10, letterSpacing: 0.6,
                       textTransform: 'uppercase', flex: 1 }}>
          {opponent} defence {MATCHUP_LABEL[prop] || ''}
        </span>
        <span style={{ color: tone, fontSize: 10.5, fontWeight: 800,
                       textTransform: 'uppercase', letterSpacing: 0.4 }}>
          {toughness(rank, of)}
        </span>
      </div>

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 7,
                    marginTop: 4, flexWrap: 'wrap' }}>
        <span style={{ color: T.white, fontSize: 15, fontWeight: 800 }}>
          {ordinal(rank)} <span style={{ color: T.muted, fontSize: 12,
                                         fontWeight: 600 }}>of {of}</span>
        </span>
        {typeof raw === 'number' ? (
          <span style={{ color: T.muted, fontSize: 12 }}>
            · {raw} {rawLabel}
          </span>
        ) : null}
      </div>

      {/* Where that defence sits across the league — toughest at the left. */}
      <div style={{ position: 'relative', height: 5, borderRadius: 3,
                    marginTop: 7, marginBottom: 3,
                    background: 'linear-gradient(90deg,'
                                + ' rgba(63,185,80,0.18), rgba(229,83,75,0.28))' }}>
        <div style={{ position: 'absolute', top: -2.5, left: `${pos * 100}%`,
                      width: 10, height: 10, marginLeft: -5, borderRadius: 5,
                      background: tone, border: `2px solid ${T.bg}` }} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between',
                    color: T.muted2, fontSize: 9.5 }}>
        <span>toughest</span><span>softest</span>
      </div>
    </div>
  )
}

// ── ONE PROP, IN DETAIL ──────────────────────────────────────────────────────
// The card used to say OVER 4.5 · proj 5.7 and stop, which is a claim with no
// working shown. This is the working: every game against the line, how often he
// cleared it, how often he cleared it against THIS opponent, and his average.
//
// All of it is computed from the published game log rather than fetched — the
// log is already on the client, and a hit rate is only meaningful against the
// specific line being offered, which the server does not know.
function PropDetail({ r, posted, form, matchups }) {
  const matchup = (matchups && matchups[r.prop_type]) || null
  const lean = String(r.lean || '').toUpperCase()
  const over = lean === 'OVER'
  const field = FORM_FIELD[r.prop_type]
  const games = (form && form.games) || []
  const line = typeof r.line === 'number' ? r.line : null

  const played = games
    .filter(g => field && typeof g[field] === 'number')
    .map(g => ({ v: g[field], opp: g.opponent_team, wk: g.week }))

  const vals = played.map(g => g.v)
  const all = line != null ? tally(vals, line) : null
  const last5 = line != null ? tally(vals.slice(-5), line) : null
  // Against THIS opponent specifically — the question a reader asks second,
  // right after "how often does he do it at all".
  const vsOpp = played.filter(g => g.opp && r.opponent && g.opp === r.opponent)
  const vsTally = line != null && vsOpp.length
    ? tally(vsOpp.map(g => g.v), line) : null
  const avg = vals.length
    ? vals.reduce((a, b) => a + b, 0) / vals.length : null
  // The side we are ON, so a hit rate reads as "our side landed", never as a
  // bare over-rate the reader has to invert in their head.
  const hits = all ? (over ? all.o : all.u) : null
  const hitPct = all && all.n ? Math.round((hits / all.n) * 100) : null
  const hit5 = last5 ? (over ? last5.o : last5.u) : null

  const res = posted && posted.result

  return (
    <Card style={{ padding: 13, marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 13,
                       letterSpacing: 0.8, textTransform: 'uppercase',
                       color: T.muted, flex: 1 }}>
          {PROP_LABEL[r.prop_type] || r.prop_type}
        </span>
        {posted && posted.is_potd ? <span style={{ fontSize: 12 }}>⭐</span> : null}
        {res === 'W' || res === 'L' ? (
          <span style={{ fontSize: 11, fontWeight: 800,
                         color: res === 'W' ? '#3FB950' : '#E5534B' }}>
            {res}{typeof posted.result_value === 'number'
              ? ` · ${posted.result_value}` : ''}
          </span>
        ) : null}
      </div>

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 6,
                    flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13 }}>{over ? '🟢' : '🔴'}</span>
        <span style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 19,
                       color: over ? T.green : '#E5534B' }}>{lean || '—'}</span>
        <span style={{ fontSize: 17, fontWeight: 800, color: T.white,
                       fontVariantNumeric: 'tabular-nums' }}>{fmt(r.line)}</span>
        <span style={{ color: T.muted, fontSize: 12.5 }}>
          · proj <b style={{ color: T.white }}>{fmt(r.model_projection)}</b>
          {typeof r.edge === 'number'
            ? ` · edge ${r.edge > 0 ? '+' : ''}${fmt(r.edge)}` : ''}
        </span>
      </div>

      {played.length ? (
        <>
          <div style={{ marginTop: 11, marginBottom: 5 }}>
            <MiniBars values={[...vals].reverse()} refLine={line} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between',
                        color: T.muted2, fontSize: 10.5, marginBottom: 9 }}>
            <span>oldest</span>
            <span>{played.length} games · line {fmt(line)}</span>
            <span>latest</span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
                        gap: 8 }}>
            <div>
              <div style={{ color: T.muted2, fontSize: 10, letterSpacing: 0.6,
                            textTransform: 'uppercase' }}>Hit rate</div>
              <div style={{ color: T.white, fontSize: 15, fontWeight: 800 }}>
                {hitPct != null ? `${hits}/${all.n} · ${hitPct}%` : '—'}
              </div>
            </div>
            <div>
              <div style={{ color: T.muted2, fontSize: 10, letterSpacing: 0.6,
                            textTransform: 'uppercase' }}>Last 5</div>
              <div style={{ color: T.white, fontSize: 15, fontWeight: 800 }}>
                {last5 && last5.n ? `${hit5}/${last5.n}` : '—'}
              </div>
            </div>
            <div>
              <div style={{ color: T.muted2, fontSize: 10, letterSpacing: 0.6,
                            textTransform: 'uppercase' }}>Average</div>
              <div style={{ color: T.white, fontSize: 15, fontWeight: 800 }}>
                {avg != null ? avg.toFixed(1) : '—'}
              </div>
            </div>
          </div>

          {vsOpp.length ? (
            <div style={{ marginTop: 9 }}>
              <div style={{ color: T.muted2, fontSize: 10, letterSpacing: 0.6,
                            textTransform: 'uppercase' }}>
                He's faced {r.opponent}
              </div>
              <div style={{ color: T.white, fontSize: 12.5, marginTop: 3 }}>
                {vsTally
                  ? `${over ? vsTally.o : vsTally.u}/${vsTally.n} cleared · `
                  : ''}
                {vsOpp.map(g => `wk${g.wk} ${g.v}`).join('  ·  ')}
              </div>
            </div>
          ) : null}
        </>
      ) : (
        <div style={{ color: T.muted2, fontSize: 11.5, marginTop: 9 }}>
          No game log published for this player yet.
        </div>
      )}

      {/* OUTSIDE the game-log branch on purpose. What the defence allows does
          not depend on us having his log — a rookie with no published games is
          exactly the card where the matchup is the only evidence there is. */}
      <MatchupBlock m={matchup} prop={r.prop_type} over={over}
                    opponent={r.opponent} />
    </Card>
  )
}

const pct1 = (v) => (typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—')

function StatGrid({ rows }) {
  return (
    <Card style={{ padding: 13, marginBottom: 8 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)',
                    gap: '10px 14px' }}>
        {rows.map(([k, v]) => (
          <div key={k}>
            <div style={{ color: T.muted2, fontSize: 10.5, letterSpacing: 0.6,
                          textTransform: 'uppercase' }}>{k}</div>
            <div style={{ color: T.white, fontSize: 15, fontWeight: 700 }}>{v}</div>
          </div>
        ))}
      </div>
    </Card>
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
const RUSH_STATS = (p) => [
  ['Carries/game', fmt(p.carries_per_game)],
  ['Yards/carry', fmt(p.yards_per_carry, 2)],
  ...(typeof p.snap_season === 'number'
    ? [['Snap share', pct1(p.snap_season)]] : []),
  ['Games', String(p.games ?? '—')],
]
const REC_STATS = (p) => [
  ['Targets/game', fmt(p.targets_per_game)],
  ['Target share', pct1(p.target_share)],
  ['Catch rate', pct1(p.catch_rate)],
  ['Yards/target', fmt(p.yards_per_target, 2)],
]
const PASS_STATS = (p) => [
  ['Pass att/game', fmt(p.pass_att_per_game)],
  ['Yards/attempt', fmt(p.yards_per_attempt, 2)],
  ['Completion %', pct1(p.completion_pct)],
  ['Games', String(p.games ?? '—')],
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
    return out.filter(([k]) => !seen.has(k) && seen.add(k))
  }
  const pos = String(p.position || '').toUpperCase()
  return pos === 'QB' ? PASS_STATS(p)
       : (pos === 'RB' || pos === 'FB') ? RUSH_STATS(p) : REC_STATS(p)
}

function ProfileBlock({ prof, props }) {
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
  // n is printed when an opponent was faced more than once: most are faced
  // once in a 17-game season, and a single game is a fact about that Sunday
  // rather than a matchup problem.
  const splitLine = (xs) => (xs || [])
    .map(x => `${x.opp} ${x.mean}${x.n > 1 ? ` (${x.n}g)` : ''}`).join('  ·  ')

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
      <SectionLabel right={p.seasons || p.window || ''}>{role}</SectionLabel>
      <StatGrid rows={stats} />

      {sp.worst && sp.worst.length ? (
        <Block label={`Toughest matchups (${sp.label || ''})`}>
          <div style={{ color: T.white, fontSize: 13 }}>{splitLine(sp.worst)}</div>
          {sp.best && sp.best.length ? (
            <div style={{ marginTop: 8 }}>
              <div style={{ color: T.muted2, fontSize: 10.5, letterSpacing: 0.6,
                            textTransform: 'uppercase' }}>Best matchups</div>
              <div style={{ color: T.white, fontSize: 13, marginTop: 3 }}>
                {splitLine(sp.best)}
              </div>
            </div>
          ) : null}
          {typeof sp.mean === 'number' ? (
            <div style={{ color: T.muted, fontSize: 11.5, marginTop: 8 }}>
              Season average {sp.mean} {sp.label}
            </div>
          ) : null}
        </Block>
      ) : null}

      {showReceiving && tg.targets && tg.targets.length ? (
        <Block label="Throws to">
          {tg.targets.map(t => (
            <div key={t.player} style={{ display: 'flex',
                  justifyContent: 'space-between', gap: 8, fontSize: 12.5,
                  color: T.white, marginTop: 3 }}>
              <span>{t.player}</span>
              <span style={{ color: T.muted }}>
                {t.targets} tgt ({Math.round((t.share || 0) * 100)}%) · {t.yards} yds
              </span>
            </div>
          ))}
        </Block>
      ) : null}

      {showReceiving && comp.targets && comp.targets.length && comp.rank ? (
        <Block label={`Target share on ${comp.team} — #${comp.rank} of ${comp.of}`}>
          <div style={{ color: T.white, fontSize: 12.5 }}>
            {comp.targets.map(t => {
              const last = String(t.player).split(' ').slice(-1)[0]
              const s = `${last} ${Math.round((t.share || 0) * 100)}%`
              return t.is_player ? <b key={t.player}>{s}{'  '}</b>
                                 : <span key={t.player}>{s}{'  '}</span>
            })}
          </div>
        </Block>
      ) : null}

      {form.games && form.games.length ? (
        <>
          <SectionLabel right={String(form.season || '')}>Recent form</SectionLabel>
          <Card style={{ padding: 13, marginBottom: 8 }}>
            {form.games.map((gm, i) => (
              <div key={i} style={{ display: 'flex', gap: 10, fontSize: 12.5,
                                    color: T.white, marginTop: i ? 5 : 0 }}>
                <span style={{ color: T.muted2, minWidth: 38 }}>wk{gm.week}</span>
                <span style={{ color: T.muted, minWidth: 40 }}>
                  {gm.opponent_team || ''}
                </span>
                <span>
                  {gm.targets != null
                    ? `${gm.receptions || 0}/${gm.targets} for ${gm.receiving_yards || 0}`
                    : ''}
                  {gm.carries ? `  ${gm.carries} car ${gm.rushing_yards || 0}` : ''}
                  {gm.attempts
                    ? `  ${gm.completions || 0}/${gm.attempts} for ${gm.passing_yards || 0}`
                    : ''}
                </span>
              </div>
            ))}
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

        <div style={{ marginBottom: 4 }}>
          <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 30,
                        color: T.white, letterSpacing: 0.4, lineHeight: 1.05 }}>
            {player.player}
          </div>
          <div style={{ color: T.muted, fontSize: 13, marginTop: 5 }}>
            {head.team || player.team}
            {head.opponent ? ` vs ${head.opponent}` : ''}
            {head.matchup ? ` · ${head.matchup}` : ''}
          </div>
          {rec.w + rec.l > 0 && (
            <div style={{ color: T.muted2, fontSize: 12, marginTop: 4 }}>
              Posted record: <b style={{ color: T.white }}>{rec.w}-{rec.l}</b>
            </div>
          )}
        </div>

        {loadingProf && (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 20 }}>
            <Spinner size={20} />
          </div>
        )}

        {!loadingProf && prof && prof.profile && (
          <ProfileBlock prof={prof} props={pricedProps} />
        )}

        <SectionLabel right={`${mine.length} priced`}>Props on this game</SectionLabel>
        {mine.length
          ? mine.map(r => (
              <PropDetail key={`${r.prop_type}-${r.id}`} r={r}
                          posted={postedFor.get(`${r.slate_date}|${r.prop_type}`)}
                          form={prof && prof.form}
                          matchups={((prof && prof.profile
                                      && prof.profile.matchup) || {}).by_prop} />
            ))
          : <Empty icon="🏈" title="No priced props"
                   hint="Nothing on this player for that slate." />}

        {history.length > 0 && (
          <>
            <SectionLabel right={`${rec.w}-${rec.l}`}>Previously posted</SectionLabel>
            {history.map(p => (
              <Card key={p.id} style={{ padding: 11, marginBottom: 6 }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                  <span style={{ color: T.muted2, fontSize: 11.5, minWidth: 72 }}>
                    {p.slate_date}
                  </span>
                  <span style={{ fontSize: 12.5, color: T.white, flex: 1 }}>
                    {(p.lean || '').toUpperCase()} {fmt(p.line)}{' '}
                    {PROP_LABEL[p.prop_type] || p.prop_type}
                  </span>
                  <span style={{ fontSize: 11.5, fontWeight: 800,
                                 color: p.result === 'W' ? '#3FB950'
                                   : p.result === 'L' ? '#E5534B' : T.muted2 }}>
                    {p.result}
                    {typeof p.result_value === 'number' ? ` · ${p.result_value}` : ''}
                  </span>
                </div>
              </Card>
            ))}
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
