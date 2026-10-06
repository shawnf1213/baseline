// Board — the live player board: every line the books list for the selected
// sport, with Baseline's number beside it. Compact rows (player · side · line
// · prop · confidence), a tap opens the full sheet. Search, a prop filter and
// a sort live above the list; pull to refresh; placeholders while it loads.
//
// Tennis rows arrive unpriced and are priced on the phone a few at a time
// (lib/board), so the confidence column fills in as the backend answers. NFL
// and NBA arrive priced by the bot's scan.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FlatList, RefreshControl, StyleSheet, TextInput, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { Empty, Muted, PageTitle, Segmented } from '@/components/ui'
import { SkeletonCard } from '@/components/Skeleton'
import { PickCard } from '@/components/PickCard'
import { PickSheet } from '@/components/PickSheet'
import { OptionSheet } from '@/components/OptionSheet'
import { SelectField } from '@/components/projectBits'
import { BoardRow, loadBoard } from '@/lib/board'
import { Book, PickRow, kickoffLabel, normName, prettyDate, propLabel, startTimeLabel } from '@/lib/picks'
import { SportKey, SportSwitch, useSport } from '@/lib/sports'
import { F, T } from '@/theme'

const BOOKS: { key: Book; label: string }[] = [
  { key: 'prizepicks', label: 'PrizePicks' },
  { key: 'underdog', label: 'Underdog' },
]
type Sort = 'start' | 'confidence' | 'edge'
const SORTS: { value: Sort; label: string; sub?: string }[] = [
  { value: 'start', label: 'Start time', sub: 'soonest first' },
  { value: 'confidence', label: 'Confidence', sub: 'highest first' },
  { value: 'edge', label: 'Edge', sub: 'biggest gap to the line first' },
]
const EMPTY: Record<SportKey, string> = {
  tennis: 'has no tennis lines up right now. Check back when matches are near.',
  nfl: 'The board refreshes through the day; Sunday slates load Friday.',
  nba: 'The board is published in the afternoon on game days.',
  mlb: '',
}

export default function Board() {
  const insets = useSafeAreaInsets()
  const { sport } = useSport()
  const [book, setBook] = useState<Book>('prizepicks')
  const [data, setData] = useState<Record<string, { rows: BoardRow[]; available: boolean; slate: string | null }>>({})
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [prop, setProp] = useState('All')
  const [sort, setSort] = useState<Sort>('start')
  const [sheet, setSheet] = useState<'prop' | 'sort' | null>(null)
  const [open, setOpen] = useState<PickRow | null>(null)
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
    setQuery(''); setProp('All'); setSort(sport === 'tennis' ? 'start' : 'confidence')
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

  const rows = useMemo(() => {
    let list: BoardRow[] = baseRows
    const q = normName(query)
    if (q) list = list.filter(r => normName(r.player).includes(q) || normName(r.opponent).includes(q)
      || normName(r.team || '').includes(q) || normName(propLabel(sport, r.propType)).includes(q))
    if (prop !== 'All') list = list.filter(r => r.propType === prop)
    if (sort === 'confidence') list = [...list].sort((a, b) => (b.confidence ?? -1) - (a.confidence ?? -1))
    else if (sort === 'edge') list = [...list].sort((a, b) => Math.abs(b.edge ?? -1) - Math.abs(a.edge ?? -1))
    else if (sport === 'tennis') list = [...list].sort((a, b) => (a.startTs ?? Infinity) - (b.startTs ?? Infinity))
    return list
  }, [baseRows, query, prop, sort, sport])

  const propOptions = useMemo(() => {
    const seen = new Map<string, string>()
    for (const r of baseRows) if (!seen.has(r.propType)) seen.set(r.propType, propLabel(sport, r.propType))
    return [{ value: 'All', label: 'All props' }, ...[...seen.entries()].map(([value, label]) => ({ value, label }))]
  }, [baseRows, sport])

  const priced = rows.filter(r => r.state === 'done').length
  const players = new Set(rows.map(r => normName(r.player))).size
  const bookName = book === 'underdog' ? 'Underdog' : 'PrizePicks'
  const sportWord = sport === 'tennis' ? 'tennis' : sport.toUpperCase()

  const header = (
    <View>
      <PageTitle sub={board?.slate && sport !== 'tennis' ? `${prettyDate(board.slate)} slate` : 'Live lines, priced by Baseline'}>Board</PageTitle>
      <SportSwitch />
      <Segmented options={BOOKS} value={book} onChange={setBook} compact />
      <TextInput value={query} onChangeText={setQuery} placeholder={`Search ${sportWord} players`}
                 placeholderTextColor={T.muted2} autoCorrect={false} autoCapitalize="none"
                 clearButtonMode="while-editing" style={s.search} returnKeyType="search" />
      <View style={{ flexDirection: 'row', gap: 10, marginTop: 10 }}>
        <SelectField label="Prop" value={propOptions.find(o => o.value === prop)?.label || 'All props'}
                     onPress={() => setSheet('prop')} style={{ flex: 1, minHeight: 50 }} />
        <SelectField label="Sort" value={SORTS.find(o => o.value === sort)?.label || 'Start time'}
                     onPress={() => setSheet('sort')} style={{ flex: 1, minHeight: 50 }} />
      </View>
      {!loading && !error && rows.length ? (
        <Muted size={11.5} style={{ marginTop: 12, marginBottom: 8 }}>
          {sport === 'tennis'
            ? `${priced}${priced < rows.length ? ` of ${rows.length}` : ''} priced · ${players} players${rows.some(r => r.state === 'pending') ? ' · Baseline is pricing the rest' : ''}`
            : `${rows.length} lines · ${players} players`}
        </Muted>
      ) : <View style={{ height: 12 }} />}
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
      <FlatList
        data={body ? [] : rows}
        keyExtractor={r => r.key}
        renderItem={({ item: r }) => (
          <PickCard r={r} onPress={setOpen} pricing={r.state === 'pending'} noData={r.state === 'nodata' || r.state === 'started'}
                    sub={r.sport === 'tennis' ? startTimeLabel(r.startTs) : kickoffLabel(r.startsAt)} />
        )}
        ListHeaderComponent={header}
        ListEmptyComponent={body}
        ListFooterComponent={!body ? (
          <View>
            <Muted size={11} style={{ textAlign: 'center', marginTop: 12, lineHeight: 16 }}>
              Live {bookName} lines with Baseline's projection. Edge is projection minus line.
              {sport !== 'tennis' ? ' ⭐ marks a posted play.' : ''}
              {book === 'underdog' ? ' Multiplier and one-sided lines are left out.' : ''}
            </Muted>
            <Muted size={10.5} style={{ textAlign: 'center', marginTop: 10 }}>Projections are for informational purposes only.</Muted>
          </View>
        ) : null}
        contentContainerStyle={{ paddingHorizontal: 16, paddingTop: insets.top + T.s3, paddingBottom: T.s5 }}
        keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag"
        initialNumToRender={12} windowSize={7} removeClippedSubviews
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

const s = StyleSheet.create({
  search: { backgroundColor: 'rgba(255,255,255,0.04)', borderWidth: 1, borderColor: T.glassLine,
            borderRadius: T.r1, paddingHorizontal: 14, minHeight: 48, color: T.white, fontFamily: F.body,
            fontSize: 16 },
})
