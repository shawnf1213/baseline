// A player, in a sheet: who they are, what's on the board for them, recent
// form, prop history and (tennis) surface splits. Three sport branches on one
// frame, so the reader learns the layout once.
//
// Everything here comes from endpoints the website already reads
// (/api/player/{stats,form,next-match}, /api/history, /api/nfl/players,
// /api/nba/player). The heavy tennis stats call (20+ s cold) is loaded last
// and shown with its own placeholder so the rest of the sheet is usable at
// once.
import { useEffect, useMemo, useRef, useState } from 'react'
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native'
import { router } from 'expo-router'
import { Sheet } from './Sheet'
import { Button, Card, Disclaimer, Muted, Pill } from './ui'
import { Skeleton } from './Skeleton'
import { PhotoCredit, PlayerAvatar } from './Avatar'
import { CardGlow } from './Glow'
import { Divider, Figures, GameChart, HitWindows, Meter } from './charts'
import { fetchForm, fetchHistory, fetchNbaPlayer, fetchNextMatch, fetchNflPlayer, fetchStats } from '@/lib/api'
import { PlayerLine, boardLinesFor } from '@/lib/board'
import { Found, Hist, NBA_PROPS, NFL_PROPS, PROP_TYPES, fmt, fmtLine, nbaHistory, nflHistory, prettyDate,
         propLabel, resolvePlayer, shapeHistory, startTimeLabel } from '@/lib/picks'
import { SportKey, useSport } from '@/lib/sports'
import { useRecent, useSaved, playerKey } from '@/lib/saved'
import { F, T, sideTone } from '@/theme'
import { tap } from '@/lib/haptics'

export type PlayerRef = { sport: SportKey; name: string; id?: string | null; tour?: string;
                          team?: string | null; rank?: number | null; position?: string | null }

export function PlayerSheet({ player, onClose }: { player: PlayerRef | null; onClose: () => void }) {
  const { has, toggle } = useSaved()
  const { push } = useRecent()
  useEffect(() => { if (player) push({ sport: player.sport, name: player.name, id: player.id, tour: player.tour, team: player.team, rank: player.rank }) }, [player?.sport, player?.name])  // eslint-disable-line react-hooks/exhaustive-deps
  if (!player) return <Sheet open={false} onClose={onClose}><View /></Sheet>
  const key = playerKey(player.sport, player.name)
  const saved = has(key)
  const heart = (
    <Pressable onPress={() => { tap(); toggle({ sport: player.sport, name: player.name, id: player.id, tour: player.tour, team: player.team, rank: player.rank }) }}
               hitSlop={8} style={[s.heart, saved && s.heartOn]} accessibilityLabel={saved ? 'Remove from saved' : 'Save player'}>
      <Text style={[s.heartText, saved && { color: T.green }]}>{saved ? '♥ Saved' : '♡ Save'}</Text>
    </Pressable>
  )
  return (
    <Sheet open={!!player} onClose={onClose}
           title={<View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                    <Text style={s.title} numberOfLines={1}>{player.name}</Text>{heart}
                  </View>}>
      {player.sport === 'tennis' ? <TennisPlayer p={player} onClose={onClose} />
        : player.sport === 'nfl' ? <NflPlayer p={player} onClose={onClose} />
        : <NbaPlayer p={player} onClose={onClose} />}
      <Disclaimer />
    </Sheet>
  )
}

// ── shared bits ─────────────────────────────────────────────────────────────
function Header({ name, badges, sport, team }:
  { name: string; badges: (string | null | undefined)[]; sport: SportKey; team?: string | null }) {
  return (
    <View style={s.header}>
      <CardGlow color={T.green} strength={0.2} />
      <PlayerAvatar sport={sport} name={name} size={84} ring={T.green} team={team} />
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={s.name} numberOfLines={2}>{name}</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
          {badges.filter(Boolean).map(b => <Pill key={b!} tone={T.muted}>{b}</Pill>)}
        </View>
        <PhotoCredit sport={sport} names={[name]} style={{ justifyContent: 'flex-start', marginTop: 10 }} />
      </View>
    </View>
  )
}

function K({ children, right }: { children: string; right?: string }) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', marginTop: 20, marginBottom: 8 }}>
      <Text style={s.k}>{children}</Text>
      {right ? <Text style={s.kRight}>{right}</Text> : null}
    </View>
  )
}

// One prop's history card: the line on the board (or the player's average),
// how often our side landed, the game chart, and a jump to Project.
function PropHistory({ sport, name, propKey, hist, line, lean, gamesWord, surface, loading, onProject }:
  { sport: SportKey; name: string; propKey: string; hist: Hist | null; line: PlayerLine | null;
    lean: string; gamesWord: string; surface?: string; loading?: boolean; onProject: () => void }) {
  const label = propLabel(sport, propKey)
  if (loading) return <Card style={{ marginBottom: 10 }}><Skeleton w="40%" h={14} /><Skeleton h={90} r={10} style={{ marginTop: 10 }} /></Card>
  if (!hist || !hist.games.length) {
    return (
      <Card style={{ marginBottom: 10 }}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
          <Text style={s.propTitle}>{label}</Text>
          <Muted size={12}>no {gamesWord} logged{surface ? ` on ${surface.toLowerCase()}` : ''}</Muted>
        </View>
      </Card>
    )
  }
  const ref = line?.line ?? hist.average
  const haveSide = !!line && (lean === 'OVER' || lean === 'UNDER')
  const side = sideTone(haveSide ? lean : '')
  return (
    <Card style={{ marginBottom: 10 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Text style={[s.propTitle, { flex: 1 }]}>{label}</Text>
        {line ? <Pill tone={side.tone}>{haveSide ? `${lean} ${fmtLine(line.line)}` : `line ${fmtLine(line.line)}`}</Pill>
              : <Muted size={12}>avg {fmt(hist.average)}</Muted>}
      </View>
      {ref != null ? (
        <View style={{ marginTop: 10 }}>
          <HitWindows hist={hist} lean={haveSide ? lean : 'OVER'} line={ref} gamesWord={gamesWord} season={!!line} />
          <Muted size={11} style={{ marginTop: 6, marginBottom: 12 }}>
            {haveSide ? `How often ${name.split(' ').slice(-1)[0]} finished ${lean.toLowerCase()} ${fmtLine(ref)}`
                      : `How often ${name.split(' ').slice(-1)[0]} went over ${line ? 'the line' : 'their average'} of ${fmt(ref)}`}
            {surface ? ` on ${surface.toLowerCase()}` : ''} · {hist.sample} {gamesWord}
          </Muted>
          <GameChart hist={hist} line={ref} lean={haveSide ? lean : 'OVER'} gamesWord={gamesWord} />
        </View>
      ) : null}
      {line ? <Button label="Project this line" kind="quiet" onPress={onProject} style={{ marginTop: 6, alignSelf: 'flex-start', paddingHorizontal: 0 }} /> : null}
    </Card>
  )
}

const lineFor = (lines: PlayerLine[], prop: string) => lines.find(l => l.propType === prop) || null

// ── TENNIS ──────────────────────────────────────────────────────────────────
function TennisPlayer({ p, onClose }: { p: PlayerRef; onClose: () => void }) {
  const { setSport } = useSport()
  const [who, setWho] = useState<Found | null>(p.id ? { id: p.id, name: p.name, tour: p.tour === 'WTA' ? 'WTA' : 'ATP', currentRank: p.rank ?? null } : null)
  const [notFound, setNotFound] = useState(false)
  const [form, setForm] = useState<any>(null)
  const [next, setNext] = useState<any>(null)
  const [stats, setStats] = useState<any>(null)
  const [statsState, setStatsState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [lines, setLines] = useState<PlayerLine[]>([])
  const [hists, setHists] = useState<Record<string, Hist | null | 'loading'>>({})
  const alive = useRef(0)

  useEffect(() => {
    const token = ++alive.current
    ;(async () => {
      const w = who || await resolvePlayer(p.name, p.tour || 'ATP')
      if (alive.current !== token) return
      if (!w) { setNotFound(true); return }
      setWho(w)
      const [f, n, ls] = await Promise.all([
        fetchForm(w.id, w.tour).catch(() => null),
        fetchNextMatch(w.id, w.tour).catch(() => null),
        boardLinesFor('tennis', p.name).catch(() => []),
      ])
      if (alive.current !== token) return
      setForm(f); setNext(n && n.opponent_name ? n : null); setLines(ls)
      const surface = (n?.surface) || ls[0]?.row.surface || 'Hard'
      const histProps = PROP_TYPES.filter(t => t.history)
      setHists(Object.fromEntries(histProps.map(t => [t.key, 'loading'])))
      histProps.forEach(t => {
        const l = lineFor(ls, t.key)
        fetchHistory(w.id, w.tour, t.key, surface, l?.line ?? 0)
          .then(h => { if (alive.current === token) setHists(m => ({ ...m, [t.key]: shapeHistory(h) })) })
          .catch(() => { if (alive.current === token) setHists(m => ({ ...m, [t.key]: null })) })
      })
      fetchStats(w.id, w.tour, p.name)
        .then(st => { if (alive.current === token) { setStats(st); setStatsState('ready') } })
        .catch(() => { if (alive.current === token) setStatsState('error') })
    })()
  }, [p.name])  // eslint-disable-line react-hooks/exhaustive-deps

  const surface = next?.surface || lines[0]?.row.surface || mostPlayed(stats) || 'Hard'
  const hand = stats?.ta_stats?.handedness
  const openProject = (l: PlayerLine) => {
    tap(); onClose(); setSport('tennis')
    router.push({ pathname: '/project', params: {
      sport: 'tennis', player: p.name, opponent: l.row.opponent, playerId: who?.id || '', opponentId: l.row.opponentId || '',
      tour: who?.tour || 'ATP', surface: l.row.surface || surface, court: l.row.tournament || '',
      prop: l.propType, line: String(l.line), t: String(Date.now()) } })
  }

  if (notFound) return <Card style={{ marginTop: 10 }}><Muted size={13}>Couldn't find this player in the data source.</Muted></Card>
  return (
    <View>
      <Header sport="tennis" name={p.name} badges={[who?.tour, who?.currentRank ? `#${who.currentRank}` : null,
        hand === 'L' ? 'Left-handed' : hand === 'R' ? 'Right-handed' : null, stats?.archetype || null]} />

      {next ? (
        <Card style={{ marginTop: 8 }}>
          <Text style={s.k}>Next match</Text>
          <Text style={s.big}>vs {next.opponent_name}</Text>
          <Muted size={12.5}>{[next.tournament, next.surface, startTimeLabel(next.start_timestamp)].filter(Boolean).join(' · ')}</Muted>
        </Card>
      ) : null}

      {lines.length ? (
        <>
          <K right={`${lines.length} line${lines.length === 1 ? '' : 's'}`}>On the board</K>
          {lines.map(l => {
            const side = sideTone(l.lean)
            const priced = l.row.projection != null
            return (
              <Card key={`${l.book}|${l.propType}|${l.line}`} onPress={() => openProject(l)} style={s.lineRow}>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={s.propTitle}>{propLabel('tennis', l.propType)}</Text>
                  <Muted size={12}>vs {l.row.opponent} · {l.book === 'underdog' ? 'Underdog' : 'PrizePicks'}</Muted>
                </View>
                <View style={{ alignItems: 'flex-end' }}>
                  <Text style={[s.call, { color: priced ? side.tone : T.muted }]}>{priced ? `${l.lean || '—'} ${fmtLine(l.line)}` : `line ${fmtLine(l.line)}`}</Text>
                  <Muted size={11}>{priced ? `Baseline ${fmt(l.row.projection)} · conf ${l.row.confidence != null ? Math.round(l.row.confidence) : '—'}` : 'not priced yet'}</Muted>
                </View>
              </Card>
            )
          })}
        </>
      ) : null}

      <K right={form?.streak_len ? `${form.streak_type}${form.streak_len} streak` : undefined}>Recent form</K>
      {form == null ? <Card><Skeleton h={54} r={10} /></Card> : !Array.isArray(form.last10) || !form.last10.length ? (
        <Card><Muted size={12.5}>No recent matches.</Muted></Card>
      ) : (
        <Card>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              {form.last10.map((m: any, i: number) => (
                <View key={i} style={s.formCell}>
                  <View style={[s.wl, { backgroundColor: m.won ? `${T.green}26` : `${T.red}26` }]}>
                    <Text style={[s.wlText, { color: m.won ? T.green : T.red }]}>{m.won ? 'W' : 'L'}</Text>
                  </View>
                  <Text style={s.formOpp} numberOfLines={1}>{(m.opponent || '—').split(' ').slice(-1)[0]}</Text>
                  <Text style={s.formSub}>{prettyDate(m.date) || m.surface || ''}</Text>
                </View>
              ))}
            </View>
          </ScrollView>
          {form.trend && typeof form.trend === 'object' ? (
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 12 }}>
              {Object.entries(form.trend as Record<string, any>).slice(0, 4).map(([k, v]) => (
                <Pill key={k} tone={v?.direction === 'up' ? T.green : v?.direction === 'down' ? T.red : T.muted}>
                  {k.replace(/_/g, ' ')} {v?.recent5 != null ? fmt(v.recent5) : ''} {v?.direction === 'up' ? '↑' : v?.direction === 'down' ? '↓' : '→'}
                </Pill>
              ))}
            </View>
          ) : null}
          {form.freshness?.message && form.freshness?.level === 'red' ? (
            <Muted size={11.5} style={{ marginTop: 10, color: T.amber }}>{form.freshness.message}</Muted>
          ) : null}
        </Card>
      )}

      <K right={`${surface} · last 10`}>Prop history</K>
      {PROP_TYPES.filter(t => t.history).map(t => {
        const h = hists[t.key]
        const l = lineFor(lines, t.key)
        return <PropHistory key={t.key} sport="tennis" name={p.name} propKey={t.key} hist={h === 'loading' ? null : (h ?? null)}
                            loading={h === 'loading' || h === undefined} line={l} lean={l?.lean || ''} gamesWord="matches" surface={surface}
                            onProject={() => l && openProject(l)} />
      })}

      <K>Surface splits</K>
      {statsState === 'loading' ? (
        <Card><Skeleton h={80} r={10} /><Muted size={11} style={{ marginTop: 8 }}>Pulling the full match log — up to half a minute on a player we haven't seen today.</Muted></Card>
      ) : statsState === 'error' || !stats ? (
        <Card><Muted size={12.5}>Surface stats aren't available right now.</Muted></Card>
      ) : <SurfaceSplits stats={stats} />}
    </View>
  )
}

function mostPlayed(stats: any) {
  if (!stats) return null
  let best: string | null = null, n = -1
  for (const sf of ['Hard', 'Clay', 'Grass']) {
    const m = stats[sf]?.matches_played || 0
    if (m > n) { n = m; best = sf }
  }
  return n > 0 ? best : null
}

const SURF_TONE: Record<string, string> = { Hard: T.blue, Clay: '#E07A3A', Grass: T.green }
const SURF_STATS: [string, string, number][] = [
  ['aces', 'Aces per match', 1], ['double_faults', 'Double faults per match', 1],
  ['bp_generated_per_match', 'Break points created per match', 1],
  ['first_serve_pct', '1st serve in', 0], ['service_games_won_pct', 'Service games held', 0],
  ['return_games_won_pct', 'Return games won', 0],
]
function SurfaceSplits({ stats }: { stats: any }) {
  const surfaces = ['Hard', 'Clay', 'Grass'].filter(sf => (stats?.[sf]?.matches_played || 0) > 0)
  if (!surfaces.length) return <Card><Muted size={12.5}>No surface stats yet.</Muted></Card>
  const peak: Record<string, number> = {}
  for (const [k] of SURF_STATS) peak[k] = Math.max(...surfaces.map(sf => stats[sf]?.[k] || 0), 0.0001)
  return (
    <View>
      {surfaces.map(sf => {
        const d = stats[sf] || {}
        const c = SURF_TONE[sf] || T.green
        const wr = typeof d.win_rate === 'number' ? d.win_rate : null
        return (
          <Card key={sf} style={{ marginBottom: 10, borderColor: `${c}44` }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <View style={{ width: 9, height: 9, borderRadius: 5, backgroundColor: c }} />
              <Text style={[s.propTitle, { flex: 1 }]}>{sf}</Text>
              <Muted size={11.5}>{d.matches_played} match{d.matches_played === 1 ? '' : 'es'}</Muted>
            </View>
            {wr != null ? (
              <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 7, marginTop: 8 }}>
                <Text style={[s.bigNum, { color: wr >= 50 ? T.green : T.red }]}>{fmt(wr, 0)}%</Text>
                <Text style={s.k}>win rate</Text>
              </View>
            ) : null}
            {SURF_STATS.map(([k, label, dp]) => {
              const v = d[k]
              const isPct = label.includes('in') || label.includes('held') || label.includes('won')
              return (
                <View key={k} style={{ marginTop: 8 }}>
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 3 }}>
                    <Muted size={11.5}>{label}</Muted>
                    <Text style={s.statV}>{v != null ? `${fmt(v, dp)}${isPct ? '%' : ''}` : '—'}</Text>
                  </View>
                  <Meter pct={isPct ? (v || 0) : Math.min(100, ((v || 0) / peak[k]) * 100)} tone={c} height={4} />
                </View>
              )
            })}
          </Card>
        )
      })}
    </View>
  )
}

// ── NFL ─────────────────────────────────────────────────────────────────────
function NflPlayer({ p, onClose }: { p: PlayerRef; onClose: () => void }) {
  const { setSport } = useSport()
  const [prof, setProf] = useState<any>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'none' | 'error'>('loading')
  const [lines, setLines] = useState<PlayerLine[]>([])
  useEffect(() => {
    let on = true
    Promise.all([fetchNflPlayer(p.name).catch(() => null), boardLinesFor('nfl', p.name).catch(() => [])])
      .then(([d, ls]) => {
        if (!on) return
        const pr = d?.players?.[0]
        setLines(ls)
        if (!pr) { setState(d ? 'none' : 'error'); return }
        setProf(pr); setState('ready')
      })
    return () => { on = false }
  }, [p.name])
  const profile = prof?.profile || {}
  const openProject = (prop: string, line?: number) => {
    tap(); onClose(); setSport('nfl')
    router.push({ pathname: '/project', params: { sport: 'nfl', player: p.name, team: prof?.team || p.team || '',
      prop, line: line != null ? String(line) : '', t: String(Date.now()) } })
  }
  const pct = (v: unknown) => (typeof v === 'number' ? `${Math.round(v * 100)}%` : '—')
  const n1 = (v: unknown) => (typeof v === 'number' ? v.toFixed(1) : '—')
  return (
    <View>
      <Header sport="nfl" team={prof?.team || p.team} name={p.name} badges={[prof?.position || p.position, prof?.team || p.team,
        profile.depth_pos && profile.depth_rank ? `${profile.depth_pos}${profile.depth_rank} on the depth chart` : null]} />
      {state === 'loading' ? <Card style={{ marginTop: 8 }}><Skeleton h={70} r={10} /></Card>
        : state === 'none' ? <Card style={{ marginTop: 8 }}><Muted size={12.5}>No published profile for this player yet — profiles are published with each slate's board.</Muted></Card>
        : state === 'error' ? <Card style={{ marginTop: 8 }}><Muted size={12.5}>Couldn't load this player right now.</Muted></Card>
        : (
          <>
            <Card style={{ marginTop: 8 }}>
              <Text style={s.k}>Role · {profile.window || 'this season'}</Text>
              <View style={{ marginTop: 8 }}>
                <Figures cells={[
                  ['Target share', pct(profile.target_share), T.white, typeof profile.target_share === 'number' ? profile.target_share * 100 : null],
                  ['Targets per game', n1(profile.targets_per_game), T.white, null],
                  ['Carries per game', n1(profile.carries_per_game), T.white, null],
                  ['Catch rate', pct(profile.catch_rate), T.white, typeof profile.catch_rate === 'number' ? profile.catch_rate * 100 : null],
                  ['Yards per target', n1(profile.yards_per_target), T.white, null],
                  ['Snap share', pct(profile.snap_season), T.white, typeof profile.snap_season === 'number' ? Math.min(100, profile.snap_season * 100) : null],
                ]} />
              </View>
              {profile.matchup?.opponent ? (
                <>
                  <Divider />
                  <Text style={s.k}>This week · vs {profile.matchup.opponent}</Text>
                  {Object.entries(profile.matchup.by_prop || {}).map(([prop, m]: [string, any]) => (
                    <View key={prop} style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 6 }}>
                      <Muted size={12}>{propLabel('nfl', prop)}</Muted>
                      <Text style={s.statV}>{m?.rank ? `${m.rank} of ${m.of}` : '—'}{typeof m?.factor === 'number' ? ` · ×${m.factor}` : ''}</Text>
                    </View>
                  ))}
                  <Muted size={11} style={{ marginTop: 8 }}>Rank 1 allows the least. The factor is what the model applies to the rate.</Muted>
                </>
              ) : null}
            </Card>
            <K right={`${(prof?.form?.games || []).length} games`}>Game log by prop</K>
            {NFL_PROPS.map(t => {
              const l = lineFor(lines, t.key)
              const hist = nflHistory(prof, t.key, l?.line ?? null)
              return <PropHistory key={t.key} sport="nfl" name={p.name} propKey={t.key} hist={hist} line={l} lean={l?.lean || ''}
                                  gamesWord="games" onProject={() => openProject(t.key, l?.line)} />
            })}
          </>
        )}
    </View>
  )
}

// ── NBA ─────────────────────────────────────────────────────────────────────
function NbaPlayer({ p, onClose }: { p: PlayerRef; onClose: () => void }) {
  const { setSport } = useSport()
  const [data, setData] = useState<any>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'none' | 'error'>('loading')
  const [lines, setLines] = useState<PlayerLine[]>([])
  useEffect(() => {
    let on = true
    Promise.all([fetchNbaPlayer(p.name, 20).catch(() => null), boardLinesFor('nba', p.name).catch(() => [])])
      .then(([d, ls]) => {
        if (!on) return
        setLines(ls)
        if (!d) { setState('error'); return }
        if (!Array.isArray(d.games) || !d.games.length) { setState('none'); return }
        setData(d); setState('ready')
      })
    return () => { on = false }
  }, [p.name])
  const games: any[] = data?.games || []
  const usage = data?.usage || {}
  const avg = (k: string) => { const v = games.map(g => g?.[k]).filter((x: any) => typeof x === 'number'); return v.length ? v.reduce((a: number, b: number) => a + b, 0) / v.length : null }
  const openProject = (prop: string, line?: number) => {
    tap(); onClose(); setSport('nba')
    router.push({ pathname: '/project', params: { sport: 'nba', player: p.name, team: p.team || '',
      prop, line: line != null ? String(line) : '', t: String(Date.now()) } })
  }
  const teamFromLog = games[0]?.matchup ? String(games[0].matchup).split(/\s+/)[0] : null
  return (
    <View>
      <Header sport="nba" team={p.team || teamFromLog} name={p.name} badges={[p.team || teamFromLog, usage?.position || null,
        typeof usage?.rotation === 'string' ? `rotation ${usage.rotation}` : null]} />
      {state === 'loading' ? <Card style={{ marginTop: 8 }}><Skeleton h={70} r={10} /></Card>
        : state === 'none' ? <Card style={{ marginTop: 8 }}><Muted size={12.5}>No game log for this player yet.</Muted></Card>
        : state === 'error' ? <Card style={{ marginTop: 8 }}><Muted size={12.5}>Couldn't load this player right now.</Muted></Card>
        : (
          <>
            <Card style={{ marginTop: 8 }}>
              <Text style={s.k}>Last {games.length} games{usage?.window ? ` · ${usage.window}` : ''}</Text>
              <View style={{ marginTop: 8 }}>
                <Figures cells={[
                  ['Minutes', fmt(typeof usage?.minutes === 'number' ? usage.minutes : avg('min'), 0), T.white, null],
                  ['Points', fmt(avg('pts')), T.white, null],
                  ['Rebounds', fmt(avg('reb')), T.white, null],
                  ['Assists', fmt(avg('ast')), T.white, null],
                  ['3-pointers', fmt(avg('fg3m')), T.white, null],
                  ['Fantasy', fmt(avg('nba_fantasy_pts')), T.white, null],
                ]} />
              </View>
            </Card>
            <K>Game log by prop</K>
            {NBA_PROPS.filter(t => ['pts', 'reb', 'ast', 'fg3m', 'pra', 'nba_fantasy_pts'].includes(t.key) || lineFor(lines, t.key)).map(t => {
              const l = lineFor(lines, t.key)
              const hist = nbaHistory(data, t.key, l?.line ?? null)
              return <PropHistory key={t.key} sport="nba" name={p.name} propKey={t.key} hist={hist} line={l} lean={l?.lean || ''}
                                  gamesWord="games" onProject={() => openProject(t.key, l?.line)} />
            })}
          </>
        )}
    </View>
  )
}

const s = StyleSheet.create({
  title: { fontFamily: F.condBlack, fontSize: 19, color: T.white, flexShrink: 1 },
  heart: { minHeight: 32, paddingHorizontal: 10, borderRadius: 999, borderWidth: 1, borderColor: T.glassLineHi,
           justifyContent: 'center' },
  heartOn: { borderColor: `${T.green}66`, backgroundColor: `${T.green}1C` },
  heartText: { fontFamily: F.condBold, fontSize: 12, color: T.muted, letterSpacing: 0.6 },
  header: { flexDirection: 'row', alignItems: 'center', gap: 14, marginTop: 4, marginBottom: 6, padding: 14,
            borderRadius: T.r3, overflow: 'hidden', borderWidth: StyleSheet.hairlineWidth, borderColor: T.glassLine,
            backgroundColor: 'rgba(255,255,255,0.02)' },
  name: { fontFamily: F.condHeavy, fontSize: 26, lineHeight: 29, color: T.white },
  k: { fontFamily: F.condBold, fontSize: 10.5, letterSpacing: 1.3, textTransform: 'uppercase', color: T.muted2 },
  kRight: { fontFamily: F.bodyMed, fontSize: 11.5, color: T.muted2 },
  big: { fontFamily: F.condBlack, fontSize: 18, color: T.white, marginTop: 4 },
  bigNum: { fontFamily: F.condHeavy, fontSize: 26, lineHeight: 28 },
  propTitle: { fontFamily: F.condBlack, fontSize: 16, color: T.white, letterSpacing: 0.3 },
  statV: { fontFamily: F.bodySemi, fontSize: 12.5, color: T.white },
  lineRow: { flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 8, paddingVertical: 12 },
  call: { fontFamily: F.condHeavy, fontSize: 17, letterSpacing: 0.5 },
  formCell: { width: 72, alignItems: 'center', padding: 8, borderRadius: T.r1, borderWidth: 1, borderColor: T.glassLine,
              backgroundColor: 'rgba(255,255,255,0.025)' },
  wl: { width: 28, height: 28, borderRadius: 14, alignItems: 'center', justifyContent: 'center', marginBottom: 6 },
  wlText: { fontFamily: F.condBlack, fontSize: 14 },
  formOpp: { fontFamily: F.bodyMed, fontSize: 10.5, color: T.white },
  formSub: { fontFamily: F.body, fontSize: 9.5, color: T.muted2, marginTop: 2 },
})
