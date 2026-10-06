// The visual pieces a pick's evidence is drawn with: the line-versus-
// projection scale, a confidence meter, the hit windows as rings, the game
// log with the book line and the average drawn through it, and the
// serve/return table.
import { ReactNode } from 'react'
import { StyleSheet, Text, View } from 'react-native'
import { LinearGradient } from 'expo-linear-gradient'
import { Ring } from './Ring'
import { F, T } from '@/theme'
import { Hist, fmt, fmtLine } from '@/lib/picks'

// ── BOOK LINE vs BASELINE ───────────────────────────────────────────────────
export function EdgeScale({ line, proj, tone }: { line: number | null; proj: number | null; tone: string }) {
  if (typeof line !== 'number' || typeof proj !== 'number') return null
  const lo = Math.min(line, proj), hi = Math.max(line, proj)
  const pad = Math.max((hi - lo) * 0.35, Math.abs(hi) * 0.06, 0.5)
  const a = lo - pad, b = hi + pad
  const at = (v: number) => ((v - a) / (b - a || 1)) * 100
  const lPct = at(line), pPct = at(proj)
  const from = Math.min(lPct, pPct), width = Math.abs(pPct - lPct)
  return (
    <View>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <View>
          <Text style={c.k}>Book line</Text>
          <Text style={[c.v, { color: T.muted }]}>{fmtLine(line)}</Text>
        </View>
        <View style={{ alignItems: 'flex-end' }}>
          <Text style={c.k}>Baseline projects</Text>
          <Text style={[c.v, { color: tone, fontSize: 20 }]}>{fmt(proj)}</Text>
        </View>
      </View>
      <View style={{ height: 16, marginTop: 6, justifyContent: 'center' }}>
        <View style={c.track} />
        <View style={[c.span, { left: `${from}%`, width: `${width}%`, backgroundColor: tone }]} />
        <View style={[c.dot, { left: `${lPct}%`, backgroundColor: T.muted2 }]} />
        <View style={[c.dot, c.dotBig, { left: `${pPct}%`, backgroundColor: tone }]} />
      </View>
    </View>
  )
}

// ── CONFIDENCE ──────────────────────────────────────────────────────────────
export function Meter({ pct, tone, height = 6 }: { pct: number | null; tone: string; height?: number }) {
  const w = pct == null ? 0 : Math.max(0, Math.min(100, pct))
  return (
    <View style={[c.meter, { height, borderRadius: height / 2 }]}>
      <View style={{ width: `${w}%`, height, borderRadius: height / 2, backgroundColor: tone }} />
    </View>
  )
}

// ── HIT WINDOWS ─────────────────────────────────────────────────────────────
// How often this prop cleared, on the side Baseline leans, over the last 5,
// last 10 and the season.
// `season` = show the season cell. Its counts come from the server, tallied
// against the line the request carried — so it is only meaningful when that
// was a real book line, not a placeholder.
export function HitWindows({ hist, lean, line, gamesWord = 'matches', season = true }:
  { hist: Hist | null; lean: string; line: number | null; gamesWord?: string; season?: boolean }) {
  if (!hist || line == null) return null
  const vals = hist.games.map(g => g.value)
  const side = (arr: number[]) => {
    const n = arr.length
    if (!n) return null
    const hits = arr.filter(v => lean === 'UNDER' ? v < line : v > line).length
    return { pct: Math.round((hits / n) * 100), avg: arr.reduce((a, b) => a + b, 0) / n, n }
  }
  const s = hist.season
  const tot = s.over + s.under + s.push
  const seasonPct = season && tot ? Math.round(((lean === 'UNDER' ? s.under : s.over) / tot) * 100) : null
  const cells = [
    { k: 'Last 5', d: side(vals.slice(0, 5)) },
    { k: 'Last 10', d: side(vals) },
    { k: 'Season', d: seasonPct == null ? null : { pct: seasonPct, avg: hist.average, n: s.n } },
  ].filter(x => x.d) as { k: string; d: { pct: number; avg: number | null; n: number } }[]
  if (!cells.length) return null
  return (
    <View style={{ flexDirection: 'row', gap: 8 }}>
      {cells.map(({ k, d }) => {
        const tone = d.pct >= 70 ? T.green : d.pct >= 50 ? T.amber : T.red
        return (
          <View key={k} style={c.cell}>
            <Ring value={d.pct} size={62} stroke={5} tone={tone}>
              <Text style={[c.ringNum, { color: T.white }]}>{d.pct}<Text style={c.ringPct}>%</Text></Text>
            </Ring>
            <Text style={[c.k, { marginTop: 8 }]}>{k}</Text>
            <Text style={c.sub}>avg {fmt(d.avg)}{d.n ? ` · ${d.n} ${gamesWord}` : ''}</Text>
          </View>
        )
      })}
    </View>
  )
}

// ── GAME LOG ────────────────────────────────────────────────────────────────
// Every recent match as a bar, with the book line (dashed, labelled) and the
// player's average (dotted) drawn through them; green cleared the line on the
// side Baseline leans, red did not.
export function GameChart({ hist, line, lean, gamesWord = 'matches' }:
  { hist: Hist | null; line: number | null; lean: string; gamesWord?: string }) {
  const games = hist?.games || []
  if (!games.length || line == null) return null
  const series = [...games].reverse()           // oldest -> newest
  const avg = series.reduce((a, g) => a + g.value, 0) / series.length
  const top = Math.max(line, avg, ...series.map(g => g.value)) * 1.22 || 1
  const H = 132
  const at = (v: number) => Math.min(H - 2, Math.max(0, (v / top) * H))
  const hits = series.filter(g => (lean === 'UNDER' ? g.value < line : g.value > line)).length
  // "2026-10-04" prints as 10/04; anything else ("Wk 5") prints as given.
  const dateLabel = (d?: string) => (/^\d{4}-\d{2}-\d{2}/.test(d || '') ? d!.slice(5, 10).replace('-', '/') : (d || ''))
  return (
    <View>
      <View style={{ flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 12 }}>
        <Text style={c.k}>Last {series.length} {gamesWord}</Text>
        <Text style={c.sub}>
          <Text style={{ color: hits / series.length >= 0.5 ? T.green : T.red, fontFamily: F.bodySemi }}>{hits}/{series.length}</Text>
          {' '}{lean === 'UNDER' ? 'under' : 'over'} {fmtLine(line)}
        </Text>
      </View>
      <View style={{ height: H, flexDirection: 'row', alignItems: 'flex-end', gap: 5 }}>
        {/* average — dotted, behind the bars */}
        <View pointerEvents="none" style={[c.avgRule, { bottom: at(avg) }]} />
        {series.map((g, i) => {
          const cleared = lean === 'UNDER' ? g.value < line : g.value > line
          const h = Math.max(6, at(g.value))
          const tone = cleared ? T.green : T.red
          return (
            <View key={i} style={{ flex: 1, alignItems: 'center', justifyContent: 'flex-end' }}>
              <Text style={[c.barVal, { color: tone }]}>{Number.isInteger(g.value) ? g.value : fmt(g.value)}</Text>
              <LinearGradient colors={[`${tone}F0`, `${tone}40`]} start={{ x: 0.5, y: 0 }} end={{ x: 0.5, y: 1 }}
                              style={{ width: '100%', height: h, borderTopLeftRadius: 6, borderTopRightRadius: 6,
                                       borderBottomLeftRadius: 2, borderBottomRightRadius: 2 }} />
            </View>
          )
        })}
        {/* the book line — dashed, on top, with its value in a pill */}
        <View pointerEvents="none" style={[c.lineRule, { bottom: at(line) }]} />
        <View pointerEvents="none" style={[c.linePill, { bottom: at(line) - 9 }]}>
          <Text style={c.linePillText}>{fmtLine(line)}</Text>
        </View>
      </View>
      <View style={{ flexDirection: 'row', gap: 5, marginTop: 6 }}>
        {series.map((g, i) => (
          <View key={i} style={{ flex: 1, alignItems: 'center' }}>
            <Text style={c.axis} numberOfLines={1}>{dateLabel(g.date)}</Text>
            <Text style={[c.axis, { color: '#555' }]} numberOfLines={1}>
              {(g.opponent || '').split(' ').slice(-1)[0].slice(0, 6)}
            </Text>
          </View>
        ))}
      </View>
      <View style={{ flexDirection: 'row', gap: 14, marginTop: 10 }}>
        <View style={c.legend}><View style={[c.legendDash, { borderColor: 'rgba(255,255,255,0.7)' }]} /><Text style={c.sub}>Book line</Text></View>
        <View style={c.legend}><View style={[c.legendDash, { borderColor: T.blue, borderStyle: 'dotted' }]} /><Text style={c.sub}>Average {fmt(avg)}</Text></View>
      </View>
    </View>
  )
}

// ── SERVE & RETURN ──────────────────────────────────────────────────────────
// The same rows the Discord card prints (bot.py::_prop_stats), same precision.
const pct = (v: unknown) => (typeof v === 'number' ? `${v.toFixed(0)}%` : '—')
const num = (v: unknown) => (typeof v === 'number' ? v.toFixed(1) : '—')
const tb = (r: unknown) => (typeof r !== 'number' ? '—' : `${r.toFixed(0)}%${r > 35 ? ' · specialist' : ''}`)
type Row = [string, string]

export function statRows(prop: string, res: any, surface: string): [Row[], Row[]] {
  const ps = res?.player_stats || {}
  const os = res?.opponent_stats || {}
  const sfx = surface && surface !== 'All' ? ` (${surface})` : ''
  let p: Row[], o: Row[]
  switch (prop) {
    case 'Aces':
      p = [[`Aces per match${sfx}`, num(ps.aces)], ['1st serve in', pct(ps.first_serve_pct)],
           ['1st serve points won', pct(ps.first_serve_pts_won)]]
      o = [[`Aces allowed per match${sfx}`, num(res.opponent_ace_against)],
           ['Return pts won vs 1st', pct(os.return_first_serve_pts_won)],
           [`Own aces per match${sfx}`, num(os.aces)]]
      break
    case 'Double Faults':
      p = [['Double faults per match', num(ps.double_faults)], ['2nd serve points won', pct(ps.second_serve_pts_won)],
           ['1st serve in', pct(ps.first_serve_pct)]]
      o = [['Return pts won vs 2nd', pct(os.return_second_serve_pts_won)],
           ['Double faults per match', num(os.double_faults)]]
      break
    case 'Break Points Won':
      p = [['Break points created per match', num(res.bp_generated_per_match)],
           ['Created, quality-adjusted', num(res.bp_generated_quality_adj)],
           ['Break point conversion', pct(res.bp_blended_conv_pct ?? ps.bp_converted)],
           ['Service games held', pct(ps.service_games_won_pct)],
           ['Return games won', pct(ps.return_games_won_pct)]]
      o = [['Break points faced per match', num(res.bp_blended_opp_faced)],
           ['Service games held', pct(os.service_games_won_pct)],
           ['Hold rate', pct(res.opp_hold_rate_pct)],
           ['Server quality', String(res.opp_server_quality_tier || res.opp_serve_tier || '—')],
           ['1st serve points won', pct(os.first_serve_pts_won)],
           ['2nd serve points won', pct(os.second_serve_pts_won)]]
      break
    case 'Break Points Saved':
      p = [['Service games held', pct(ps.service_games_won_pct)],
           ['Hold vs this opponent', pct(res.bps_effective_hold)],
           ['Break points saved', pct(res.bps_save_rate ?? ps.bp_saved)],
           ['Break points faced per match', num(ps.bp_faced_count)],
           ['Projected break points faced', num(res.bps_faced_proj)]]
      o = [['Return games won', pct(os.return_games_won_pct)],
           ['Break points created per match', num(os.return_bp_opportunities)],
           ['Break point conversion', pct(os.bp_converted)],
           ['Service games held', pct(os.service_games_won_pct)]]
      break
    case 'Player Total Games Won':
      p = [['Hold rate', pct(res.player_hold_rate)], ['Break rate vs opponent', pct(res.player_break_rate)]]
      o = [['Hold rate', pct(res.opp_hold_rate_g)], ['Win rate', pct(os.win_rate)]]
      break
    case 'Fantasy Score':
      p = [['Projected aces', num(res.fs_ace_proj)], ['Projected double faults', num(res.fs_df_proj)],
           [`Aces per match${sfx}`, num(ps.aces)], ['Double faults per match', num(ps.double_faults)],
           ['1st serve in', pct(ps.first_serve_pct)], ['1st serve points won', pct(ps.first_serve_pts_won)],
           ['2nd serve points won', pct(ps.second_serve_pts_won)], ['Win rate', pct(ps.win_rate)]]
      o = [[`Aces allowed per match${sfx}`, num(res.opponent_ace_against)],
           ['Double faults per match', num(os.double_faults)],
           ['Return pts won vs 1st', pct(os.return_first_serve_pts_won)],
           ['Return pts won vs 2nd', pct(os.return_second_serve_pts_won)],
           ['1st serve points won', pct(os.first_serve_pts_won)],
           ['2nd serve points won', pct(os.second_serve_pts_won)], ['Win rate', pct(os.win_rate)]]
      break
    default:   // Total Games
      p = [['1st serve points won', pct(ps.first_serve_pts_won)],
           ['2nd serve points won', pct(ps.second_serve_pts_won)], ['Win rate', pct(ps.win_rate)]]
      o = [['1st serve points won', pct(os.first_serve_pts_won)],
           ['2nd serve points won', pct(os.second_serve_pts_won)], ['Win rate', pct(os.win_rate)]]
  }
  if (prop !== 'Break Points Saved') {
    if (res?.player_tiebreak_rate != null) p.push(['Tiebreak rate', tb(res.player_tiebreak_rate)])
    if (res?.opponent_tiebreak_rate != null) o.push(['Tiebreak rate', tb(res.opponent_tiebreak_rate)])
  }
  return [p, o]
}

const hand = (h: unknown) => {
  const u = String(h || '').toUpperCase()
  return u.startsWith('L') ? 'Left-handed' : u.startsWith('R') ? 'Right-handed' : null
}

function StatColumn({ name, rows, arch, hd, serve }:
  { name: string; rows: Row[]; arch?: string; hd?: string | null; serve?: string }) {
  return (
    <View style={{ flex: 1, minWidth: 0 }}>
      <Text style={c.colName} numberOfLines={1}>{name}</Text>
      {rows.map(([k, v]) => (
        <View key={k} style={c.statRow}>
          <Text style={c.statK} numberOfLines={2}>{k}</Text>
          <Text style={c.statV}>{v}</Text>
        </View>
      ))}
      {serve ? <Text style={c.note}>{serve}</Text> : null}
      {arch ? <Text style={[c.note, { fontStyle: 'italic' }]}>{arch}</Text> : null}
      {hd ? <Text style={[c.note, { color: T.muted2 }]}>{hd}</Text> : null}
    </View>
  )
}

export function StatBlock({ prop, res, surface, playerName, opponentName }:
  { prop: string; res: any; surface: string; playerName: string; opponentName: string }) {
  const [pRows, oRows] = statRows(prop, res, surface)
  return (
    <View style={{ flexDirection: 'row', gap: 14 }}>
      <StatColumn name={playerName} rows={pRows} arch={res?.player_archetype}
                  hd={hand(res?.player_handedness)} serve={res?.player_serve_profile} />
      <StatColumn name={opponentName} rows={oRows} arch={res?.opponent_archetype}
                  hd={hand(res?.opponent_handedness)} serve={res?.opponent_serve_profile} />
    </View>
  )
}

// A row of labelled figures, optionally with a small bar under each.
export function Figures({ cells }: { cells: ([string, string, string?, (number | null)?] | null)[] }) {
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: T.s2 }}>
      {cells.filter(Boolean).map(cell => {
        const [k, v, tone, bar] = cell!
        return (
          <View key={k} style={{ minWidth: '45%', flex: 1 }}>
            <Text style={c.k}>{k}</Text>
            <Text style={[c.big, { color: tone || T.white, fontSize: 19 }]}>{v}</Text>
            {typeof bar === 'number' ? <View style={{ marginTop: 5 }}><Meter pct={bar} tone={tone || T.white} height={3} /></View> : null}
          </View>
        )
      })}
    </View>
  )
}

export function Divider({ children }: { children?: ReactNode }) {
  return <View style={c.divider}>{children}</View>
}

const c = StyleSheet.create({
  k: { fontFamily: F.condBold, fontSize: 10, letterSpacing: 1.1, textTransform: 'uppercase', color: T.muted2 },
  v: { fontFamily: F.condHeavy, fontSize: 17, color: T.white, marginTop: 1 },
  big: { fontFamily: F.condHeavy, fontSize: 21, lineHeight: 25, marginTop: 2 },
  sub: { fontFamily: F.body, fontSize: 10.5, color: T.muted2, marginTop: 1 },
  track: { position: 'absolute', left: 0, right: 0, height: 4, borderRadius: 2,
           backgroundColor: 'rgba(255,255,255,0.08)' },
  span: { position: 'absolute', height: 4, borderRadius: 2, opacity: 0.85 },
  dot: { position: 'absolute', width: 9, height: 9, borderRadius: 5, marginLeft: -4.5,
         borderWidth: 2, borderColor: T.ground },
  dotBig: { width: 13, height: 13, borderRadius: 7, marginLeft: -6.5 },
  meter: { backgroundColor: 'rgba(255,255,255,0.08)', overflow: 'hidden' },
  cell: { flex: 1, paddingVertical: 12, paddingHorizontal: 6, borderRadius: T.r2, borderWidth: 1,
          borderColor: T.glassLine, backgroundColor: 'rgba(255,255,255,0.025)', alignItems: 'center' },
  ringNum: { fontFamily: F.condHeavy, fontSize: 19 },
  ringPct: { fontFamily: F.condBold, fontSize: 11, color: T.muted },
  lineRule: { position: 'absolute', left: 0, right: 0, borderTopWidth: 1.5, borderStyle: 'dashed',
              borderColor: 'rgba(255,255,255,0.7)', zIndex: 2 },
  avgRule: { position: 'absolute', left: 0, right: 0, borderTopWidth: 1.5, borderStyle: 'dotted',
             borderColor: `${T.blue}AA`, zIndex: 1 },
  linePill: { position: 'absolute', right: -2, zIndex: 3, paddingHorizontal: 6, paddingVertical: 2,
              borderRadius: 6, backgroundColor: '#f2f2f2' },
  linePillText: { fontFamily: F.condHeavy, fontSize: 11, color: '#0a0a0a' },
  legend: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  legendDash: { width: 16, borderTopWidth: 1.5, borderStyle: 'dashed' },
  barVal: { fontFamily: F.condBold, fontSize: 10.5, marginBottom: 3 },
  axis: { fontFamily: F.body, fontSize: 8.5, color: T.muted2 },
  colName: { fontFamily: F.condBold, fontSize: 13, color: T.white, marginBottom: 8 },
  statRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8,
             marginBottom: 6 },
  statK: { fontFamily: F.body, fontSize: 11.5, color: T.muted2, flex: 1, lineHeight: 15 },
  statV: { fontFamily: F.bodySemi, fontSize: 12, color: T.white, textAlign: 'right' },
  note: { fontFamily: F.body, fontSize: 11, color: T.muted, marginTop: 4 },
  divider: { height: StyleSheet.hairlineWidth, backgroundColor: T.glassLine, marginVertical: T.s3 },
})
