// Project — price any matchup with the same engine that builds the board.
// Tennis: /api/prop/calculate (the bot's /prop). NFL and NBA: their own
// pricing endpoints, in TeamProject. One card to set it up, one button, one
// verdict card, and the evidence under it.
//
// ONE PRICING REQUEST IN FLIGHT PER SESSION (operator ruling 7): the Run
// button is disabled while a request is out, and leaving the screen aborts
// it. A cold player can take up to a minute on the backend, so the wait has
// its own placeholder and a line saying why.
import { useEffect, useMemo, useRef, useState } from 'react'
import { Text, View } from 'react-native'
import { useLocalSearchParams } from 'expo-router'
import { Screen } from '@/components/Screen'
import { Button, Card, Chip, Empty, Muted, PageTitle, Segmented } from '@/components/ui'
import { Skeleton } from '@/components/Skeleton'
import { PlayerPicker, PickedPlayer } from '@/components/PlayerPicker'
import { OptionSheet } from '@/components/OptionSheet'
import { TeamProject } from '@/components/TeamProject'
import { BigLine, ConfRing, NumField, SelectField, Tile, pb } from '@/components/projectBits'
import { Divider, EdgeScale, Figures, GameChart, HitWindows, StatBlock } from '@/components/charts'
import { ApiError, calcProp, fetchHistory, fetchNextMatch } from '@/lib/api'
import { resolveCourtName, useCourts } from '@/lib/courts'
import { Hist, PROP_TYPES, SURFACES, fmt, fmtLine, fmtSigned, propHasHistory, shapeHistory,
         shortProp } from '@/lib/picks'
import { SportSwitch, useSport } from '@/lib/sports'
import { F, T, sideTone } from '@/theme'
import { success, tap, warn } from '@/lib/haptics'

type Mode = 'prop' | 'spread' | 'match'
type Tour = 'ATP' | 'WTA'
const MODES: { key: Mode; label: string }[] = [
  { key: 'prop', label: 'Prop' }, { key: 'spread', label: 'Spread' }, { key: 'match', label: 'Match' }]
const TOURS: { key: Tour; label: string }[] = [{ key: 'ATP', label: 'ATP' }, { key: 'WTA', label: 'WTA' }]
const PROP_OPTIONS = PROP_TYPES.map(p => ({ value: p.key, label: p.key }))

export default function Project() {
  const params = useLocalSearchParams<Record<string, string>>()
  const { sport } = useSport()
  return (
    <Screen>
      <PageTitle sub="Price any matchup with Baseline's model">Project</PageTitle>
      <SportSwitch />
      {sport === 'tennis' ? <TennisProject params={params} /> : <TeamProject sport={sport} params={params} />}
    </Screen>
  )
}

function TennisProject({ params }: { params: Record<string, string | undefined> }) {
  const courts = useCourts()
  const [mode, setMode] = useState<Mode>('prop')
  const [tour, setTour] = useState<Tour>('ATP')
  const [player, setPlayer] = useState<PickedPlayer | null>(null)
  const [opponent, setOpponent] = useState<PickedPlayer | null>(null)
  const [prop, setProp] = useState<string>(PROP_TYPES[0].key)
  const [surface, setSurface] = useState<string>('Hard')
  const [court, setCourt] = useState('')
  const [line, setLine] = useState('')
  const [spread, setSpread] = useState('')
  const [picking, setPicking] = useState<'player' | 'opponent' | null>(null)
  const [sheet, setSheet] = useState<'prop' | 'court' | null>(null)
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<any>(null)
  const [hist, setHist] = useState<Hist | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [scheduled, setScheduled] = useState<{ surface: string; tournament: string } | null>(null)
  const [autoRun, setAutoRun] = useState(false)
  const alignedFor = useRef<string | null>(null)
  const ctl = useRef<AbortController | null>(null)

  // Leaving the screen cancels whatever is in flight.
  useEffect(() => () => { ctl.current?.abort() }, [])

  const clearResult = () => { setRes(null); setHist(null); setErr(null) }
  const switchTour = (t: Tour) => {
    if (t === tour) return
    setTour(t); setCourt(''); setPlayer(null); setOpponent(null); clearResult()
  }

  // ── ARRIVED FROM A PICK ───────────────────────────────────────────────────
  // The sheet hands over names, ids (when it could resolve them), surface,
  // tournament, prop and line. With both ids the projection runs on its own.
  useEffect(() => {
    if (!params?.t || (params.sport && params.sport !== 'tennis')) return
    const t: Tour = params.tour === 'WTA' ? 'WTA' : 'ATP'
    setMode('prop'); setTour(t)
    if (params.surface) setSurface(params.surface)
    setCourt(resolveCourtName(params.court || '', courts, t, params.surface || 'Hard'))
    if (params.prop) setProp(params.prop)
    setLine(params.line || '')
    setPlayer(params.playerId ? { id: params.playerId, name: params.player || '', tour: t, currentRank: null } : null)
    setOpponent(params.opponentId ? { id: params.opponentId, name: params.opponent || '', tour: t, currentRank: null } : null)
    clearResult()
    setAutoRun(Boolean(params.playerId && params.opponentId && params.line))
  }, [params?.t])   // eslint-disable-line react-hooks/exhaustive-deps

  // ── THE SURFACE COMES FROM THE SCHEDULED MATCH ────────────────────────────
  useEffect(() => {
    if (!player?.id || !opponent?.id) { setScheduled(null); return }
    const key = `${player.id}|${opponent.id}`
    if (alignedFor.current === key) return
    let alive = true
    fetchNextMatch(player.id, player.tour || tour).then(nm => {
      if (!alive || !nm || String(nm.opponent_id || '') !== String(opponent.id)) return
      alignedFor.current = key
      setScheduled({ surface: nm.surface || '', tournament: nm.tournament || '' })
      if (nm.surface && nm.surface !== surface) { setSurface(nm.surface); clearResult() }
      setCourt(resolveCourtName(nm.tournament, courts, player.tour || tour, nm.surface || surface))
    }).catch(() => {})
    return () => { alive = false }
  }, [player?.id, opponent?.id])   // eslint-disable-line react-hooks/exhaustive-deps

  const courtOptions = useMemo(() => {
    const list = courts?.[tour]?.[surface] || []
    return [{ value: '', label: 'No specific venue', sub: 'generic surface pace' }]
      .concat(list.map(c => ({ value: c.name, label: c.name, sub: c.cpr != null ? `pace ${c.cpr}` : '' })))
  }, [courts, tour, surface])

  const isNum = (v: string) => v !== '' && !isNaN(Number(v))
  const ready = !!player && !!opponent && (mode === 'prop' ? isNum(line) : mode === 'spread' ? isNum(spread) : true)
  const missing = !player ? 'Add a player' : !opponent ? 'Add an opponent'
    : mode === 'prop' && !isNum(line) ? 'Enter the book line'
    : mode === 'spread' && !isNum(spread) ? 'Enter the games spread' : 'Run projection'

  const run = async () => {
    if (!ready || busy || !player || !opponent) return
    tap()
    setBusy(true); clearResult()
    const c = new AbortController()
    ctl.current = c
    const ln = Number(line)
    try {
      const data = await calcProp({
        player_id: player.id, opponent_id: opponent.id,
        player_name: player.name, opponent_name: opponent.name,
        tour: player.tour || tour, surface, court,
        prop_type: mode === 'prop' ? prop : 'Total Games',
        prop_line: mode === 'prop' ? ln : 0,
        ...(mode === 'spread' ? { spread: Number(spread) } : {}),
      }, c.signal)
      if (c.signal.aborted) return
      setRes(data); success()
      if (mode === 'prop' && propHasHistory('tennis', prop)) {
        fetchHistory(player.id, player.tour || tour, prop, surface, ln)
          .then(h => { if (!c.signal.aborted) setHist(shapeHistory(h)) }).catch(() => {})
      }
    } catch (e: any) {
      if (c.signal.aborted) return
      warn()
      setErr(e instanceof ApiError ? ((e.body as any)?.detail || `Projection failed (HTTP ${e.status})`)
        : e?.name === 'AbortError' ? 'The projection took too long — try again.'
        : (e?.message || 'Projection failed'))
    } finally {
      if (ctl.current === c) { setBusy(false); ctl.current = null }
    }
  }
  useEffect(() => { if (autoRun && ready && !busy) { setAutoRun(false); run() } }, [autoRun, ready])  // eslint-disable-line react-hooks/exhaustive-deps

  const proj = typeof res?.model_projection === 'number' ? res.model_projection : null
  const ln = Number(line)
  const edge = proj != null && isNum(line) ? Math.round((proj - ln) * 10) / 10 : null
  const lean: string = res?.lean || (edge == null ? '' : edge > 0 ? 'OVER' : edge < 0 ? 'UNDER' : '')
  const side = sideTone(lean)
  const hitPct = useMemo(() => {
    if (!hist?.games.length || !isNum(line)) return null
    const hits = hist.games.filter(g => lean === 'UNDER' ? g.value < ln : g.value > ln).length
    return Math.round((hits / hist.games.length) * 100)
  }, [hist, lean, line])  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <Segmented options={MODES} value={mode} onChange={m => { setMode(m); clearResult() }} compact />

      {/* ── THE MATCHUP ── */}
      <Card>
        <Segmented options={TOURS} value={tour} onChange={switchTour} compact />
        <View style={{ flexDirection: 'row', alignItems: 'stretch', gap: 10 }}>
          <Tile label="Player" name={player?.name} sub={player?.currentRank ? `#${player.currentRank}` : undefined}
                onPress={() => setPicking('player')} onClear={() => { setPlayer(null); clearResult() }} />
          <View style={pb.vs}><Text style={pb.vsText}>VS</Text></View>
          <Tile label="Opponent" name={opponent?.name} sub={opponent?.currentRank ? `#${opponent.currentRank}` : undefined}
                onPress={() => setPicking('opponent')} onClear={() => { setOpponent(null); clearResult() }} />
        </View>

        <Divider />

        {mode === 'prop' ? (
          <View style={{ flexDirection: 'row', gap: 10 }}>
            <SelectField label="Prop" value={prop} onPress={() => setSheet('prop')} style={{ flex: 1 }} />
            <NumField label="Book line" value={line} onChange={setLine} placeholder="4.5" />
          </View>
        ) : mode === 'spread' ? (
          <View style={{ flexDirection: 'row', gap: 12, alignItems: 'center' }}>
            <NumField label="Games spread" value={spread} onChange={setSpread} placeholder="-4.5" signed width={140} />
            <Muted size={12} style={{ flex: 1, lineHeight: 17 }}>
              From {player ? player.name.split(' ').slice(-1)[0] : 'the player'}'s side: negative gives games, positive gets them.
            </Muted>
          </View>
        ) : (
          <Muted size={12.5} style={{ lineHeight: 18 }}>
            No line needed. This prices the match itself — who wins, how long it runs and how close it should be.
          </Muted>
        )}

        <View style={{ flexDirection: 'row', gap: 8, marginTop: T.s3, flexWrap: 'wrap' }}>
          {SURFACES.map(sf => (
            <Chip key={sf} active={surface === sf} onPress={() => { setSurface(sf); setCourt(''); clearResult() }}>{sf}</Chip>
          ))}
        </View>
        <SelectField label="Venue" value={court || 'No specific venue'} onPress={() => setSheet('court')} style={{ marginTop: 10 }} />
        {scheduled?.surface ? (
          <Text style={[pb.sched, scheduled.surface !== surface && { color: T.amber }]}>
            {scheduled.surface === surface
              ? `Scheduled: ${scheduled.surface}${scheduled.tournament ? ` · ${scheduled.tournament}` : ''}`
              : `This match is scheduled on ${scheduled.surface}${scheduled.tournament ? ` (${scheduled.tournament})` : ''} — you are projecting it on ${surface}.`}
          </Text>
        ) : null}

        <Button label={busy ? 'Projecting…' : missing} onPress={run} disabled={!ready} busy={busy}
                style={{ marginTop: T.s4 }} />
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
            Pricing {player?.name.split(' ').slice(-1)[0]} vs {opponent?.name.split(' ').slice(-1)[0]}. A player we haven't
            priced today can take up to a minute to pull.
          </Muted>
        </Card>
      ) : null}

      {err && !busy ? <Empty title="Couldn't project this one" hint={err} /> : null}

      {res && !busy && player && opponent ? (
        <>
          {mode === 'spread' ? <SpreadVerdict res={res} spread={spread} player={player} opponent={opponent} surface={surface} court={court} />
           : mode === 'match' ? <MatchVerdict res={res} player={player} opponent={opponent} surface={surface} court={court} />
           : (
            <Card style={[pb.verdict, { borderColor: `${side.tone}55` }]}>
              <Head player={player} opponent={opponent} surface={surface} court={court} />
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14, marginTop: T.s4 }}>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={pb.k}>{shortProp(prop)}</Text>
                  <BigLine value={fmt(proj)} badge={lean || '—'} tone={side.tone} />
                  <Text style={pb.under}>
                    Book line <Text style={{ color: T.white, fontFamily: F.bodySemi }}>{fmtLine(ln)}</Text>
                    {edge != null ? <> · edge <Text style={{ color: side.tone, fontFamily: F.bodySemi }}>{fmtSigned(edge)}</Text></> : null}
                  </Text>
                </View>
                <ConfRing conf={res.confidence} tone={side.tone} />
              </View>
              <View style={{ marginTop: T.s3 }}><EdgeScale line={isNum(line) ? ln : null} proj={proj} tone={side.tone} /></View>
              <Divider />
              <Figures cells={[
                ['Last 10 hit', hitPct != null ? `${hitPct}%` : '—',
                 hitPct == null ? T.muted2 : hitPct >= 70 ? T.green : hitPct >= 50 ? T.amber : T.red, hitPct],
                ['Win probability', res.p1_win_prob != null ? `${Math.round(res.p1_win_prob)}%` : '—', T.white, res.p1_win_prob ?? null],
                ['Season average', hist?.average != null ? fmt(hist.average) : '—', T.white, null],
                ['Matches in sample', hist?.sample != null ? String(hist.sample) : '—', T.muted, null],
              ]} />
            </Card>
          )}

          {mode === 'prop' ? (
            <>
              <Card style={{ marginTop: 10 }}>
                <Text style={[pb.k, { marginBottom: 10 }]}>Serve & return</Text>
                <StatBlock prop={prop} res={res} surface={surface} playerName={player.name} opponentName={opponent.name} />
              </Card>
              {hist ? (
                <Card style={{ marginTop: 10 }}>
                  <Text style={[pb.k, { marginBottom: 10 }]}>Recent form</Text>
                  <HitWindows hist={hist} lean={lean} line={isNum(line) ? ln : null} />
                  <View style={{ height: 14 }} />
                  <GameChart hist={hist} line={isNum(line) ? ln : null} lean={lean} />
                </Card>
              ) : propHasHistory('tennis', prop) ? (
                <Card style={{ marginTop: 10 }}><Skeleton h={120} r={10} /></Card>
              ) : null}
            </>
          ) : null}

          {res.explanation ? (
            <Card style={{ marginTop: 10 }}>
              <Text style={[pb.k, { marginBottom: 6 }]}>The read</Text>
              <Text style={pb.read}>{res.explanation}</Text>
            </Card>
          ) : null}
          <Muted size={10.5} style={{ textAlign: 'center', marginTop: 16 }}>Projections are for informational purposes only.</Muted>
        </>
      ) : null}

      {!res && !busy && !err ? (
        <Empty title="Pick a matchup"
               hint={mode === 'prop' ? 'Choose both players, a prop and the book line.' : 'Choose both players.'} />
      ) : null}

      <PlayerPicker open={picking !== null} label={picking === 'opponent' ? 'Opponent' : 'Player'} tour={tour}
                    onClose={() => setPicking(null)}
                    onPick={p => {
                      if (picking === 'player') { setPlayer(p); if (p.tour !== tour) { setTour(p.tour); setCourt('') } }
                      else setOpponent(p)
                      clearResult(); setPicking(null)
                    }} />
      <OptionSheet open={sheet === 'prop'} title="Prop" options={PROP_OPTIONS} value={prop}
                   onSelect={v => { setProp(v); clearResult() }} onClose={() => setSheet(null)} />
      <OptionSheet open={sheet === 'court'} title={`Venue · ${tour} ${surface}`} options={courtOptions} value={court}
                   onSelect={v => { setCourt(v); clearResult() }} onClose={() => setSheet(null)} />
    </>
  )
}

// ── pieces ──────────────────────────────────────────────────────────────────
function Head({ player, opponent, surface, court }:
  { player: PickedPlayer; opponent: PickedPlayer; surface: string; court: string }) {
  return (
    <View>
      <Text style={pb.headName} numberOfLines={1}>{player.name}</Text>
      <Muted size={12.5}>vs {opponent.name} · {surface}{court ? ` · ${court}` : ''}</Muted>
    </View>
  )
}

function SpreadVerdict({ res, spread, player, opponent, surface, court }:
  { res: any; spread: string; player: PickedPlayer; opponent: PickedPlayer; surface: string; court: string }) {
  const cover = typeof res.spread_p_cover === 'number' ? res.spread_p_cover * 100 : null
  const covers = cover != null && cover >= 50
  const tone = cover == null ? T.muted2 : covers ? T.green : T.red
  const sp = Number(spread)
  const margin = res.spread_margin_proj
  return (
    <Card style={[pb.verdict, { borderColor: `${tone}55` }]}>
      <Head player={player} opponent={opponent} surface={surface} court={court} />
      <Text style={[pb.k, { marginTop: T.s4 }]}>Games spread {sp > 0 ? '+' : ''}{sp}</Text>
      <BigLine value={cover != null ? `${Math.round(cover)}%` : '—'} badge={covers ? 'Covers' : 'Does not cover'} tone={tone} />
      <Text style={pb.under}>Projected margin <Text style={{ color: T.white, fontFamily: F.bodySemi }}>
        {typeof margin === 'number' ? `${margin > 0 ? '+' : ''}${margin.toFixed(1)} games` : '—'}</Text></Text>
      <Divider />
      <Figures cells={[
        ['Win probability', res.p1_win_prob != null ? `${Math.round(res.p1_win_prob)}%` : '—', T.white, res.p1_win_prob ?? null],
        ['Expected sets', res.expected_sets != null ? res.expected_sets.toFixed(2) : '—', T.white, null],
        ['Format', res.match_format_label || '—', T.muted, null],
        ['How close', res.competitiveness != null ? `${Math.round(res.competitiveness)}%` : '—', T.white, res.competitiveness ?? null],
      ]} />
    </Card>
  )
}

function MatchVerdict({ res, player, opponent, surface, court }:
  { res: any; player: PickedPlayer; opponent: PickedPlayer; surface: string; court: string }) {
  const wp = res.p1_win_prob
  const fav = wp != null && wp >= 50
  const tone = wp == null ? T.muted2 : fav ? T.green : T.red
  return (
    <Card style={[pb.verdict, { borderColor: `${tone}55` }]}>
      <Head player={player} opponent={opponent} surface={surface} court={court} />
      <Text style={[pb.k, { marginTop: T.s4 }]}>To win the match</Text>
      <BigLine value={wp != null ? `${Math.round(wp)}%` : '—'} badge={fav ? 'Favoured' : 'Underdog'} tone={tone} />
      <Text style={pb.under}>{res.match_format_label || 'Best of 3'} · <Text style={{ color: T.white, fontFamily: F.bodySemi }}>
        {res.expected_sets != null ? `${res.expected_sets.toFixed(2)} sets` : '—'}</Text> expected</Text>
      <Divider />
      <Figures cells={[
        ['Total games', res.tg_model_proj != null ? res.tg_model_proj.toFixed(1) : '—', T.white, null],
        ['How close', res.competitiveness != null ? `${Math.round(res.competitiveness)}%` : '—', T.white, res.competitiveness ?? null],
        ['Gap', res.win_prob_gap != null ? `${Math.round(res.win_prob_gap)}%` : '—', T.muted, null],
        ['Conditions', res.environment_label || '—', T.muted, null],
      ]} />
    </Card>
  )
}
