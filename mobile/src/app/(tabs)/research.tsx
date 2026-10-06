// Research — players, search and saved players in one tab (the website's
// Players, Search and Saved, merged). Search any player in the selected
// sport, open the player sheet, save the ones you follow; saved and recently
// viewed players sit above the search results for the sport you're on.
import { useEffect, useState } from 'react'
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native'
import { Screen } from '@/components/Screen'
import { Empty, Muted, PageTitle, PersonRow, SectionLabel, Segmented } from '@/components/ui'
import { Skeleton } from '@/components/Skeleton'
import { PlayerRef, PlayerSheet } from '@/components/PlayerSheet'
import { searchNba, searchNfl } from '@/lib/api'
import { usePlayerSearch } from '@/lib/usePlayerSearch'
import { SportKey, SportSwitch, useSport } from '@/lib/sports'
import { SavedPlayer, useRecent, useSaved } from '@/lib/saved'
import { F, T } from '@/theme'
import { tap } from '@/lib/haptics'

type Tour = 'ATP' | 'WTA'
const TOURS: { key: Tour; label: string }[] = [{ key: 'ATP', label: 'ATP' }, { key: 'WTA', label: 'WTA' }]
type Hit = { name: string; id?: string | null; tour?: string; team?: string | null; rank?: number | null; position?: string | null; games?: number | null }

export default function Research() {
  const { sport } = useSport()
  const [tour, setTour] = useState<Tour>('ATP')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<PlayerRef | null>(null)
  const { list: saved, has, toggle } = useSaved()
  const { list: recent, clear } = useRecent()
  const tennis = usePlayerSearch(tour)
  const team = useTeamSearch(sport, q)

  useEffect(() => { setQ(''); tennis.setQuery('') }, [sport])  // eslint-disable-line react-hooks/exhaustive-deps
  const query = sport === 'tennis' ? tennis.query : q
  const setQuery = (v: string) => { setQ(v); if (sport === 'tennis') tennis.setQuery(v) }
  const results: Hit[] = sport === 'tennis'
    ? tennis.results.map(r => ({ name: r.name, id: r.id, tour: r.gender === 'F' ? 'WTA' : r.gender === 'M' ? 'ATP' : tour, rank: r.currentRank ?? null }))
    : team.results
  const loading = sport === 'tennis' ? tennis.loading : team.loading
  const error = sport === 'tennis' ? tennis.error : team.error
  const minChars = sport === 'tennis' ? 3 : 2
  const searching = query.trim().length >= minChars

  const openHit = (h: Hit) => setOpen({ sport, name: h.name, id: h.id, tour: h.tour, team: h.team, rank: h.rank, position: h.position })
  const openSaved = (p: SavedPlayer) => setOpen({ sport: p.sport, name: p.name, id: p.id, tour: p.tour, team: p.team, rank: p.rank })
  const mySaved = saved.filter(p => p.sport === sport)
  const myRecent = recent.filter(p => p.sport === sport && !has(p.key)).slice(0, 6)
  const sportWord = sport === 'tennis' ? 'tennis' : sport.toUpperCase()

  return (
    <Screen>
      <PageTitle sub="Any player, their form, and the props on them">Research</PageTitle>
      <SportSwitch />
      {sport === 'tennis' ? <Segmented options={TOURS} value={tour} onChange={setTour} compact /> : null}
      <TextInput value={query} onChangeText={setQuery} placeholder={`Search ${sportWord} players`}
                 placeholderTextColor={T.muted2} autoCorrect={false} autoCapitalize="words"
                 clearButtonMode="while-editing" style={s.search} returnKeyType="search" />

      {searching ? (
        <>
          <SectionLabel right={loading ? 'searching…' : results.length ? `${results.length} found` : undefined}>Results</SectionLabel>
          {loading && !results.length ? <View style={{ gap: 8 }}>{[0, 1, 2].map(i => <Skeleton key={i} h={60} r={14} />)}</View>
            : error ? <Muted size={12.5}>{error}</Muted>
            : !results.length ? <Empty title="No players found" hint={sport === 'tennis' ? 'Try a different spelling or the other tour.' : 'Try a different spelling.'} />
            : results.slice(0, 12).map(h => (
              <PersonRow key={`${h.name}|${h.team || h.id || ''}`} name={h.name}
                         meta={[h.tour, h.rank ? `#${h.rank}` : null, h.position, h.team, h.games != null ? `${h.games} games` : null].filter(Boolean).join(' · ')}
                         onPress={() => openHit(h)} />
            ))}
        </>
      ) : (
        <>
          <SectionLabel right={mySaved.length ? `${mySaved.length}` : undefined}>Saved</SectionLabel>
          {!mySaved.length ? (
            <Muted size={12.5} style={{ lineHeight: 17, marginBottom: 6 }}>
              Nothing saved for {sportWord} yet. Open a player and tap Save to keep them here.
            </Muted>
          ) : mySaved.map(p => (
            <PersonRow key={p.key} name={p.name} meta={[p.tour, p.rank ? `#${p.rank}` : null, p.team].filter(Boolean).join(' · ')}
                       onPress={() => openSaved(p)}
                       right={<Pressable onPress={() => { tap(); toggle(p) }} hitSlop={8} style={s.unsave}><Text style={s.unsaveText}>♥</Text></Pressable>} />
          ))}
          {myRecent.length ? (
            <>
              <SectionLabel right={<Pressable onPress={clear} hitSlop={8}><Text style={s.clear}>Clear</Text></Pressable>}>Recently viewed</SectionLabel>
              {myRecent.map(p => (
                <PersonRow key={p.key} name={p.name} meta={[p.tour, p.rank ? `#${p.rank}` : null, p.team].filter(Boolean).join(' · ')}
                           onPress={() => openSaved(p)} />
              ))}
            </>
          ) : null}
          {!mySaved.length && !myRecent.length ? (
            <Empty title={`Look up any ${sportWord} player`}
                   hint={sport === 'tennis' ? 'Stats by surface, recent form, and how each prop has landed.'
                     : sport === 'nfl' ? 'Role, this week\'s matchup, and the game log for every prop.'
                     : 'Minutes, recent averages, and the game log for every prop.'} />
          ) : null}
        </>
      )}

      <PlayerSheet player={open} onClose={() => setOpen(null)} />
    </Screen>
  )
}

function useTeamSearch(sport: SportKey, q: string) {
  const [results, setResults] = useState<Hit[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (sport === 'tennis' || q.trim().length < 2) { setResults([]); setLoading(false); return }
    let alive = true
    setLoading(true); setError(null)
    const t = setTimeout(() => {
      (sport === 'nfl'
        ? searchNfl(q.trim()).then(d => (d?.players || []).map((p: any): Hit => ({ name: p.name, team: p.team || null, position: p.position || null })))
        : searchNba(q.trim()).then(d => (Array.isArray(d) ? d : []).map((p: any): Hit => ({ name: p.name, team: p.team || null, games: p.games ?? null }))))
        .then(r => { if (alive) setResults(r) })
        .catch(() => { if (alive) { setResults([]); setError('Search failed — try again') } })
        .finally(() => { if (alive) setLoading(false) })
    }, 300)
    return () => { alive = false; clearTimeout(t) }
  }, [q, sport])
  return { results, loading, error }
}

const s = StyleSheet.create({
  search: { backgroundColor: 'rgba(255,255,255,0.04)', borderWidth: 1, borderColor: T.glassLine,
            borderRadius: T.r1, paddingHorizontal: 14, minHeight: 50, color: T.white, fontFamily: F.body, fontSize: 16 },
  unsave: { minWidth: 36, minHeight: 36, alignItems: 'center', justifyContent: 'center' },
  unsaveText: { fontSize: 18, color: T.green },
  clear: { fontFamily: F.bodyMed, fontSize: 12, color: T.muted2 },
})
