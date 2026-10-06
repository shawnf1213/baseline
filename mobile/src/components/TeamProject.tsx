// Project, for NFL and NBA: the same question as tennis asked of a different
// sport. One player (the opponent is a fact from the schedule, not a choice),
// a prop from /api/{sport}/props, the book line, Run. The verdict card reads
// only the fields the Discord card shows — never a confidence breakdown.
import { useEffect, useMemo, useRef, useState } from 'react'
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native'
import { Button, Card, Empty, Muted } from './ui'
import { Skeleton } from './Skeleton'
import { Sheet } from './Sheet'
import { OptionSheet } from './OptionSheet'
import { Divider, EdgeScale, Figures, GameChart, HitWindows } from './charts'
import { BigLine, ConfRing, FactTile, NumField, SelectField, Tile, pb } from './projectBits'
import { ApiError, fetchNbaPlayer, fetchNbaProps, fetchNflPlayer, fetchNflProps, projectNba, projectNfl,
         searchNba, searchNfl } from '@/lib/api'
import { Hist, NBA_PROPS, NFL_PROPS, fmt, fmtLine, fmtSigned, nbaHistory, nflHistory, propLabel } from '@/lib/picks'
import type { SportKey } from '@/lib/sports'
import { F, T, sideTone } from '@/theme'
import { success, tap, warn } from '@/lib/haptics'

type Player = { name: string; team: string; position?: string; games?: number | null }
type Prop = { key: string; label: string }

export function TeamProject({ sport, params }: { sport: SportKey; params: Record<string, string | undefined> }) {
  const [props, setProps] = useState<Prop[] | null>(null)
  const [avail, setAvail] = useState<boolean | null>(null)
  const [prop, setProp] = useState<string>(sport === 'nfl' ? 'receiving_yards' : 'pts')
  const [player, setPlayer] = useState<Player | null>(null)
  const [line, setLine] = useState('')
  const [picking, setPicking] = useState(false)
  const [sheet, setSheet] = useState(false)
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<any>(null)
  const [hist, setHist] = useState<Hist | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [autoRun, setAutoRun] = useState(false)
  const ctl = useRef<AbortController | null>(null)
  useEffect(() => () => { ctl.current?.abort() }, [])

  useEffect(() => {
    let alive = true
    ;(sport === 'nfl' ? fetchNflProps() : fetchNbaProps()).then(d => {
      if (!alive) return
      const ok = sport === 'nfl' ? !!d?.ready : !!d?.available
      const list: Prop[] = (d?.props || []).map((p: any) => ({ key: String(p.key ?? p.value), label: String(p.label || p.key || p.value) }))
      setAvail(ok); setProps(list.length ? list : (sport === 'nfl' ? [...NFL_PROPS] : [...NBA_PROPS]).map(p => ({ key: p.key, label: p.label })))
    }).catch(() => { if (alive) { setAvail(false); setProps([]) } })
    return () => { alive = false }
  }, [sport])

  const clearResult = () => { setRes(null); setHist(null); setErr(null) }

  // Arrived from a pick or a board row.
  useEffect(() => {
    if (!params?.t || params.sport !== sport) return
    if (params.prop) setProp(params.prop)
    setLine(params.line || '')
    setPlayer(params.player ? { name: params.player, team: params.team || '' } : null)
    clearResult()
    setAutoRun(Boolean(params.player && params.line))
  }, [params?.t])   // eslint-disable-line react-hooks/exhaustive-deps

  const isNum = (v: string) => v !== '' && !isNaN(Number(v))
  const ready = !!player && !!prop && isNum(line)
  const missing = !player ? 'Add a player' : !isNum(line) ? 'Enter the book line' : 'Run projection'

  const run = async () => {
    if (!ready || busy || !player) return
    tap(); setBusy(true); clearResult()
    const c = new AbortController(); ctl.current = c
    const ln = Number(line)
    try {
      const body = { player: player.name, prop, line: ln, team: player.team || undefined }
      const d = sport === 'nfl' ? await projectNfl(body, c.signal) : await projectNba(body, c.signal)
      if (c.signal.aborted) return
      if (!d || d.ok === false || d.available === false || d.error || typeof d.projection !== 'number') {
        warn()
        setErr(d?.reason || (d?.error === 'timeout' ? 'The projection took too long — try again.'
          : d?.available === false ? `${sport.toUpperCase()} pricing is not available right now.`
          : 'Not enough data to price this player.'))
        return
      }
      setRes(d); success()
      ;(sport === 'nfl'
        ? fetchNflPlayer(player.name).then(p => nflHistory(p?.players?.[0], prop, ln))
        : fetchNbaPlayer(player.name, 10).then(p => nbaHistory(p, prop, ln)))
        .then(h => { if (!c.signal.aborted) setHist(h) }).catch(() => {})
    } catch (e: any) {
      if (c.signal.aborted) return
      warn()
      setErr(e instanceof ApiError ? ((e.body as any)?.detail || `Projection failed (HTTP ${e.status})`)
        : e?.name === 'AbortError' ? 'The projection took too long — try again.' : (e?.message || 'Projection failed'))
    } finally { if (ctl.current === c) { setBusy(false); ctl.current = null } }
  }
  useEffect(() => { if (autoRun && ready && !busy) { setAutoRun(false); run() } }, [autoRun, ready])  // eslint-disable-line react-hooks/exhaustive-deps

  const proj: number | null = typeof res?.projection === 'number' ? res.projection : null
  const ln = Number(line)
  const edge = proj != null && isNum(line) ? Math.round((proj - ln) * 10) / 10 : null
  const lean: string = res?.lean || (edge == null ? '' : edge > 0 ? 'OVER' : edge < 0 ? 'UNDER' : '')
  const side = sideTone(lean)
  const game = res?.game || {}
  const label = props?.find(p => p.key === prop)?.label || propLabel(sport, prop)
  const opponentName: string | null = sport === 'nfl' ? (game.opponent_team || res?.opponent || null) : (res?.opponent || null)
  const hitPct = useMemo(() => {
    if (!hist?.games.length || !isNum(line)) return null
    const hits = hist.games.filter(g => lean === 'UNDER' ? g.value < ln : g.value > ln).length
    return Math.round((hits / hist.games.length) * 100)
  }, [hist, lean, line])  // eslint-disable-line react-hooks/exhaustive-deps

  if (avail === false) {
    return <Empty title={`${sport.toUpperCase()} pricing unavailable`}
                  hint="This deploy does not carry the model for this sport. Picks and the board still work." />
  }

  return (
    <>
      <Card>
        <View style={{ flexDirection: 'row', alignItems: 'stretch', gap: 10 }}>
          <Tile label="Player" name={player?.name} sub={[player?.position, player?.team].filter(Boolean).join(' · ')}
                onPress={() => setPicking(true)} onClear={() => { setPlayer(null); clearResult() }} />
          <View style={pb.vs}><Text style={pb.vsText}>VS</Text></View>
          <FactTile label="Opponent comes from the schedule" name={opponentName} sub={game.matchup || 'opponent'} />
        </View>
        <Divider />
        <View style={{ flexDirection: 'row', gap: 10 }}>
          <SelectField label="Prop" value={label} onPress={() => setSheet(true)} style={{ flex: 1 }} />
          <NumField label="Book line" value={line} onChange={setLine} placeholder={sport === 'nfl' ? '50.5' : '24.5'} />
        </View>
        <Button label={busy ? 'Projecting…' : missing} onPress={run} disabled={!ready} busy={busy} style={{ marginTop: T.s4 }} />
      </Card>

      {busy ? (
        <Card style={{ marginTop: T.s3 }}>
          <View style={{ flexDirection: 'row', gap: 12, alignItems: 'center' }}>
            <View style={{ flex: 1, gap: 8 }}>
              <Skeleton w={90} h={10} /><Skeleton w="60%" h={44} r={8} /><Skeleton w="45%" h={12} />
            </View>
            <Skeleton w={76} h={76} r={38} />
          </View>
          <Muted size={11.5} style={{ marginTop: 14, lineHeight: 16 }}>
            Pricing {player?.name}. The first run of the day pulls this player's season, which can take up to a minute.
          </Muted>
        </Card>
      ) : null}

      {err && !busy ? <Empty title="Couldn't project this one" hint={err} /> : null}

      {res && !busy && player ? (
        <>
          <Card style={[pb.verdict, { borderColor: `${side.tone}55` }]}>
            <Text style={pb.headName} numberOfLines={1}>{player.name}</Text>
            <Muted size={12.5}>
              {[res.position || player.position, game.matchup || (opponentName ? `vs ${opponentName}` : ''),
                game.kickoff ? String(game.kickoff).slice(5, 10) : ''].filter(Boolean).join(' · ')}
            </Muted>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14, marginTop: T.s4 }}>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={pb.k}>{label}</Text>
                <BigLine value={fmt(proj)} badge={lean || '—'} tone={side.tone} />
                <Text style={pb.under}>
                  Book line <Text style={{ color: T.white, fontFamily: F.bodySemi }}>{fmtLine(ln)}</Text>
                  {edge != null ? <> · edge <Text style={{ color: side.tone, fontFamily: F.bodySemi }}>{fmtSigned(edge)}</Text></> : null}
                  {typeof res.fair_line === 'number' ? <> · fair line <Text style={{ color: T.white, fontFamily: F.bodySemi }}>{fmt(res.fair_line)}</Text></> : null}
                </Text>
              </View>
              <ConfRing conf={typeof res.confidence === 'number' ? res.confidence : null} tone={side.tone} />
            </View>
            <View style={{ marginTop: T.s3 }}><EdgeScale line={isNum(line) ? ln : null} proj={proj} tone={side.tone} /></View>
            <Divider />
            <Figures cells={sport === 'nfl' ? [
              ['Chance of over', typeof res.p_over === 'number' ? `${Math.round(res.p_over * 100)}%` : '—', T.white,
               typeof res.p_over === 'number' ? res.p_over * 100 : null],
              ['Spread', game.player_spread != null ? `${game.player_spread > 0 ? '+' : ''}${game.player_spread}` : '—', T.white, null],
              ['Game total', game.total != null ? String(game.total) : '—', T.white, null],
              ['Games in sample', res.games_in_window != null ? String(res.games_in_window) : '—', T.muted, null],
            ] : [
              ['Chance of over', typeof res.p_over === 'number' ? `${Math.round(res.p_over * 100)}%` : '—', T.white,
               typeof res.p_over === 'number' ? res.p_over * 100 : null],
              ['Projected minutes', typeof res.minutes === 'number' ? res.minutes.toFixed(0) : '—', T.white, null],
              ['Rotation', res.rotation ? String(res.rotation) : '—', res.rotation === 'volatile' ? T.amber : T.muted, null],
              ['Games in sample', res.games_in_window != null ? String(res.games_in_window) : '—', T.muted, null],
            ]} />
          </Card>

          <Drivers sport={sport} res={res} />

          {hist ? (
            <Card style={{ marginTop: 10 }}>
              <Text style={[pb.k, { marginBottom: 10 }]}>Recent form</Text>
              <HitWindows hist={hist} lean={lean || 'OVER'} line={isNum(line) ? ln : null} gamesWord="games" />
              <View style={{ height: 14 }} />
              <GameChart hist={hist} line={isNum(line) ? ln : null} lean={lean || 'OVER'} gamesWord="games" />
            </Card>
          ) : <Card style={{ marginTop: 10 }}><Skeleton h={120} r={10} /></Card>}
          <Muted size={10.5} style={{ textAlign: 'center', marginTop: 16 }}>Projections are for informational purposes only.</Muted>
        </>
      ) : null}

      {!res && !busy && !err ? <Empty title="Pick a player" hint="Choose a player, a prop and the book line." /> : null}

      <TeamPicker open={picking} sport={sport} onClose={() => setPicking(false)}
                  onPick={p => { setPlayer(p); clearResult(); setPicking(false) }} />
      <OptionSheet open={sheet} title="Prop" options={(props || []).map(p => ({ value: p.key, label: p.label }))}
                   value={prop} onSelect={v => { setProp(v); clearResult() }} onClose={() => setSheet(false)} />
    </>
  )
}

// What the number is built from — the drivers and the opponent, in a sentence.
function Drivers({ sport, res }: { sport: SportKey; res: any }) {
  const d = res?.drivers || {}
  const rows: [string, number][] = []
  for (const [k, v] of Object.entries(d)) {
    if (typeof v === 'number') rows.push([k, v])
    else if (v && typeof v === 'object') for (const [k2, v2] of Object.entries(v as any)) if (typeof v2 === 'number') rows.push([`${k} ${k2}`, v2])
  }
  const label = (k: string) => k.replace(/_/g, ' ')
  const fmtDriver = (x: number) => (x > 0 && x < 1 ? `${Math.round(x * 100)}%` : x.toFixed(2))
  const sentence = sport === 'nfl'
    ? (res.opponent
        ? `Opponent ${res.opponent}${res.opponent_rank?.rank ? `, ranked ${res.opponent_rank.rank} of ${res.opponent_rank.of} (${res.opponent_rank.raw} ${res.opponent_rank.raw_label || ''})` : ''}, applied to the rate at ×${res.opponent_factor ?? '—'}.`
        : String(res.opponent_basis || ''))
    : [res.def_basis ? `Defence: ${res.def_basis}.` : '', res.pace_basis ? `Pace: ${res.pace_basis}.` : '',
       res.usage_vacuum_basis ? `Usage: ${res.usage_vacuum_basis}.` : '',
       res.home_basis ? `Home/away: ${res.home_basis}.` : '', res.back_to_back ? 'Back-to-back game.' : ''].filter(Boolean).join(' ')
  if (!rows.length && !sentence) return null
  return (
    <Card style={{ marginTop: 10 }}>
      <Text style={[pb.k, { marginBottom: 10 }]}>What the number is built from</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
        {rows.slice(0, 8).map(([k, x]) => (
          <View key={k} style={{ minWidth: '30%' }}>
            <Text style={pb.k} numberOfLines={1}>{label(k)}</Text>
            <Text style={{ fontFamily: F.condHeavy, fontSize: 18, color: T.white }}>{fmtDriver(x)}</Text>
          </View>
        ))}
      </View>
      {sentence ? <Text style={[pb.read, { marginTop: 10, fontSize: 12.5, lineHeight: 17 }]}>
        {sentence}{res.window ? ` Usage window: ${res.window}.` : ''}
      </Text> : null}
    </Card>
  )
}

function TeamPicker({ open, sport, onPick, onClose }:
  { open: boolean; sport: SportKey; onPick: (p: Player) => void; onClose: () => void }) {
  const [q, setQ] = useState('')
  const [rows, setRows] = useState<Player[]>([])
  const [loading, setLoading] = useState(false)
  useEffect(() => { if (!open) { setQ(''); setRows([]) } }, [open])
  useEffect(() => {
    if (q.trim().length < 2) { setRows([]); return }
    let alive = true
    setLoading(true)
    const t = setTimeout(() => {
      (sport === 'nfl'
        ? searchNfl(q.trim()).then(d => (d?.players || []).map((p: any) => ({ name: p.name, team: p.team || '', position: p.position })))
        : searchNba(q.trim()).then(d => (Array.isArray(d) ? d : []).map((p: any) => ({ name: p.name, team: p.team || '', games: p.games ?? null }))))
        .then(r => { if (alive) setRows(r) }).catch(() => { if (alive) setRows([]) })
        .finally(() => { if (alive) setLoading(false) })
    }, 300)
    return () => { alive = false; clearTimeout(t) }
  }, [q, sport])
  return (
    <Sheet open={open} onClose={onClose} title="Choose player">
      <TextInput value={q} onChangeText={setQ} autoFocus autoCorrect={false} autoCapitalize="words"
                 placeholder={`Search ${sport.toUpperCase()} players`} placeholderTextColor={T.muted2}
                 style={s.input} returnKeyType="search" clearButtonMode="while-editing" />
      {q.trim().length < 2 ? <Muted size={12.5} style={{ marginTop: 12 }}>Type a few letters of a name.</Muted>
        : loading && !rows.length ? <View style={{ marginTop: 12, gap: 8 }}>{[0, 1, 2].map(i => <Skeleton key={i} h={52} r={12} />)}</View>
        : !rows.length ? <Muted size={12.5} style={{ marginTop: 12 }}>No {sport.toUpperCase()} player matches "{q.trim()}".</Muted>
        : (
          <View style={{ marginTop: 10 }}>
            {rows.slice(0, 10).map(p => (
              <Pressable key={`${p.name}|${p.team}`} onPress={() => { tap(); onPick(p) }}
                         style={({ pressed }) => [s.row, pressed && { backgroundColor: 'rgba(255,255,255,0.06)' }]}>
                <View style={s.avatar}><Text style={s.avatarText}>{(p.team || '?').slice(0, 3)}</Text></View>
                <Text style={s.name} numberOfLines={1}>{p.name}</Text>
                <Text style={s.sub}>{[p.position, p.games != null ? `${p.games} games` : ''].filter(Boolean).join(' · ')}</Text>
              </Pressable>
            ))}
          </View>
        )}
    </Sheet>
  )
}

const s = StyleSheet.create({
  input: { backgroundColor: 'rgba(255,255,255,0.05)', borderWidth: 1, borderColor: `${T.green}66`,
           borderRadius: T.r1, paddingHorizontal: 14, minHeight: 50, color: T.white, fontFamily: F.body,
           fontSize: 17, marginTop: 4 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 54, paddingHorizontal: 10,
         borderRadius: T.r1, marginBottom: 4 },
  avatar: { width: 36, height: 36, borderRadius: 18, backgroundColor: 'rgba(255,255,255,0.07)',
            alignItems: 'center', justifyContent: 'center' },
  avatarText: { fontFamily: F.condBold, fontSize: 11, color: T.muted },
  name: { flex: 1, fontFamily: F.bodyMed, fontSize: 16, color: T.white },
  sub: { fontFamily: F.condBold, fontSize: 12, color: T.muted2 },
})
