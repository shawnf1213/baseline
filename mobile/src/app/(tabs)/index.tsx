// Board — the first tab: the live player board, every line the books list for
// the selected sport with Baseline's number beside it. At the top, the
// strongest plays as a swipeable strip and a one-line summary of the slate;
// under it, one card per player (face · best call · confidence ring) that
// drops down to every prop on him, like the website's board. Search, prop
// filter and sort; pull to refresh; placeholders while it loads.
//
// Tennis rows are priced on the backend on a schedule and arrive priced or
// pending; NFL and NBA arrive priced by the bot's scan, every game day still
// to come at once.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FlatList, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { Empty, Icon, Muted, PageTitle, SectionLabel, Segmented, Symbol } from '@/components/ui'
import { SkeletonCard } from '@/components/Skeleton'
import { PlayerGroupCard } from '@/components/PlayerGroupCard'
import { PickSheet } from '@/components/PickSheet'
import { OptionSheet } from '@/components/OptionSheet'
import { SelectField } from '@/components/projectBits'
import { HeaderAccount } from '@/components/HeaderAccount'
import { PlayerAvatar } from '@/components/Avatar'
import { ConfRing } from '@/components/Ring'
import { AmbientGlow, CardGlow } from '@/components/Glow'
import { BoardData, BoardRow, BoardSort, groupBoard, loadBoard } from '@/lib/board'
import { Book, PickRow, fmtLine, fmtSigned, kickoffLabel, normName, prettyDate, propLabel,
         startTimeLabel } from '@/lib/picks'
import { SportKey, SportSwitch, useSport } from '@/lib/sports'
import { F, T, sideTone } from '@/theme'
import { tap } from '@/lib/haptics'

const BOOKS: { key: Book; label: string }[] = [
  { key: 'prizepicks', label: 'PrizePicks' },
  { key: 'underdog', label: 'Underdog' },
]
type Sort = BoardSort
const SORTS: { value: Sort; label: string; sub?: string }[] = [
  { value: 'start', label: 'Start time', sub: 'soonest first' },
  { value: 'confidence', label: 'Confidence', sub: 'highest first' },
  { value: 'edge', label: 'Edge', sub: 'biggest gap to the line first' },
]
const EMPTY: Record<SportKey, string> = {
  tennis: 'has no tennis lines up right now. Check back when matches are near.',
  nfl: 'Lines show up here, priced, as soon as the book lists the next games.',
  nba: 'The board is published in the afternoon on game days.',
  mlb: '',
}

// "Oct 8 – Oct 12 · live lines" — every game day on an NFL/NBA board.
function slateLine(sport: SportKey, b?: BoardData) {
  if (sport === 'tennis' || !b || !b.slates.length) return 'Live lines, priced by Baseline'
  const first = prettyDate(b.slates[0]), last = prettyDate(b.slates[b.slates.length - 1])
  if (b.final) return `${first} · final`
  return `${first === last ? first : `${first} – ${last}`} · live lines`
}

export default function Board() {
  const insets = useSafeAreaInsets()
  const { sport } = useSport()
  const [book, setBook] = useState<Book>('prizepicks')
  const [data, setData] = useState<Record<string, BoardData>>({})
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [prop, setProp] = useState('All')
  const [sort, setSort] = useState<Sort>('start')
  const [sheet, setSheet] = useState<'prop' | 'sort' | null>(null)
  const [open, setOpen] = useState<PickRow | null>(null)
  // The one player card dropped down, like the website's board — opening
  // another closes it.
  const [openKey, setOpenKey] = useState<string | null>(null)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])

  const key = `${sport}|${book}`
  const load = useCallback(async (s: SportKey, b: Book, quiet = false) => {
    if (quiet) setRefreshing(true); else setLoading(true)
    try { const d = await loadBoard(s, b); if (alive.current) { setData(m => ({ ...m, [`${s}|${b}`]: d })); setError(null) } }
    catch { if (alive.current) setError('Could not load the board.') }
    finally { if (alive.current) { setLoading(false); setRefreshing(false) } }
  }, [])
  useEffect(() => {
    setQuery(''); setProp('All'); setSort(sport === 'tennis' ? 'start' : 'confidence'); setOpenKey(null)
    if (data[key] === undefined) load(sport, book); else setLoading(false)
  }, [sport, book])  // eslint-disable-line react-hooks/exhaustive-deps

  const board = data[key]
  const baseRows = board?.rows || []

  // Tennis rows are priced on the backend on a schedule; while a board still
  // has pending rows, poll it every minute so they fill in without a pull.
  useEffect(() => {
    if (sport !== 'tennis' || !board || !board.rows.some(r => r.state === 'pending')) return
    const t = setInterval(() => { if (alive.current) load(sport, book, true) }, 60_000)
    return () => clearInterval(t)
  }, [sport, book, board, load])

  // Filter the props, then fold them into one card per player. Filtering first
  // means "Aces" shows each player's Aces line alone, not his whole card.
  const rows = useMemo(() => {
    let list: BoardRow[] = baseRows
    const q = normName(query)
    if (q) list = list.filter(r => normName(r.player).includes(q) || normName(r.opponent).includes(q)
      || normName(r.team || '').includes(q) || normName(propLabel(sport, r.propType)).includes(q))
    if (prop !== 'All') list = list.filter(r => r.propType === prop)
    return list
  }, [baseRows, query, prop, sport])
  const groups = useMemo(() => groupBoard(rows, sort), [rows, sort])

  // The strongest priced plays on the whole board (not the filtered list), one
  // per player so the strip is a spread of the slate, not one name four times.
  const top = useMemo(() => {
    const seen = new Set<string>()
    return [...baseRows].filter(r => r.state === 'done' && r.confidence != null)
      .sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0))
      .filter(r => { const k = normName(r.player); if (seen.has(k)) return false; seen.add(k); return true })
      .slice(0, 8)
  }, [baseRows])

  const summary = useMemo(() => {
    const done = baseRows.filter(r => r.state === 'done')
    const elite = done.filter(r => (r.confidence ?? 0) >= 80).length
    const strong = done.filter(r => (r.confidence ?? 0) >= 72 && (r.confidence ?? 0) < 80).length
    const best = done.reduce<BoardRow | null>((m, r) => (Math.abs(r.edge ?? 0) > Math.abs(m?.edge ?? -1) ? r : m), null)
    return { priced: done.length, total: baseRows.length, elite, strong, best,
             players: new Set(baseRows.map(r => normName(r.player))).size }
  }, [baseRows])

  const propOptions = useMemo(() => {
    const seen = new Map<string, string>()
    for (const r of baseRows) if (!seen.has(r.propType)) seen.set(r.propType, propLabel(sport, r.propType))
    return [{ value: 'All', label: 'All props' }, ...[...seen.entries()].map(([value, label]) => ({ value, label }))]
  }, [baseRows, sport])

  const bookName = book === 'underdog' ? 'Underdog' : 'PrizePicks'
  const sportWord = sport === 'tennis' ? 'tennis' : sport.toUpperCase()
  const filtering = !!query || prop !== 'All'

  const header = (
    <View>
      <PageTitle sub={slateLine(sport, board)} right={<HeaderAccount />}>Board</PageTitle>
      <SportSwitch />
      <Segmented options={BOOKS} value={book} onChange={setBook} compact />

      {!loading && !error && baseRows.length ? (
        <>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingRight: 8 }}
                      style={{ marginBottom: 4 }}>
            <Stat icon="list.bullet" label={sport === 'tennis' ? `${summary.priced}/${summary.total} priced` : `${summary.total} lines`} />
            <Stat icon="person.2.fill" label={`${summary.players} players`} />
            {summary.elite ? <Stat icon="flame.fill" tone={T.green} label={`${summary.elite} elite`} /> : null}
            {summary.strong ? <Stat icon="bolt.fill" tone={T.green} label={`${summary.strong} strong`} /> : null}
            {summary.best?.edge != null ? <Stat icon="arrow.up.right" tone={sideTone(summary.best.lean).tone}
                                                label={`best edge ${fmtSigned(summary.best.edge)}`} /> : null}
          </ScrollView>

          {top.length && !filtering ? (
            <>
              <SectionLabel right={`${top.length} plays`}>Top plays</SectionLabel>
              <FlatList horizontal data={top} keyExtractor={r => `top-${r.key}`} showsHorizontalScrollIndicator={false}
                        contentContainerStyle={{ gap: 10, paddingHorizontal: 16 }} style={{ marginHorizontal: -16 }}
                        renderItem={({ item }) => <TopCard r={item} onPress={setOpen} />} />
            </>
          ) : null}
          <SectionLabel right={`${groups.length} ${groups.length === 1 ? 'player' : 'players'} · ${rows.length} ${rows.length === 1 ? 'line' : 'lines'}`}>
            {filtering ? 'Matches' : 'Full board'}
          </SectionLabel>
        </>
      ) : null}

      <View style={s.searchWrap}>
        <Icon name="magnifyingglass" size={15} color={T.muted2} />
        <TextInput value={query} onChangeText={setQuery} placeholder={`Search ${sportWord} players or props`}
                   placeholderTextColor={T.muted2} autoCorrect={false} autoCapitalize="none"
                   clearButtonMode="while-editing" style={s.search} returnKeyType="search" />
      </View>
      <View style={{ flexDirection: 'row', gap: 10, marginTop: 10, marginBottom: 12 }}>
        <SelectField label="Prop" value={propOptions.find(o => o.value === prop)?.label || 'All props'}
                     onPress={() => setSheet('prop')} style={{ flex: 1, minHeight: 50 }} />
        <SelectField label="Sort" value={SORTS.find(o => o.value === sort)?.label || 'Start time'}
                     onPress={() => setSheet('sort')} style={{ flex: 1, minHeight: 50 }} />
      </View>
      {!loading && !error && sport === 'tennis' && baseRows.some(r => r.state === 'pending') ? (
        <Muted size={11.5} style={{ marginBottom: 10 }}>Baseline is pricing the rest of the board — rows fill in on their own.</Muted>
      ) : null}
    </View>
  )

  const body = loading
    ? <View>{[0, 1, 2, 3, 4, 5].map(i => <SkeletonCard key={i} />)}</View>
    : error ? <Empty title="Couldn't load the board" hint={`${error} Pull down to try again.`} />
    : board && !board.available ? <Empty title={`${bookName} didn't answer`} hint="The book's feed could not be read just now. Pull down to try again." />
    : !baseRows.length ? <Empty title={`No ${sportWord} lines on ${bookName}`} hint={sport === 'tennis' ? `${bookName} ${EMPTY.tennis}` : EMPTY[sport]} />
    : !rows.length ? <Empty title="Nothing matches" hint="Try another search or clear the prop filter." />
    : null

  return (
    <View style={{ flex: 1, backgroundColor: T.bg }}>
      <AmbientGlow />
      <FlatList
        data={body ? [] : groups}
        keyExtractor={g => g.key}
        extraData={openKey}
        renderItem={({ item: g }) => (
          <PlayerGroupCard g={g} open={openKey === g.key} onOpen={setOpen}
                           onToggle={() => setOpenKey(k => (k === g.key ? null : g.key))} />
        )}
        ListHeaderComponent={header}
        ListEmptyComponent={body}
        ListFooterComponent={!body ? (
          <View>
            <Muted size={11} style={{ textAlign: 'center', marginTop: 12, lineHeight: 16 }}>
              Live {bookName} lines with Baseline's projection. Edge is projection minus line.
              {sport === 'nfl' ? ' ⭐ marks the Pick of the Day.' : sport === 'nba' ? " ⭐ marks the board's starred play." : ''}
              {book === 'underdog' ? ' Multiplier and one-sided lines are left out.' : ''}
            </Muted>
            <Muted size={10.5} style={{ textAlign: 'center', marginTop: 10 }}>Projections are for informational purposes only.</Muted>
          </View>
        ) : null}
        contentContainerStyle={{ paddingHorizontal: 16, paddingTop: insets.top + T.s3, paddingBottom: T.s5 }}
        keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag"
        initialNumToRender={10} windowSize={7} removeClippedSubviews
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load(sport, book, true)}
                                        tintColor={T.green} colors={[T.green]} />}
      />
      <PickSheet pick={open} onClose={() => setOpen(null)} />
      <OptionSheet open={sheet === 'prop'} title="Prop" options={propOptions} value={prop}
                   onSelect={setProp} onClose={() => setSheet(null)} />
      <OptionSheet open={sheet === 'sort'} title="Sort by" options={SORTS} value={sort}
                   onSelect={setSort} onClose={() => setSheet(null)} />
    </View>
  )
}

function Stat({ icon, label, tone = T.muted }: { icon: Symbol; label: string; tone?: string }) {
  return (
    <View style={[s.stat, { borderColor: `${tone}40` }]}>
      <Icon name={icon} size={12} color={tone} />
      <Text style={[s.statText, { color: tone === T.muted ? T.white : tone }]}>{label}</Text>
    </View>
  )
}

function TopCard({ r, onPress }: { r: BoardRow; onPress: (r: PickRow) => void }) {
  const side = sideTone(r.lean)
  const when = r.sport === 'tennis' ? startTimeLabel(r.startTs) : kickoffLabel(r.startsAt)
  return (
    <Pressable onPress={() => { tap(); onPress(r) }} style={({ pressed }) => [pressed && { transform: [{ scale: 0.97 }] }]}>
      <View style={[s.top, { borderColor: `${side.tone}55` }]}>
        <CardGlow color={side.tone} strength={0.3} />
        <View style={{ flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between' }}>
          <PlayerAvatar sport={r.sport} name={r.player} size={54} ring={side.tone} team={r.team} />
          <ConfRing conf={r.confidence} size={46} />
        </View>
        <Text style={s.topName} numberOfLines={1}>{r.player}</Text>
        <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 5, marginTop: 2 }}>
          <Text style={[s.topSide, { color: side.tone }]}>{r.lean || '—'}</Text>
          <Text style={s.topLine}>{fmtLine(r.line)}</Text>
          <Text style={s.topProp} numberOfLines={1}>{propLabel(r.sport, r.propType)}</Text>
        </View>
        <View style={s.topFoot}>
          <Text style={s.topMeta} numberOfLines={1}>vs {r.opponent}</Text>
          {r.edge != null ? <Text style={[s.topEdge, { color: side.tone }]}>{fmtSigned(r.edge)}</Text> : null}
        </View>
        {when ? <Text style={[s.topMeta, { marginTop: 2 }]} numberOfLines={1}>{when}</Text> : null}
      </View>
    </Pressable>
  )
}

const s = StyleSheet.create({
  searchWrap: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14, minHeight: 48,
                backgroundColor: 'rgba(255,255,255,0.045)', borderWidth: 1, borderColor: T.glassLine,
                borderRadius: T.r2 },
  search: { flex: 1, color: T.white, fontFamily: F.body, fontSize: 16, minHeight: 46 },
  stat: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingVertical: 7,
          borderRadius: 999, borderWidth: 1, backgroundColor: 'rgba(255,255,255,0.035)' },
  statText: { fontFamily: F.condBold, fontSize: 12, letterSpacing: 0.6 },
  top: { width: 210, padding: 14, borderRadius: T.r3, borderWidth: 1, overflow: 'hidden',
         backgroundColor: 'rgba(255,255,255,0.03)' },
  topName: { fontFamily: F.condBlack, fontSize: 18, color: T.white, marginTop: 10 },
  topSide: { fontFamily: F.condHeavy, fontSize: 14, letterSpacing: 0.8 },
  topLine: { fontFamily: F.condHeavy, fontSize: 17, color: T.white },
  topProp: { fontFamily: F.condBold, fontSize: 13, color: T.muted, flexShrink: 1 },
  topFoot: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 8, gap: 6 },
  topMeta: { fontFamily: F.body, fontSize: 11.5, color: T.muted2, flexShrink: 1 },
  topEdge: { fontFamily: F.condHeavy, fontSize: 14 },
})
