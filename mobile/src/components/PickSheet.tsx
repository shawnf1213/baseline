// A pick's full story, in a sheet: the call, the projection against the line,
// confidence, the result, and the player's recent form for that prop. The
// card shows the verdict; this is where the reader checks it.
//
// Record rows carry names, not Sofascore ids, so recent form is fetched
// lazily: resolve the player by name, then /api/history for this prop,
// surface and line. Both steps are cancelled if the sheet closes first.
import { useEffect, useRef, useState } from 'react'
import { StyleSheet, Text, View } from 'react-native'
import { router } from 'expo-router'
import { Sheet } from './Sheet'
import { Button, Card, Muted } from './ui'
import { Skeleton } from './Skeleton'
import { EdgeScale, GameChart, HitWindows, Meter } from './charts'
import { ResultBadge } from './PickCard'
import { F, T, sideTone, tier } from '@/theme'
import { fetchHistory } from '@/lib/api'
import { Found, Hist, PickRow, fmt, fmtLine, fmtSigned, prettyDate, propHasHistory, propUnit,
         resolvePlayer, resultMeta, shapeHistory, shortProp } from '@/lib/picks'
import { tap } from '@/lib/haptics'

const TIER_WORD: Record<string, string> = {
  ELITE: 'Elite — the model\'s strongest band',
  STRONG: 'Strong',
  LEAN: 'Lean — a smaller edge',
  '': 'Below the posting bar',
}

type Form = { state: 'loading' | 'ready' | 'none' | 'error'; hist?: Hist; who?: Found | null }

export function PickSheet({ pick, onClose }: { pick: PickRow | null; onClose: () => void }) {
  const [form, setForm] = useState<Form>({ state: 'loading' })
  const [opp, setOpp] = useState<Found | null>(null)
  const alive = useRef(0)

  useEffect(() => {
    const token = ++alive.current
    setOpp(null)
    if (!pick) return
    if (!propHasHistory(pick.propType)) { setForm({ state: 'none' }); return }
    setForm({ state: 'loading' })
    ;(async () => {
      const who = await resolvePlayer(pick.player, pick.tour)
      if (alive.current !== token) return
      if (!who) { setForm({ state: 'error' }); return }
      try {
        const h = await fetchHistory(who.id, who.tour, pick.propType, pick.surface, pick.line ?? 0)
        if (alive.current !== token) return
        setForm({ state: 'ready', hist: shapeHistory(h), who })
      } catch { if (alive.current === token) setForm({ state: 'error', who }) }
      // The opponent id is only needed for the Project hand-off; resolved in
      // the background and never blocks the sheet.
      resolvePlayer(pick.opponent, who.tour).then(o => { if (alive.current === token) setOpp(o) })
    })()
  }, [pick?.key])   // eslint-disable-line react-hooks/exhaustive-deps

  if (!pick) return <Sheet open={false} onClose={onClose}><View /></Sheet>
  const r = pick
  const side = sideTone(r.lean)
  const tr = tier(r.confidence)
  const meta = resultMeta(r.result)
  const resTone = meta.tone === 'win' ? T.green : meta.tone === 'loss' ? T.red : meta.tone === 'void' ? T.muted2 : T.amber
  const who = form.who || null

  const openProject = () => {
    tap()
    onClose()
    router.push({ pathname: '/project', params: {
      player: r.player, opponent: r.opponent,
      playerId: who?.id || '', opponentId: opp?.id || '',
      tour: who?.tour || (r.tour === 'WTA' ? 'WTA' : 'ATP'),
      surface: r.surface || 'Hard', court: r.tournament || '',
      prop: r.propType, line: r.line != null ? String(r.line) : '',
      t: String(Date.now()),
    } })
  }

  return (
    <Sheet open={!!pick} onClose={onClose}
           title={`${prettyDate(r.date)} · ${r.book === 'underdog' ? 'Underdog' : 'PrizePicks'}${r.isPotd ? ' · ⭐' : ''}`}
           footer={<Button label="Project this matchup" onPress={openProject} kind="ghost" />}>
      <Text style={s.name}>{r.player}{r.playerRank ? <Text style={s.rank}>  #{r.playerRank}</Text> : null}</Text>
      <Text style={s.vs}>vs {r.opponent}{r.opponentRank ? ` (#${r.opponentRank})` : ''}</Text>
      <Muted size={12.5} style={{ marginTop: 2 }}>
        {[r.tournament, r.surface].filter(Boolean).join(' · ') || 'Tournament not recorded'}
      </Muted>

      {/* THE CALL */}
      <Card style={[s.call, { borderColor: `${side.tone}44` }]}>
        <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 8 }}>
          <Text style={[s.callSide, { color: side.tone }]}>{r.lean || '—'}</Text>
          <Text style={s.callLine}>{fmtLine(r.line)}</Text>
          <Text style={s.callProp}>{shortProp(r.propType)}</Text>
          {r.isThreeX ? <Text style={s.threex}>3x slip</Text> : null}
        </View>
        <View style={{ marginTop: 14 }}>
          <EdgeScale line={r.line} proj={r.projection} tone={side.tone} />
        </View>
        <Text style={s.edgeLine}>
          Edge <Text style={{ color: side.tone, fontFamily: F.bodySemi }}>{fmtSigned(r.edge)} {propUnit(r.propType)}</Text>
          {' '}between Baseline's number and the book's line.
        </Text>
      </Card>

      {/* CONFIDENCE */}
      <Card style={{ marginTop: 10 }}>
        <View style={{ flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between' }}>
          <View>
            <Text style={s.k}>Confidence</Text>
            <Text style={s.tierWord}>{TIER_WORD[tr.label]}</Text>
          </View>
          <Text style={[s.confNum, { color: tr.tone }]}>{r.confidence != null ? Math.round(r.confidence) : '—'}</Text>
        </View>
        <View style={{ marginTop: 10 }}><Meter pct={r.confidence} tone={tr.tone} /></View>
        <Muted size={11.5} style={{ marginTop: 8, lineHeight: 16 }}>
          The model's own score for this play, 0–100. The same number the Discord card shows.
        </Muted>
      </Card>

      {/* RESULT */}
      <Card style={{ marginTop: 10, borderColor: `${resTone}33` }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={s.k}>Result</Text>
            {meta.tone === 'pending' ? (
              <Text style={s.resultText}>Not graded yet — results post after the match finishes.</Text>
            ) : meta.tone === 'void' ? (
              <Text style={s.resultText}>{meta.label} — this play did not count (walkover, retirement or a line that changed).</Text>
            ) : (
              <Text style={s.resultText}>
                {r.player.split(' ').slice(-1)[0]} finished with{' '}
                <Text style={{ color: T.white, fontFamily: F.bodySemi }}>
                  {r.resultValue != null ? `${fmtLine(r.resultValue)} ${propUnit(r.propType)}` : 'a graded result'}
                </Text>
                {r.resultValue != null && r.line != null ? ` against a line of ${fmtLine(r.line)}.` : '.'}
              </Text>
            )}
          </View>
          {meta.tone !== 'pending' ? <ResultBadge r={r} big /> : null}
        </View>
      </Card>

      {/* RECENT FORM */}
      <Text style={[s.k, { marginTop: 22, marginBottom: 8 }]}>Recent form · {shortProp(r.propType)}</Text>
      {form.state === 'loading' ? (
        <Card>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            {[0, 1, 2].map(i => <Skeleton key={i} h={58} r={10} style={{ flex: 1 }} />)}
          </View>
          <Skeleton h={110} r={10} style={{ marginTop: 12 }} />
          <Muted size={11} style={{ marginTop: 10 }}>Pulling {r.player.split(' ').slice(-1)[0]}'s recent matches…</Muted>
        </Card>
      ) : form.state === 'none' ? (
        <Card><Muted size={12.5} style={{ lineHeight: 17 }}>
          {shortProp(r.propType)} is built from several stats, so there is no single per-match
          log to show. Use Project this matchup for the full breakdown.
        </Muted></Card>
      ) : form.state === 'error' ? (
        <Card><Muted size={12.5}>Couldn't load recent matches right now.</Muted></Card>
      ) : (
        <Card>
          <HitWindows hist={form.hist!} lean={r.lean} line={r.line} />
          <Muted size={11} style={{ marginTop: 8, marginBottom: 14 }}>
            How often {r.player.split(' ').slice(-1)[0]} finished {r.lean === 'UNDER' ? 'under' : 'over'} {fmtLine(r.line)}
            {r.surface ? ` on ${r.surface.toLowerCase()}` : ''}
            {form.hist!.average != null ? ` · season average ${fmt(form.hist!.average)}` : ''}
          </Muted>
          <GameChart hist={form.hist!} line={r.line} lean={r.lean} />
        </Card>
      )}

      <Muted size={10.5} style={{ textAlign: 'center', marginTop: 18 }}>
        Model projections, not betting advice.
      </Muted>
    </Sheet>
  )
}

const s = StyleSheet.create({
  name: { fontFamily: F.condHeavy, fontSize: 28, lineHeight: 31, color: T.white, marginTop: 4 },
  rank: { fontFamily: F.condBold, fontSize: 15, color: T.muted2 },
  vs: { fontFamily: F.bodyMed, fontSize: 15, color: T.muted, marginTop: 2 },
  call: { marginTop: 16, borderWidth: 1 },
  callSide: { fontFamily: F.condHeavy, fontSize: 24, letterSpacing: 1.2 },
  callLine: { fontFamily: F.condHeavy, fontSize: 30, color: T.white },
  callProp: { fontFamily: F.condBold, fontSize: 15, color: T.muted, flexShrink: 1 },
  threex: { fontFamily: F.condBold, fontSize: 10, letterSpacing: 1, color: T.amber,
            borderWidth: 1, borderColor: `${T.amber}44`, borderRadius: 5, paddingHorizontal: 5,
            paddingVertical: 1, textTransform: 'uppercase', marginLeft: 'auto' },
  edgeLine: { fontFamily: F.body, fontSize: 12.5, color: T.muted, lineHeight: 17, marginTop: 12 },
  k: { fontFamily: F.condBold, fontSize: 10.5, letterSpacing: 1.3, textTransform: 'uppercase', color: T.muted2 },
  tierWord: { fontFamily: F.bodyMed, fontSize: 14, color: T.white, marginTop: 3 },
  confNum: { fontFamily: F.condHeavy, fontSize: 36, lineHeight: 38 },
  resultText: { fontFamily: F.body, fontSize: 13.5, color: T.muted, lineHeight: 19, marginTop: 3 },
})
