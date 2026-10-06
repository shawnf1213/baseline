// Project — price any matchup with the same engine that builds the board
// (/api/prop/calculate, the bot's /prop). One card to set it up, one button,
// one verdict card, and the evidence under it.
//
// ONE PRICING REQUEST IN FLIGHT PER SESSION (operator ruling 7): the Run
// button is disabled while a request is out, and leaving the screen aborts
// it. A cold player can take up to a minute on the backend, so the wait has
// its own placeholder and a line saying why.
import { useEffect, useMemo, useRef, useState } from 'react'
import { StyleSheet, Text, TextInput, View, Pressable } from 'react-native'
import { useLocalSearchParams } from 'expo-router'
import { Screen } from '@/components/Screen'
import { Button, Card, Chip, Empty, Muted, PageTitle, Segmented } from '@/components/ui'
import { Skeleton } from '@/components/Skeleton'
import { PlayerPicker, PickedPlayer } from '@/components/PlayerPicker'
import { OptionSheet } from '@/components/OptionSheet'
import { Divider, EdgeScale, Figures, GameChart, HitWindows, Meter, StatBlock } from '@/components/charts'
import { ApiError, calcProp, fetchHistory, fetchNextMatch } from '@/lib/api'
import { resolveCourtName, useCourts } from '@/lib/courts'
import { Hist, PROP_TYPES, SURFACES, fmt, fmtLine, fmtSigned, propHasHistory, shapeHistory,
         shortProp } from '@/lib/picks'
import { F, T, sideTone, tier } from '@/theme'
import { success, tap, warn } from '@/lib/haptics'

type Mode = 'prop' | 'spread' | 'match'
type Tour = 'ATP' | 'WTA'
const MODES: { key: Mode; label: string }[] = [
  { key: 'prop', label: 'Prop' }, { key: 'spread', label: 'Spread' }, { key: 'match', label: 'Match' }]
const TOURS: { key: Tour; label: string }[] = [{ key: 'ATP', label: 'ATP' }, { key: 'WTA', label: 'WTA' }]
const PROP_OPTIONS = PROP_TYPES.map(p => ({ value: p.key, label: p.key }))

export default function Project() {
  const params = useLocalSearchParams<Record<string, string>>()
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
    if (!params?.t) return
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
      if (mode === 'prop' && propHasHistory(prop)) {
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
    <Screen>
      <PageTitle sub="Price any matchup with Baseline's model">Project</PageTitle>
      <Segmented options={MODES} value={mode} onChange={m => { setMode(m); clearResult() }} />

      {/* ── THE MATCHUP ── */}
      <Card>
        <Segmented options={TOURS} value={tour} onChange={switchTour} compact />
        <View style={{ flexDirection: 'row', alignItems: 'stretch', gap: 10 }}>
          <PlayerTile label="Player" value={player} onPress={() => setPicking('player')}
                      onClear={() => { setPlayer(null); clearResult() }} />
          <View style={s.vs}><Text style={s.vsText}>VS</Text></View>
          <PlayerTile label="Opponent" value={opponent} onPress={() => setPicking('opponent')}
                      onClear={() => { setOpponent(null); clearResult() }} />
        </View>

        <Divider />

        {mode === 'prop' ? (
          <View style={{ flexDirection: 'row', gap: 10 }}>
            <Pressable onPress={() => { tap(); setSheet('prop') }} style={[s.select, { flex: 1 }]}>
              <Text style={s.fieldK}>Prop</Text>
              <Text style={s.fieldV} numberOfLines={1}>{prop}</Text>
              <Text style={s.chev}>⌄</Text>
            </Pressable>
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
        <Pressable onPress={() => { tap(); setSheet('court') }} style={[s.select, { marginTop: 10 }]}>
          <Text style={s.fieldK}>Venue</Text>
          <Text style={s.fieldV} numberOfLines={1}>{court || 'No specific venue'}</Text>
          <Text style={s.chev}>⌄</Text>
        </Pressable>
        {scheduled?.surface ? (
          <Text style={[s.sched, scheduled.surface !== surface && { color: T.amber }]}>
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
            <Card style={[s.verdict, { borderColor: `${side.tone}55` }]}>
              <Head player={player} opponent={opponent} surface={surface} court={court} />
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14, marginTop: T.s4 }}>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={s.k}>{shortProp(prop)}</Text>
                  <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 10 }}>
                    <Text style={s.bigNum}>{fmt(proj)}</Text>
                    <View style={[s.leanPill, { borderColor: `${side.tone}55`, backgroundColor: `${side.tone}1C` }]}>
                      <Text style={[s.leanText, { color: side.tone }]}>{lean || '—'}</Text>
                    </View>
                  </View>
                  <Text style={s.under}>
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
                <Text style={[s.k, { marginBottom: 10 }]}>Serve & return</Text>
                <StatBlock prop={prop} res={res} surface={surface} playerName={player.name} opponentName={opponent.name} />
              </Card>
              {hist ? (
                <Card style={{ marginTop: 10 }}>
                  <Text style={[s.k, { marginBottom: 10 }]}>Recent form</Text>
                  <HitWindows hist={hist} lean={lean} line={isNum(line) ? ln : null} />
                  <View style={{ height: 14 }} />
                  <GameChart hist={hist} line={isNum(line) ? ln : null} lean={lean} />
                </Card>
              ) : propHasHistory(prop) ? (
                <Card style={{ marginTop: 10 }}><Skeleton h={120} r={10} /></Card>
              ) : null}
            </>
          ) : null}

          {res.explanation ? (
            <Card style={{ marginTop: 10 }}>
              <Text style={[s.k, { marginBottom: 6 }]}>The read</Text>
              <Text style={s.read}>{res.explanation}</Text>
            </Card>
          ) : null}
          <Muted size={10.5} style={{ textAlign: 'center', marginTop: 16 }}>Model projections, not betting advice.</Muted>
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
    </Screen>
  )
}

// ── pieces ──────────────────────────────────────────────────────────────────
function PlayerTile({ label, value, onPress, onClear }:
  { label: string; value: PickedPlayer | null; onPress: () => void; onClear: () => void }) {
  if (value) {
    return (
      <View style={[s.tile, s.tileOn]}>
        <Pressable onPress={() => { tap(); onClear() }} hitSlop={10} style={s.clear}><Text style={s.clearX}>×</Text></Pressable>
        <View style={s.avatar}><Text style={s.initials}>{initials(value.name)}</Text></View>
        <Text style={s.tileName} numberOfLines={2}>{value.name}</Text>
        <Text style={s.tileSub}>{value.currentRank ? `#${value.currentRank}` : label}</Text>
      </View>
    )
  }
  return (
    <Pressable onPress={() => { tap(); onPress() }} style={({ pressed }) => [s.tile, s.tileEmpty, pressed && { opacity: 0.7 }]}>
      <View style={[s.avatar, { borderWidth: 1.5, borderStyle: 'dashed', borderColor: T.glassLineHi, backgroundColor: 'transparent' }]}>
        <Text style={[s.initials, { fontSize: 22, color: T.muted2 }]}>+</Text>
      </View>
      <Text style={[s.tileName, { color: T.muted }]}>{label}</Text>
      <Text style={s.tileSub}>tap to choose</Text>
    </Pressable>
  )
}

function NumField({ label, value, onChange, placeholder, signed, width = 118 }:
  { label: string; value: string; onChange: (v: string) => void; placeholder: string; signed?: boolean; width?: number }) {
  const clean = (v: string) => v.replace(signed ? /[^\d.\-]/g : /[^\d.]/g, '')
  return (
    <View style={[s.select, { width, flexShrink: 0, borderColor: value ? `${T.green}66` : T.glassLine }]}>
      <Text style={s.fieldK}>{label}</Text>
      <TextInput value={value} onChangeText={v => onChange(clean(v))} placeholder={placeholder}
                 placeholderTextColor={T.muted2} keyboardType={signed ? 'numbers-and-punctuation' : 'decimal-pad'}
                 style={s.numInput} returnKeyType="done" />
    </View>
  )
}

function Head({ player, opponent, surface, court }:
  { player: PickedPlayer; opponent: PickedPlayer; surface: string; court: string }) {
  return (
    <View>
      <Text style={s.headName} numberOfLines={1}>{player.name}</Text>
      <Muted size={12.5}>vs {opponent.name} · {surface}{court ? ` · ${court}` : ''}</Muted>
    </View>
  )
}

function ConfRing({ conf, tone }: { conf: number | null | undefined; tone: string }) {
  if (conf == null) return null
  const tr = tier(conf)
  return (
    <View style={[s.ring, { borderColor: `${tone}66` }]}>
      <Text style={[s.ringNum, { color: tone }]}>{Math.round(conf)}</Text>
      <Text style={s.ringK}>{tr.label ? tr.label.toLowerCase() : 'conf.'}</Text>
    </View>
  )
}

function Big({ value, suffix = '', badge, tone }: { value: number | null; suffix?: string; badge: string; tone: string }) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 10 }}>
      <Text style={s.bigNum}>{value != null ? `${Math.round(value)}${suffix}` : '—'}</Text>
      <View style={[s.leanPill, { borderColor: `${tone}55`, backgroundColor: `${tone}1C` }]}>
        <Text style={[s.leanText, { color: tone }]}>{badge}</Text>
      </View>
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
    <Card style={[s.verdict, { borderColor: `${tone}55` }]}>
      <Head player={player} opponent={opponent} surface={surface} court={court} />
      <Text style={[s.k, { marginTop: T.s4 }]}>Games spread {sp > 0 ? '+' : ''}{sp}</Text>
      <Big value={cover} suffix="%" badge={covers ? 'Covers' : 'Does not cover'} tone={tone} />
      <Text style={s.under}>Projected margin <Text style={{ color: T.white, fontFamily: F.bodySemi }}>
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
    <Card style={[s.verdict, { borderColor: `${tone}55` }]}>
      <Head player={player} opponent={opponent} surface={surface} court={court} />
      <Text style={[s.k, { marginTop: T.s4 }]}>To win the match</Text>
      <Big value={wp ?? null} suffix="%" badge={fav ? 'Favoured' : 'Underdog'} tone={tone} />
      <Text style={s.under}>{res.match_format_label || 'Best of 3'} · <Text style={{ color: T.white, fontFamily: F.bodySemi }}>
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

const initials = (name: string) => {
  const t = (name || '').trim().split(/\s+/)
  return ((t[0]?.[0] || '') + (t.length > 1 ? t[t.length - 1][0] : '')).toUpperCase() || '?'
}

const s = StyleSheet.create({
  vs: { alignSelf: 'center', width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center',
        backgroundColor: 'rgba(255,255,255,0.05)', borderWidth: 1, borderColor: T.glassLine },
  vsText: { fontFamily: F.condBold, fontSize: 12, letterSpacing: 0.6, color: T.muted },
  tile: { flex: 1, minWidth: 0, minHeight: 124, paddingVertical: 14, paddingHorizontal: 8, borderRadius: T.r2,
          alignItems: 'center', justifyContent: 'center' },
  tileOn: { backgroundColor: 'rgba(255,255,255,0.04)', borderWidth: 1, borderColor: T.glassLine },
  tileEmpty: { borderWidth: 1.5, borderStyle: 'dashed', borderColor: T.glassLine },
  clear: { position: 'absolute', top: 4, right: 8, minWidth: 32, minHeight: 32, alignItems: 'center', justifyContent: 'center' },
  clearX: { fontSize: 20, color: T.muted2, lineHeight: 22 },
  avatar: { width: 50, height: 50, borderRadius: 25, backgroundColor: 'rgba(255,255,255,0.07)',
            alignItems: 'center', justifyContent: 'center' },
  initials: { fontFamily: F.condBold, fontSize: 17, color: T.white },
  tileName: { fontFamily: F.bodySemi, fontSize: 14, color: T.white, marginTop: 8, textAlign: 'center', lineHeight: 18 },
  tileSub: { fontFamily: F.body, fontSize: 11, color: T.muted2, marginTop: 2 },
  select: { minHeight: 56, borderRadius: T.r1, borderWidth: 1, borderColor: T.glassLine,
            backgroundColor: 'rgba(255,255,255,0.03)', paddingHorizontal: 12, paddingTop: 8, paddingBottom: 8,
            justifyContent: 'center' },
  fieldK: { fontFamily: F.condBold, fontSize: 9, letterSpacing: 1.2, textTransform: 'uppercase', color: T.muted2 },
  fieldV: { fontFamily: F.bodySemi, fontSize: 16, color: T.white, marginTop: 3, paddingRight: 18 },
  chev: { position: 'absolute', right: 12, top: 14, fontSize: 18, color: T.muted2 },
  numInput: { fontFamily: F.condHeavy, fontSize: 22, color: T.white, padding: 0, marginTop: 1, minHeight: 26 },
  sched: { fontFamily: F.body, fontSize: 12, color: T.muted2, lineHeight: 17, marginTop: 8 },
  verdict: { marginTop: T.s3, borderWidth: 1 },
  headName: { fontFamily: F.condBlack, fontSize: 20, color: T.white },
  k: { fontFamily: F.condBold, fontSize: 10.5, letterSpacing: 1.3, textTransform: 'uppercase', color: T.muted2 },
  bigNum: { fontFamily: F.condHeavy, fontSize: 54, lineHeight: 56, color: T.white, letterSpacing: -1 },
  leanPill: { borderWidth: 1, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 3 },
  leanText: { fontFamily: F.condBlack, fontSize: 16, letterSpacing: 1, textTransform: 'uppercase' },
  under: { fontFamily: F.body, fontSize: 12.5, color: T.muted, marginTop: 6 },
  ring: { width: 80, height: 80, borderRadius: 40, borderWidth: 5, alignItems: 'center', justifyContent: 'center' },
  ringNum: { fontFamily: F.condHeavy, fontSize: 26, lineHeight: 28 },
  ringK: { fontFamily: F.condBold, fontSize: 9, letterSpacing: 1, textTransform: 'uppercase', color: T.muted2 },
  read: { fontFamily: F.body, fontSize: 13.5, color: T.muted, lineHeight: 19 },
})
