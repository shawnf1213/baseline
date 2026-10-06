// Choose a player: a sheet with one search box and a short result list. The
// keyboard is up as soon as it opens, so picking a player is type, tap, done.
import { useEffect } from 'react'
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native'
import { Sheet } from './Sheet'
import { Muted } from './ui'
import { PlayerAvatar } from './Avatar'
import { Skeleton } from './Skeleton'
import { F, T } from '@/theme'
import { SearchHit, usePlayerSearch } from '@/lib/usePlayerSearch'
import { tap } from '@/lib/haptics'

export type PickedPlayer = { id: string; name: string; tour: 'ATP' | 'WTA'; currentRank: number | null }

export function PlayerPicker({ open, label, tour, onPick, onClose }:
  { open: boolean; label: string; tour: 'ATP' | 'WTA'; onPick: (p: PickedPlayer) => void; onClose: () => void }) {
  const { query, setQuery, results, loading, error } = usePlayerSearch(tour)
  useEffect(() => { if (!open) setQuery('') }, [open, setQuery])

  const choose = (h: SearchHit) => {
    tap()
    const t: 'ATP' | 'WTA' = h.gender === 'F' ? 'WTA' : h.gender === 'M' ? 'ATP' : tour
    onPick({ id: h.id, name: h.name, tour: t, currentRank: h.currentRank ?? null })
  }

  return (
    <Sheet open={open} onClose={onClose} title={`Choose ${label.toLowerCase()}`}>
      <TextInput value={query} onChangeText={setQuery} autoFocus autoCorrect={false}
                 autoCapitalize="words" placeholder={`Search ${tour} players`}
                 placeholderTextColor={T.muted2} style={s.input} returnKeyType="search"
                 clearButtonMode="while-editing" />
      {query.trim().length < 3 ? (
        <Muted size={12.5} style={{ marginTop: 12 }}>Type at least three letters of a name.</Muted>
      ) : loading && !results.length ? (
        <View style={{ marginTop: 12, gap: 8 }}>
          {[0, 1, 2].map(i => <Skeleton key={i} h={52} r={12} />)}
        </View>
      ) : error ? (
        <Muted size={12.5} style={{ marginTop: 12 }}>{error}</Muted>
      ) : !results.length ? (
        <Muted size={12.5} style={{ marginTop: 12 }}>No {tour} player matches "{query.trim()}".</Muted>
      ) : (
        <View style={{ marginTop: 10 }}>
          {results.slice(0, 8).map(h => (
            <Pressable key={h.id} onPress={() => choose(h)}
                       style={({ pressed }) => [s.row, pressed && { backgroundColor: 'rgba(255,255,255,0.06)' }]}>
              <PlayerAvatar sport="tennis" name={h.name} size={40} />
              <Text style={s.name} numberOfLines={1}>{h.name}</Text>
              {h.currentRank ? <Text style={s.rank}>#{h.currentRank}</Text> : null}
            </Pressable>
          ))}
        </View>
      )}
    </Sheet>
  )
}

const s = StyleSheet.create({
  input: { backgroundColor: 'rgba(255,255,255,0.05)', borderWidth: 1, borderColor: `${T.green}66`,
           borderRadius: T.r1, paddingHorizontal: 14, minHeight: 50, color: T.white,
           fontFamily: F.body, fontSize: 17, marginTop: 4 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 54, paddingHorizontal: 10,
         borderRadius: T.r1, marginBottom: 4 },
  avatar: { width: 36, height: 36, borderRadius: 18, backgroundColor: 'rgba(255,255,255,0.07)',
            alignItems: 'center', justifyContent: 'center' },
  initials: { fontFamily: F.condBold, fontSize: 13, color: T.muted },
  name: { flex: 1, fontFamily: F.bodyMed, fontSize: 16, color: T.white },
  rank: { fontFamily: F.condBold, fontSize: 13, color: T.muted2 },
})
