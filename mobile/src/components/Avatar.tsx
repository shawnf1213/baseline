// A player's face in a circle, a team's abbreviation in a circle, and the
// credit line the photos' free licences ask for.
//
// The face loads from lib/images; while it loads (or if there is none) the
// circle shows the player's initials on a background tinted by `ring`, so a
// row never jumps or shows an empty hole. A URL that failed is remembered for
// ten minutes, so scrolling a long board does not re-ask for the same missing
// photo on every recycle, but a face that was only temporarily unavailable
// comes back.
import { useState } from 'react'
import { Linking, Pressable, StyleSheet, StyleProp, Text, View, ViewStyle } from 'react-native'
import { Image } from 'expo-image'
import { LinearGradient } from 'expo-linear-gradient'
import { F, T } from '@/theme'
import type { SportKey } from '@/lib/sports'
import { initials, playerImageUrl } from '@/lib/images'
import { fetchPhotoCredit } from '@/lib/api'
import { tap } from '@/lib/haptics'

const RETRY_MS = 10 * 60_000
const failedAt = new Map<string, number>()
const failed = {
  has: (u: string) => { const t = failedAt.get(u); return t != null && Date.now() - t < RETRY_MS },
  add: (u: string) => { failedAt.set(u, Date.now()) },
}

type Props = {
  sport: SportKey
  name: string
  size?: number
  ring?: string | null        // border colour (lean); omitted = hairline
  team?: string | null        // accepted for callers; no crest is drawn (see lib/images)
}

export function PlayerAvatar({ sport, name, size = 44, ring }: Props) {
  const uri = playerImageUrl(sport, name)
  const [badUri, setBadUri] = useState<string | null>(null)
  const bad = !uri || failed.has(uri) || badUri === uri
  const tint = ring || T.glassLineHi
  return (
    <View style={[s.circle, { width: size, height: size, borderRadius: size / 2,
                              borderColor: tint, borderWidth: ring ? 2 : StyleSheet.hairlineWidth }]}>
      <LinearGradient colors={[`${ring || '#ffffff'}2E`, 'rgba(255,255,255,0.03)']}
                      start={{ x: 0.2, y: 0 }} end={{ x: 0.8, y: 1 }} style={StyleSheet.absoluteFill} />
      <Text style={[s.ini, { fontSize: Math.round(size * 0.34) }]}>{initials(name)}</Text>
      {!bad ? (
        <Image source={{ uri: uri! }} style={StyleSheet.absoluteFill} contentFit="cover"
               contentPosition="top" transition={180} cachePolicy="memory-disk" recyclingKey={uri!}
               onError={() => { failed.add(uri!); setBadUri(uri) }} />
      ) : null}
    </View>
  )
}

// A team, as its abbreviation. `sport` stays in the signature so callers read
// the same as they did when this drew a crest.
export function TeamLogo({ team, size = 44, ring }:
  { sport: SportKey; team: string | null | undefined; size?: number; ring?: string | null }) {
  return (
    <View style={[s.circle, { width: size, height: size, borderRadius: size / 2,
                              borderColor: ring || T.glassLineHi, borderWidth: ring ? 2 : StyleSheet.hairlineWidth,
                              backgroundColor: 'rgba(255,255,255,0.05)' }]}>
      <Text style={[s.abbr, { fontSize: Math.max(9, Math.round(size * 0.3)) }]}>{(team || '?').slice(0, 3).toUpperCase()}</Text>
    </View>
  )
}

// "Photos: Wikimedia Commons · Gauff ↗ · Mertens ↗" — each name opens the
// Commons file page that names the photographer and the licence, which is the
// attribution those licences ask for. A player with no photo opens Commons
// itself; the line is still true of every face on the screen.
const COMMONS = 'https://commons.wikimedia.org/'
const surname = (n: string) => { const t = n.trim().split(/\s+/); return t[t.length - 1] || n }

export function PhotoCredit({ sport, names, style }:
  { sport: SportKey; names: (string | null | undefined)[]; style?: StyleProp<ViewStyle> }) {
  const list = [...new Set(names.filter(Boolean) as string[])]
  if (!list.length) return null
  const open = async (n: string) => {
    tap()
    let url = COMMONS
    try { const c = await fetchPhotoCredit(sport, n); url = c?.file_page || c?.article || COMMONS } catch { /* no photo for this name */ }
    Linking.openURL(url).catch(() => {})
  }
  return (
    <View style={[s.credit, style]}>
      <Text style={s.creditText}>Photos: Wikimedia Commons</Text>
      {list.map(n => (
        <Pressable key={n} onPress={() => open(n)} hitSlop={6} accessibilityRole="link" accessibilityLabel={`Photo credit for ${n}`}>
          <Text style={s.creditLink}>{surname(n)} ↗</Text>
        </Pressable>
      ))}
    </View>
  )
}

const s = StyleSheet.create({
  circle: { overflow: 'hidden', alignItems: 'center', justifyContent: 'center', backgroundColor: '#121212' },
  ini: { fontFamily: F.condBold, color: T.muted, letterSpacing: 0.5 },
  abbr: { fontFamily: F.condHeavy, color: T.muted, letterSpacing: 1 },
  credit: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'center', gap: 8, marginTop: 8 },
  creditText: { fontFamily: F.body, fontSize: 10.5, color: T.muted2 },
  creditLink: { fontFamily: F.bodySemi, fontSize: 10.5, color: T.muted, textDecorationLine: 'underline' },
})
