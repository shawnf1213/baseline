// A player's face in a circle, and a team's abbreviation in a circle.
//
// The face loads from lib/images; while it loads (or if there is none) the
// circle shows the player's initials on a background tinted by `ring`, so a
// row never jumps or shows an empty hole. A URL that failed is remembered for
// ten minutes, so scrolling a long board does not re-ask for the same missing
// photo on every recycle, but a face that was only temporarily unavailable
// comes back.
//
// No source label is drawn (operator, 2026-10-07): the photos are free-use
// images from Wikipedia where one exists for the player, and that is stated
// in the App Store review notes rather than on the screen.
import { useState } from 'react'
import { StyleSheet, Text, View } from 'react-native'
import { Image } from 'expo-image'
import { LinearGradient } from 'expo-linear-gradient'
import { F, T } from '@/theme'
import type { SportKey } from '@/lib/sports'
import { initials, playerImageUrl } from '@/lib/images'

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

const s = StyleSheet.create({
  circle: { overflow: 'hidden', alignItems: 'center', justifyContent: 'center', backgroundColor: '#121212' },
  ini: { fontFamily: F.condBold, color: T.muted, letterSpacing: 0.5 },
  abbr: { fontFamily: F.condHeavy, color: T.muted, letterSpacing: 1 },
})
