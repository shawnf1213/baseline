// One pick, at a glance: the player's face, the call (side · line · prop), a
// confidence ring, and its result once graded. Everything else lives in the
// sheet a tap opens. `hero` is the Pick of the Day treatment. `pricing` is the
// board's not-yet-priced state: the line is known, our number is not.
import { Pressable, StyleSheet, Text, View } from 'react-native'
import { LinearGradient } from 'expo-linear-gradient'
import { Card } from './ui'
import { PlayerAvatar } from './Avatar'
import { ConfRing } from './Ring'
import { CardGlow } from './Glow'
import { F, T, sideTone, tier } from '@/theme'
import { PickRow, fmtLine, fmtSigned, propLabel, resultMeta } from '@/lib/picks'
import { tap } from '@/lib/haptics'

export const SURFACE_TONE: Record<string, string> = { Hard: T.blue, Clay: '#E8803F', Grass: T.green }

export function ResultBadge({ r, big }: { r: PickRow; big?: boolean }) {
  const m = resultMeta(r.result)
  if (m.tone === 'pending') return null
  const tone = m.tone === 'win' ? T.green : m.tone === 'loss' ? T.red : T.muted2
  const val = m.tone === 'void' || r.resultValue == null ? '' : ` · ${fmtLine(r.resultValue)}`
  return (
    <View style={[s.badge, { borderColor: `${tone}55`, backgroundColor: `${tone}1F` }, big && { paddingVertical: 5 }]}>
      <Text style={[s.badgeText, { color: tone }, big && { fontSize: 12 }]}>{m.label}{val}</Text>
    </View>
  )
}

export function SurfaceChip({ surface }: { surface?: string }) {
  if (!surface) return null
  const tone = SURFACE_TONE[surface] || T.muted
  return (
    <View style={[s.chip, { borderColor: `${tone}55`, backgroundColor: `${tone}1A` }]}>
      <View style={[s.chipDot, { backgroundColor: tone }]} />
      <Text style={[s.chipText, { color: tone }]}>{surface}</Text>
    </View>
  )
}

type Props = { r: PickRow; onPress: (r: PickRow) => void; hero?: boolean; pricing?: boolean; sub?: string; noData?: boolean }

export function PickCard({ r, onPress, hero, pricing, sub, noData }: Props) {
  const side = sideTone(r.lean)
  const open = () => { tap(); onPress(r) }
  const label = propLabel(r.sport, r.propType)
  const dim = resultMeta(r.result).tone === 'void' || noData
  const live = !pricing && !noData
  const toneNow = live ? side.tone : T.muted2

  if (hero) return <Hero r={r} onPress={open} sub={sub} />

  return (
    <Pressable onPress={open} style={({ pressed }) => [pressed && { transform: [{ scale: 0.985 }] }]}>
      <View style={[s.row, { opacity: dim ? 0.6 : 1 }]}>
        <LinearGradient colors={[`${toneNow}1E`, 'rgba(255,255,255,0.015)']} start={{ x: 0, y: 0.5 }} end={{ x: 0.75, y: 0.5 }}
                        style={StyleSheet.absoluteFill} />
        <PlayerAvatar sport={r.sport} name={r.player} size={48} ring={live ? side.tone : null} team={r.team} />
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={s.name} numberOfLines={1}>{r.isPotd ? '⭐ ' : ''}{r.player}</Text>
          <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 6, marginTop: 2 }}>
            <Text style={[s.side, { color: toneNow }]}>{live ? (r.lean || '—') : 'LINE'}</Text>
            <Text style={s.line}>{fmtLine(r.line)}</Text>
            <Text style={s.prop} numberOfLines={1}>{label}</Text>
            {r.isThreeX ? <Text style={s.threex}>3x</Text> : null}
          </View>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4 }}>
            <Text style={s.meta} numberOfLines={1}>
              {r.team ? `${r.team} · ` : ''}vs {r.opponent}{sub ? ` · ${sub}` : ''}
            </Text>
            <ResultBadge r={r} />
          </View>
        </View>
        <ConfRing conf={r.confidence} size={48} state={pricing ? 'pending' : noData ? 'nodata' : undefined} />
      </View>
    </Pressable>
  )
}

function Hero({ r, onPress, sub }: { r: PickRow; onPress: () => void; sub?: string }) {
  const side = sideTone(r.lean)
  const tr = tier(r.confidence)
  const label = propLabel(r.sport, r.propType)
  const dim = resultMeta(r.result).tone === 'void'
  return (
    <Card hi onPress={onPress} style={[s.hero, { borderColor: `${side.tone}66`, opacity: dim ? 0.65 : 1 }]}>
      <CardGlow color={side.tone} strength={0.28} />
      <CardGlow color={T.amber} strength={0.1} corner="tr" />
      <View style={s.heroTop}>
        <View style={s.eyebrow}><Text style={s.eyebrowText}>⭐  Pick of the Day</Text></View>
        {r.sport === 'tennis' ? <SurfaceChip surface={r.surface} /> : null}
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14, marginTop: 14 }}>
        <PlayerAvatar sport={r.sport} name={r.player} size={76} ring={side.tone} team={r.team} />
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={s.heroName} numberOfLines={2}>{r.player}</Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 6 }}>
            {/* An NFL/NBA opponent is a team; its abbreviation is already in the text. */}
            {r.sport === 'tennis' ? <PlayerAvatar sport="tennis" name={r.opponent} size={20} /> : null}
            <Text style={s.meta} numberOfLines={1}>vs {r.opponent}{sub ? ` · ${sub}` : ''}</Text>
          </View>
        </View>
        <ConfRing conf={r.confidence} size={74} />
      </View>
      <View style={[s.callBox, { borderColor: `${side.tone}55` }]}>
        <LinearGradient colors={[`${side.tone}2A`, `${side.tone}08`]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}
                        style={[StyleSheet.absoluteFill, { borderRadius: T.r2 }]} />
        <Text style={[s.callBig, { color: side.tone }]}>{r.lean || '—'}</Text>
        <Text style={s.callLine}>{fmtLine(r.line)}</Text>
        <Text style={s.callProp} numberOfLines={1}>{label}</Text>
        <View style={{ flex: 1 }} />
        <ResultBadge r={r} big />
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 12 }}>
        <Text style={s.hint}>
          Baseline <Text style={{ color: T.white, fontFamily: F.bodySemi }}>{r.projection != null ? r.projection.toFixed(1) : '—'}</Text>
          {r.edge != null ? <> · edge <Text style={{ color: side.tone, fontFamily: F.bodySemi }}>{fmtSigned(r.edge)}</Text></> : null}
          {tr.label ? ` · ${tr.label.toLowerCase()}` : ''}
        </Text>
        <Text style={s.more}>Details ›</Text>
      </View>
    </Card>
  )
}

const s = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12, paddingHorizontal: 12,
         marginBottom: 10, borderRadius: T.r3, overflow: 'hidden', borderWidth: StyleSheet.hairlineWidth,
         borderColor: T.glassLine, backgroundColor: 'rgba(255,255,255,0.02)' },
  hero: { paddingVertical: 16, paddingHorizontal: 16, marginBottom: 14, overflow: 'hidden', borderWidth: 1 },
  heroTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  eyebrow: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: `${T.amber}1F`,
             borderWidth: 1, borderColor: `${T.amber}55` },
  eyebrowText: { fontFamily: F.condBold, fontSize: 11, letterSpacing: 1.4, textTransform: 'uppercase', color: T.amber },
  name: { fontFamily: F.condBlack, fontSize: 18, color: T.white, letterSpacing: 0.3 },
  side: { fontFamily: F.condHeavy, fontSize: 14, letterSpacing: 0.8 },
  line: { fontFamily: F.condHeavy, fontSize: 16, color: T.white },
  prop: { fontFamily: F.condBold, fontSize: 13.5, color: T.muted, flexShrink: 1 },
  heroName: { fontFamily: F.condHeavy, fontSize: 27, lineHeight: 29, color: T.white, letterSpacing: 0.3 },
  meta: { fontFamily: F.body, fontSize: 12.5, color: T.muted, flexShrink: 1 },
  threex: { fontFamily: F.condBold, fontSize: 10, letterSpacing: 1, color: T.amber,
            borderWidth: 1, borderColor: `${T.amber}44`, borderRadius: 5, paddingHorizontal: 5,
            paddingVertical: 1, textTransform: 'uppercase' },
  callBox: { flexDirection: 'row', alignItems: 'baseline', gap: 8, marginTop: 16, overflow: 'hidden',
             paddingHorizontal: 14, paddingVertical: 12, borderRadius: T.r2, borderWidth: 1 },
  callBig: { fontFamily: F.condHeavy, fontSize: 24, letterSpacing: 1.2 },
  callLine: { fontFamily: F.condHeavy, fontSize: 28, color: T.white },
  callProp: { fontFamily: F.condBold, fontSize: 14.5, color: T.muted, flexShrink: 1 },
  badge: { borderWidth: 1, borderRadius: 7, paddingHorizontal: 8, paddingVertical: 3, alignSelf: 'flex-start' },
  badgeText: { fontFamily: F.condBold, fontSize: 10.5, letterSpacing: 0.8, textTransform: 'uppercase' },
  hint: { fontFamily: F.body, fontSize: 12, color: T.muted, flexShrink: 1 },
  more: { fontFamily: F.condBold, fontSize: 12, letterSpacing: 0.8, color: T.green, textTransform: 'uppercase' },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 8, paddingVertical: 3,
          borderRadius: 999, borderWidth: 1 },
  chipDot: { width: 6, height: 6, borderRadius: 3 },
  chipText: { fontFamily: F.condBold, fontSize: 10.5, letterSpacing: 0.9, textTransform: 'uppercase' },
})
