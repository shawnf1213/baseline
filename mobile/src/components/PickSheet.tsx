// A pick's full story, in a sheet: the matchup, the call, the projection
// against the line, confidence, the result, and the player's recent form for
// that prop. The card shows the verdict; this is where the reader checks it.
// One sheet for every sport — only the "recent form" source differs.
//
// Tennis record rows carry names, not Sofascore ids, so recent form is
// fetched lazily: resolve the player by name (unless the board already did),
// then /api/history for this prop, surface and line (Fantasy Score and Break
// Points Saved included — the backend computes them per match with the same
// formulas the resolver grades with). NFL reads the published profile's
// weekly log; NBA the recent game log. All cancelled if the sheet closes.
import { useEffect, useRef, useState } from 'react'
import { StyleSheet, Text, View } from 'react-native'
import { router } from 'expo-router'
import { Sheet } from './Sheet'
import { Button, Card, Disclaimer, Muted } from './ui'
import { Skeleton } from './Skeleton'
import { EdgeScale, GameChart, HitWindows } from './charts'
import { ResultBadge, SurfaceChip } from './PickCard'
import { PlayerAvatar, TeamLogo } from './Avatar'
import { ConfRing } from './Ring'
import { CardGlow } from './Glow'
import { F, T, sideTone, tier } from '@/theme'
import { fetchHistory, fetchNbaPlayer, fetchNflPlayer } from '@/lib/api'
import { Found, Hist, PickRow, fmt, fmtLine, fmtSigned, kickoffLabel, nbaHistory, nflHistory,
         prettyDate, propHasHistory, propLabel, propUnit, resolvePlayer, resultMeta, shapeHistory,
         startTimeLabel } from '@/lib/picks'
import { useSport } from '@/lib/sports'
import { tap } from '@/lib/haptics'

const TIER_WORD: Record<string, string> = {
  ELITE: 'Elite — the model\'s strongest band',
  STRONG: 'Strong',
  LEAN: 'Lean — a smaller edge',
  '': 'Below the posting bar',
}
const BOOK: Record<string, string> = { prizepicks: 'PrizePicks', underdog: 'Underdog' }

type Form = { state: 'loading' | 'ready' | 'none' | 'error'; hist?: Hist; who?: Found | null }

export function PickSheet({ pick, onClose }: { pick: PickRow | null; onClose: () => void }) {
  const { setSport } = useSport()
  const [form, setForm] = useState<Form>({ state: 'loading' })
  const [opp, setOpp] = useState<Found | null>(null)
  const alive = useRef(0)

  useEffect(() => {
    const token = ++alive.current
    setOpp(null)
    if (!pick) return
    if (!propHasHistory(pick.sport, pick.propType)) { setForm({ state: 'none' }); return }
    setForm({ state: 'loading' })
    ;(async () => {
      try {
        if (pick.sport === 'nfl') {
          const d = await fetchNflPlayer(pick.player)
          const h = nflHistory(d?.players?.[0], pick.propType, pick.line)
          if (alive.current === token) setForm(h ? { state: 'ready', hist: h } : { state: 'error' })
          return
        }
        if (pick.sport === 'nba') {
          const d = await fetchNbaPlayer(pick.player, 10)
          const h = nbaHistory(d, pick.propType, pick.line)
          if (alive.current === token) setForm(h ? { state: 'ready', hist: h } : { state: 'error' })
          return
        }
        // tennis
        const who: Found | null = pick.playerId
          ? { id: pick.playerId, name: pick.player, tour: pick.tour === 'WTA' ? 'WTA' : 'ATP', currentRank: null }
          : await resolvePlayer(pick.player, pick.tour)
        if (alive.current !== token) return
        if (!who) { setForm({ state: 'error' }); return }
        const h = await fetchHistory(who.id, who.tour, pick.propType, pick.surface, pick.line ?? 0)
        if (alive.current !== token) return
        const shaped = shapeHistory(h)
        setForm(shaped.games.length ? { state: 'ready', hist: shaped, who } : { state: 'error', who })
        if (pick.opponentId) setOpp({ id: pick.opponentId, name: pick.opponent, tour: who.tour, currentRank: null })
        else resolvePlayer(pick.opponent, who.tour).then(o => { if (alive.current === token) setOpp(o) })
      } catch { if (alive.current === token) setForm({ state: 'error' }) }
    })()
  }, [pick?.key])   // eslint-disable-line react-hooks/exhaustive-deps

  if (!pick) return <Sheet open={false} onClose={onClose}><View /></Sheet>
  const r = pick
  const side = sideTone(r.lean)
  const tr = tier(r.confidence)
  const meta = resultMeta(r.result)
  const resTone = meta.tone === 'win' ? T.green : meta.tone === 'loss' ? T.red : meta.tone === 'void' ? T.muted2 : T.amber
  const who = form.who || null
  const label = propLabel(r.sport, r.propType)
  const unit = propUnit(r.sport, r.propType)
  const last = r.player.split(' ').slice(-1)[0]
  const priced = r.projection != null
  const gamesWord = r.sport === 'tennis' ? 'matches' : 'games'
  const when = r.sport === 'tennis' ? (r.startTs ? startTimeLabel(r.startTs) : '') : kickoffLabel(r.startsAt)

  const openProject = () => {
    tap()
    onClose()
    setSport(r.sport)
    const base = { sport: r.sport, player: r.player, prop: r.propType,
                   line: r.line != null ? String(r.line) : '', t: String(Date.now()) }
    router.push({ pathname: '/project', params: r.sport === 'tennis' ? {
      ...base, opponent: r.opponent,
      playerId: who?.id || r.playerId || '', opponentId: opp?.id || r.opponentId || '',
      tour: who?.tour || (r.tour === 'WTA' ? 'WTA' : 'ATP'),
      surface: r.surface || 'Hard', court: r.tournament || '',
    } : { ...base, team: r.team || '' } })
  }

  return (
    <Sheet open={!!pick} onClose={onClose}
           title={`${prettyDate(r.date)} · ${BOOK[r.book] || r.book}${r.isPotd ? ' · ⭐' : ''}`}
           footer={<Button label="Project this matchup" onPress={openProject} kind="ghost" />}>
      {/* THE MATCHUP */}
      <View style={s.matchup}>
        <View style={s.side}>
          <PlayerAvatar sport={r.sport} name={r.player} size={70} ring={priced ? side.tone : null} team={r.team} />
          <Text style={s.who} numberOfLines={2}>{r.player}</Text>
          <Text style={s.whoSub}>{r.playerRank ? `#${r.playerRank}` : r.team || (r.tour && !r.tourInferred ? r.tour : '')}</Text>
        </View>
        <View style={s.vs}><Text style={s.vsText}>VS</Text></View>
        <View style={s.side}>
          {r.sport === 'tennis'
            ? <PlayerAvatar sport="tennis" name={r.opponent} size={70} />
            : <TeamLogo sport={r.sport} team={r.opponent} size={70} />}
          <Text style={s.who} numberOfLines={2}>{r.opponent || '—'}</Text>
          <Text style={s.whoSub}>{r.opponentRank ? `#${r.opponentRank}` : r.sport !== 'tennis' ? 'opponent' : ''}</Text>
        </View>
      </View>
      <View style={s.context}>
        {r.sport === 'tennis' ? <SurfaceChip surface={r.surface} /> : null}
        <Muted size={12} style={{ flexShrink: 1, textAlign: 'center' }}>
          {[r.sport === 'tennis' ? r.tournament : r.matchup, when].filter(Boolean).join(' · ') || ' '}
        </Muted>
      </View>

      {/* THE CALL */}
      <Card style={[s.call, { borderColor: `${side.tone}55` }]}>
        <CardGlow color={priced ? side.tone : T.muted2} strength={0.22} />
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
          <View style={{ flex: 1, minWidth: 0 }}>
            <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
              <Text style={[s.callSide, { color: priced ? side.tone : T.muted }]}>{r.lean || (priced ? '—' : 'LINE')}</Text>
              <Text style={s.callLine}>{fmtLine(r.line)}</Text>
              <Text style={s.callProp}>{label}</Text>
            </View>
            {r.isThreeX ? <Text style={s.threex}>3x slip</Text> : null}
          </View>
          {priced ? <ConfRing conf={r.confidence} size={70} /> : <ConfRing conf={null} size={70} state="pending" />}
        </View>
        {priced ? (
          <>
            <View style={{ marginTop: 16 }}>
              <EdgeScale line={r.line} proj={r.projection} tone={side.tone} />
            </View>
            <Text style={s.edgeLine}>
              Edge <Text style={{ color: side.tone, fontFamily: F.bodySemi }}>{fmtSigned(r.edge)} {unit}</Text>
              {' '}between Baseline's number and the book's line.
              {r.overPrice != null && r.underPrice != null
                ? ` Underdog prices: over ${r.overPrice > 0 ? '+' : ''}${r.overPrice}, under ${r.underPrice > 0 ? '+' : ''}${r.underPrice}.` : ''}
            </Text>
            <View style={s.tierRow}>
              <View style={[s.tierDot, { backgroundColor: tr.tone }]} />
              <Text style={s.tierWord}>{TIER_WORD[tr.label]}</Text>
            </View>
            <Muted size={11} style={{ marginTop: 4, lineHeight: 15 }}>
              Confidence is the model's own 0–100 score — the same number the Discord card shows.
              {r.usageWindow ? ` Usage window: ${r.usageWindow}.` : ''}
              {r.minutes != null ? ` Projected minutes: ${Math.round(r.minutes)}.` : ''}
              {r.rotation && r.rotation !== 'stable' ? ` Rotation: ${r.rotation}.` : ''}
            </Muted>
          </>
        ) : (
          <Text style={s.edgeLine}>Baseline hasn't priced this line yet — rows are priced on a schedule and this one is in the queue. Check back in a few minutes, or use Project this matchup to run it now.</Text>
        )}
      </Card>

      {/* RESULT */}
      <Card style={{ marginTop: 10, borderColor: `${resTone}40` }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={s.k}>Result</Text>
            {meta.tone === 'pending' ? (
              <Text style={s.resultText}>Not graded yet — results post after the {r.sport === 'tennis' ? 'match' : 'game'} finishes.</Text>
            ) : meta.tone === 'void' ? (
              <Text style={s.resultText}>{meta.label} — this play did not count (did not play, walkover, or a line that changed).</Text>
            ) : (
              <Text style={s.resultText}>
                {last} finished with{' '}
                <Text style={{ color: T.white, fontFamily: F.bodySemi }}>
                  {r.resultValue != null ? `${fmtLine(r.resultValue)} ${unit}` : 'a graded result'}
                </Text>
                {r.resultValue != null && r.line != null ? ` against a line of ${fmtLine(r.line)}.` : '.'}
              </Text>
            )}
          </View>
          {meta.tone !== 'pending' ? <ResultBadge r={r} big /> : null}
        </View>
      </Card>

      {/* RECENT FORM */}
      <Text style={[s.k, { marginTop: 22, marginBottom: 8 }]}>Recent form · {label}</Text>
      {form.state === 'loading' ? (
        <Card>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            {[0, 1, 2].map(i => <Skeleton key={i} h={110} r={14} style={{ flex: 1 }} />)}
          </View>
          <Skeleton h={150} r={12} style={{ marginTop: 12 }} />
          <Muted size={11} style={{ marginTop: 10 }}>Pulling {last}'s recent {gamesWord}…</Muted>
        </Card>
      ) : form.state === 'none' ? (
        <Card><Muted size={12.5} style={{ lineHeight: 17 }}>
          There is no per-{r.sport === 'tennis' ? 'match' : 'game'} log for {label}. Use Project this matchup for the full breakdown.
        </Muted></Card>
      ) : form.state === 'error' ? (
        <Card><Muted size={12.5}>No recent {gamesWord} with this stat{r.surface ? ` on ${r.surface.toLowerCase()}` : ''} yet.</Muted></Card>
      ) : (
        <Card>
          <HitWindows hist={form.hist!} lean={r.lean || 'OVER'} line={r.line} gamesWord={gamesWord} />
          <Muted size={11} style={{ marginTop: 10, marginBottom: 16, lineHeight: 15 }}>
            How often {last} finished {r.lean === 'UNDER' ? 'under' : 'over'} {fmtLine(r.line)}
            {r.surface ? ` on ${r.surface.toLowerCase()}` : ''}
            {form.hist!.average != null ? ` · average ${fmt(form.hist!.average)}` : ''}
          </Muted>
          <GameChart hist={form.hist!} line={r.line} lean={r.lean || 'OVER'} gamesWord={gamesWord} />
        </Card>
      )}

      <Disclaimer />
    </Sheet>
  )
}

const s = StyleSheet.create({
  matchup: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', marginTop: 6 },
  side: { flex: 1, alignItems: 'center', minWidth: 0 },
  who: { fontFamily: F.condBlack, fontSize: 17, color: T.white, textAlign: 'center', marginTop: 8, lineHeight: 19 },
  whoSub: { fontFamily: F.condBold, fontSize: 11, letterSpacing: 0.8, color: T.muted2, marginTop: 2, textTransform: 'uppercase' },
  vs: { width: 38, height: 38, borderRadius: 19, alignItems: 'center', justifyContent: 'center', marginTop: 16,
        backgroundColor: 'rgba(255,255,255,0.06)', borderWidth: 1, borderColor: T.glassLineHi },
  vsText: { fontFamily: F.condHeavy, fontSize: 13, color: T.muted, letterSpacing: 0.6 },
  context: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, marginTop: 12 },
  call: { marginTop: 16, borderWidth: 1, overflow: 'hidden' },
  callSide: { fontFamily: F.condHeavy, fontSize: 26, letterSpacing: 1.2 },
  callLine: { fontFamily: F.condHeavy, fontSize: 32, color: T.white },
  callProp: { fontFamily: F.condBold, fontSize: 15, color: T.muted, flexShrink: 1 },
  threex: { alignSelf: 'flex-start', fontFamily: F.condBold, fontSize: 10, letterSpacing: 1, color: T.amber,
            borderWidth: 1, borderColor: `${T.amber}44`, borderRadius: 5, paddingHorizontal: 5,
            paddingVertical: 1, textTransform: 'uppercase', marginTop: 6 },
  edgeLine: { fontFamily: F.body, fontSize: 12.5, color: T.muted, lineHeight: 17, marginTop: 12 },
  tierRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 12 },
  tierDot: { width: 8, height: 8, borderRadius: 4 },
  tierWord: { fontFamily: F.bodySemi, fontSize: 13.5, color: T.white },
  k: { fontFamily: F.condBold, fontSize: 10.5, letterSpacing: 1.3, textTransform: 'uppercase', color: T.muted2 },
  resultText: { fontFamily: F.body, fontSize: 13.5, color: T.muted, lineHeight: 19, marginTop: 3 },
})
