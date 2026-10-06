// A player's face in a circle, a team's crest in a circle.
//
// The face loads from lib/images; while it loads (or if there is none) the
// circle shows the player's initials on a background tinted by `ring`, so a
// row never jumps or shows an empty hole. A URL that failed is remembered for
// ten minutes, so scrolling a long board does not re-ask for the same missing
// photo on every recycle, but a face that was only temporarily unavailable
// comes back.
import { useState } from 'react'
import { StyleSheet, Text, View } from 'react-native'
import { Image } from 'expo-image'
import { LinearGradient } from 'expo-linear-gradient'
import { F, T } from '@/theme'
import type { SportKey } from '@/lib/sports'
import { initials, playerImageUrl, teamLogoUrl } from '@/lib/images'

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
  team?: string | null        // NFL/NBA: small crest badge on the corner
}

export function PlayerAvatar({ sport, name, size = 44, ring, team }: Props) {
  const uri = playerImageUrl(sport, name)
  const [badUri, setBadUri] = useState<string | null>(null)
  const bad = !uri || failed.has(uri) || badUri === uri
  const tint = ring || T.glassLineHi
  const logo = (sport === 'nfl' || sport === 'nba') ? teamLogoUrl(sport, team, 64) : null
  const badge = Math.max(16, Math.round(size * 0.38))
  return (
    <View style={{ width: size, height: size }}>
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
      {logo ? (
        <View style={[s.badge, { width: badge, height: badge, borderRadius: badge / 2, right: -2, bottom: -2 }]}>
          <TeamImage uri={logo} size={badge - 4} />
        </View>
      ) : null}
    </View>
  )
}

export function TeamLogo({ sport, team, size = 44, ring }:
  { sport: SportKey; team: string | null | undefined; size?: number; ring?: string | null }) {
  const uri = teamLogoUrl(sport, team, Math.min(160, size * 3))
  return (
    <View style={[s.circle, { width: size, height: size, borderRadius: size / 2,
                              borderColor: ring || T.glassLineHi, borderWidth: ring ? 2 : StyleSheet.hairlineWidth,
                              backgroundColor: 'rgba(255,255,255,0.05)' }]}>
      <Text style={[s.ini, { fontSize: Math.round(size * 0.28) }]}>{(team || '?').slice(0, 3).toUpperCase()}</Text>
      {uri ? <View style={[StyleSheet.absoluteFill, { alignItems: 'center', justifyContent: 'center' }]}>
               <TeamImage uri={uri} size={Math.round(size * 0.72)} />
             </View> : null}
    </View>
  )
}

function TeamImage({ uri, size }: { uri: string; size: number }) {
  const [bad, setBad] = useState(failed.has(uri))
  if (bad) return null
  return <Image source={{ uri }} style={{ width: size, height: size }} contentFit="contain"
                cachePolicy="memory-disk" transition={120} onError={() => { failed.add(uri); setBad(true) }} />
}

const s = StyleSheet.create({
  circle: { overflow: 'hidden', alignItems: 'center', justifyContent: 'center', backgroundColor: '#121212' },
  ini: { fontFamily: F.condBold, color: T.muted, letterSpacing: 0.5 },
  badge: { position: 'absolute', backgroundColor: '#0d0d0d', borderWidth: 1, borderColor: T.glassLineHi,
           alignItems: 'center', justifyContent: 'center', overflow: 'hidden' },
})
