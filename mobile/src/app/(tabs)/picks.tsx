// Picks — Baseline's released boards. The Pick of the Day first, then the
// rest of the latest board, then the month so far and earlier boards with
// their results. Every card is the short version; a tap opens the sheet with
// everything. The sport switch at the top is shared with Board and Project.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { StyleSheet, Text, View } from 'react-native'
import { Screen } from '@/components/Screen'
import { Button, Card, Disclaimer, Empty, Muted, PageTitle, SectionLabel, Segmented } from '@/components/ui'
import { SkeletonCard } from '@/components/Skeleton'
import { PickCard } from '@/components/PickCard'
import { PickSheet } from '@/components/PickSheet'
import { HeaderAccount } from '@/components/HeaderAccount'
import { Ring } from '@/components/Ring'
import { CardGlow } from '@/components/Glow'
import { Book, PickDay, PickRow, derivePicksFor, etHour, etMonthLabel, etToday, loadPicksRaw,
         monthRecord, prettyDate } from '@/lib/picks'
import { SportKey, SportSwitch, useSport } from '@/lib/sports'
import { F, T } from '@/theme'

const BOOKS: { key: Book; label: string }[] = [
  { key: 'prizepicks', label: 'PrizePicks' },
  { key: 'underdog', label: 'Underdog' },
]
const SUB: Record<SportKey, string> = {
  tennis: "Baseline's released tennis board, graded in public",
  nfl: "Baseline's released NFL board, graded in public",
  nba: "Baseline's released NBA board, graded in public",
  mlb: "Baseline's released MLB board",
}
const WHEN: Record<SportKey, string> = {
  tennis: 'The tennis board posts around 3 PM ET each day.',
  nfl: 'NFL boards post the evening before game day.',
  nba: 'NBA boards post in the late afternoon on game days.',
  mlb: '',
}

export default function Picks() {
  const { sport } = useSport()
  const [raw, setRaw] = useState<Record<string, any>>({})
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [book, setBook] = useState<Book>('prizepicks')
  const [open, setOpen] = useState<PickRow | null>(null)
  const [moreDays, setMoreDays] = useState(0)

  const load = useCallback(async (s: SportKey, quiet = false) => {
    if (quiet) setRefreshing(true); else setLoading(true)
    try { const d = await loadPicksRaw(s); setRaw(m => ({ ...m, [s]: d })); setError(null) }
    catch { setError('Could not load the pick log.') }
    finally { setLoading(false); setRefreshing(false) }
  }, [])
  useEffect(() => { setMoreDays(0); if (raw[sport] === undefined) load(sport); else setLoading(false) }, [sport])  // eslint-disable-line react-hooks/exhaustive-deps

  const days = useMemo<PickDay[]>(() => derivePicksFor(sport, raw[sport], book), [sport, raw, book])
  const month = useMemo(() => monthRecord(days), [days])
  const today = etToday()
  const latest = days[0] || null
  const earlier = days.slice(1, 1 + 5 + moreDays)
  const hidden = Math.max(0, days.length - 1 - earlier.length)
  const bookName = book === 'underdog' ? 'Underdog' : 'PrizePicks'
  const sportName = sport.toUpperCase() === 'TENNIS' ? 'tennis' : sport.toUpperCase()

  return (
    <Screen refreshing={refreshing} onRefresh={() => load(sport, true)}>
      <PageTitle sub={SUB[sport]} right={<HeaderAccount />}>Picks</PageTitle>
      <SportSwitch />
      <Segmented options={BOOKS} value={book} onChange={b => { setBook(b); setMoreDays(0) }} compact />

      {loading ? (
        <>
          <SkeletonCard hero />
          <SkeletonCard /><SkeletonCard /><SkeletonCard />
        </>
      ) : error ? (
        <Empty title="Couldn't load picks" hint={`${error} Pull down to try again.`} />
      ) : !latest ? (
        <Empty title={`No ${bookName} ${sportName} picks yet`}
               hint={`${WHEN[sport]} Picks appear here with their results.`} />
      ) : (
        <>
          {/* LATEST BOARD */}
          <SectionLabel first right={<DayTally d={latest} />}>
            {latest.date === today ? 'Today' : latest.date > today ? `${prettyDate(latest.date)} · upcoming`
              : `Latest board · ${prettyDate(latest.date)}`}
          </SectionLabel>
          {latest.date < today ? (
            <Muted size={12} style={{ marginBottom: 10 }}>
              {sport === 'tennis' && etHour() < 15 ? "Today's board posts around 3 PM ET." : WHEN[sport]}
            </Muted>
          ) : null}
          {latest.star ? <PickCard r={latest.star} hero onPress={setOpen} />
            : latest.rows.length ? (
              // Said, not implied — the line Discord posts when the slot is empty.
              <Muted size={12} style={{ marginBottom: 10 }}>No Pick of the Day on this board. No play qualified for the ⭐ slot.</Muted>
            ) : null}
          {latest.rest.map(r => <PickCard key={r.key} r={r} onPress={setOpen} />)}

          {/* MONTH SO FAR */}
          {month.decided > 0 ? (
            <Card style={s.month}>
              <CardGlow color={(month.winRate ?? 0) >= 50 ? T.green : T.red} strength={0.16} corner="tr" />
              <View style={{ flex: 1 }}>
                <Text style={s.monthK}>{etMonthLabel()} so far · {bookName}</Text>
                <Text style={s.monthBig}>{month.wins}–{month.losses}</Text>
                <Text style={s.monthSub}>won – lost · pushes count as wins</Text>
              </View>
              <Ring value={month.winRate} size={74} stroke={6} tone={(month.winRate ?? 0) >= 50 ? T.green : T.red}>
                <Text style={s.monthRate}>{month.winRate != null ? `${Math.round(month.winRate)}%` : '—'}</Text>
              </Ring>
            </Card>
          ) : null}

          {/* EARLIER */}
          {earlier.map(d => (
            <View key={d.date}>
              <SectionLabel right={<DayTally d={d} />}>{prettyDate(d.date)}</SectionLabel>
              {d.rows.map(r => <PickCard key={r.key} r={r} onPress={setOpen} />)}
            </View>
          ))}
          {hidden > 0 ? (
            <Button label={`Show ${Math.min(hidden, 10)} more ${Math.min(hidden, 10) === 1 ? 'day' : 'days'}`}
                    kind="ghost" onPress={() => setMoreDays(n => n + 10)} style={{ marginTop: 6 }} />
          ) : null}
        </>
      )}

      <Disclaimer style={{ marginTop: 24 }} />
      <PickSheet pick={open} onClose={() => setOpen(null)} />
    </Screen>
  )
}

function DayTally({ d }: { d: PickDay }) {
  const txt = d.pending > 0 ? `${d.pending} pending`
    : d.winRate != null ? `${d.wins}–${d.losses} · ${d.winRate}%` : '—'
  return <Text style={[s.tally, { color: d.pending > 0 ? T.amber : T.muted }]}>{txt}</Text>
}

const s = StyleSheet.create({
  tally: { fontFamily: F.condBold, fontSize: 11.5, letterSpacing: 0.8, textTransform: 'uppercase' },
  month: { flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 6, paddingVertical: 14, overflow: 'hidden' },
  monthK: { fontFamily: F.condBold, fontSize: 11, letterSpacing: 1.4, textTransform: 'uppercase', color: T.muted2 },
  monthBig: { fontFamily: F.condHeavy, fontSize: 30, color: T.white, marginTop: 4 },
  monthSub: { fontFamily: F.body, fontSize: 11.5, color: T.muted, marginTop: 1 },
  monthRate: { fontFamily: F.condHeavy, fontSize: 21, color: T.white },
})
