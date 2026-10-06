// One pick, at a glance: player, the call (side · line · prop), confidence,
// and its result once graded. Everything else lives in the sheet a tap opens.
// `hero` is the Pick of the Day treatment — the first thing on the main
// screen, so it is bigger and the only card with a tinted border.
import { StyleSheet, Text, View } from 'react-native'
import { Card } from './ui'
import { Meter } from './charts'
import { F, T, sideTone, tier } from '@/theme'
import { PickRow, fmtLine, resultMeta, shortProp } from '@/lib/picks'
import { tap } from '@/lib/haptics'

const TIER_LABEL: Record<string, string> = { ELITE: 'Elite', STRONG: 'Strong', LEAN: 'Lean', '': '' }

export function ResultBadge({ r, big }: { r: PickRow; big?: boolean }) {
  const m = resultMeta(r.result)
  if (m.tone === 'pending') return null
  const tone = m.tone === 'win' ? T.green : m.tone === 'loss' ? T.red : T.muted2
  const val = m.tone === 'void' || r.resultValue == null ? '' : ` · ${fmtLine(r.resultValue)}`
  return (
    <View style={[s.badge, { borderColor: `${tone}44`, backgroundColor: `${tone}18` }, big && { paddingVertical: 5 }]}>
      <Text style={[s.badgeText, { color: tone }, big && { fontSize: 12 }]}>{m.label}{val}</Text>
    </View>
  )
}

export function PickCard({ r, onPress, hero }: { r: PickRow; onPress: (r: PickRow) => void; hero?: boolean }) {
  const side = sideTone(r.lean)
  const tr = tier(r.confidence)
  const open = () => { tap(); onPress(r) }
  const call = `${r.lean || ''} ${fmtLine(r.line)} ${shortProp(r.propType)}`.trim()
  const dim = resultMeta(r.result).tone === 'void'

  if (hero) {
    return (
      <Card hi onPress={open} style={[s.hero, { borderColor: `${side.tone}55`, opacity: dim ? 0.6 : 1 }]}>
        <View style={[s.rail, { backgroundColor: side.tone, width: 6 }]} />
        <Text style={s.eyebrow}>⭐  Pick of the Day</Text>
        <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 12 }}>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={s.heroName} numberOfLines={2}>{r.player}</Text>
            <Text style={s.meta} numberOfLines={1}>
              vs {r.opponent}{r.surface ? ` · ${r.surface}` : ''}
            </Text>
          </View>
          <View style={{ alignItems: 'flex-end' }}>
            <Text style={[s.confBig, { color: tr.tone }]}>{r.confidence != null ? Math.round(r.confidence) : '—'}</Text>
            <Text style={s.confLabel}>{TIER_LABEL[tr.label] || 'Confidence'}</Text>
          </View>
        </View>
        <View style={[s.callBox, { borderColor: `${side.tone}44`, backgroundColor: `${side.tone}14` }]}>
          <Text style={[s.callBig, { color: side.tone }]}>{r.lean || '—'}</Text>
          <Text style={s.callLine}>{fmtLine(r.line)}</Text>
          <Text style={s.callProp} numberOfLines={1}>{shortProp(r.propType)}</Text>
          <View style={{ flex: 1 }} />
          <ResultBadge r={r} big />
        </View>
        <View style={{ marginTop: 12 }}>
          <Meter pct={r.confidence} tone={tr.tone} height={5} />
        </View>
        <Text style={s.hint}>Tap for the projection, the numbers behind it and recent form</Text>
      </Card>
    )
  }

  return (
    <Card onPress={open} style={[s.row, { opacity: dim ? 0.55 : 1 }]}>
      <View style={[s.rail, { backgroundColor: side.tone }]} />
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={s.name} numberOfLines={1}>{r.player}</Text>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 3 }}>
          <Text style={[s.call, { color: side.tone }]} numberOfLines={1}>{call}</Text>
          {r.isThreeX ? <Text style={s.threex}>3x</Text> : null}
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4 }}>
          <Text style={s.meta} numberOfLines={1}>vs {r.opponent}</Text>
          <ResultBadge r={r} />
        </View>
      </View>
      <View style={{ alignItems: 'flex-end', minWidth: 44 }}>
        <Text style={[s.conf, { color: tr.tone }]}>{r.confidence != null ? Math.round(r.confidence) : '—'}</Text>
        <Text style={s.confLabel}>{TIER_LABEL[tr.label] || 'conf.'}</Text>
      </View>
      <Text style={s.chev}>›</Text>
    </Card>
  )
}

const s = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12,
         paddingLeft: 18, paddingRight: 12, marginBottom: 10, overflow: 'hidden' },
  hero: { paddingVertical: 18, paddingLeft: 20, paddingRight: 16, marginBottom: 14, overflow: 'hidden',
          borderWidth: 1 },
  rail: { position: 'absolute', left: 0, top: 0, bottom: 0, width: 4, opacity: 0.9 },
  eyebrow: { fontFamily: F.condBold, fontSize: 11, letterSpacing: 1.6, textTransform: 'uppercase',
             color: T.amber, marginBottom: 8 },
  name: { fontFamily: F.condBlack, fontSize: 18, color: T.white, letterSpacing: 0.3 },
  heroName: { fontFamily: F.condHeavy, fontSize: 26, lineHeight: 29, color: T.white, letterSpacing: 0.3 },
  call: { fontFamily: F.condBold, fontSize: 13.5, letterSpacing: 0.8, textTransform: 'uppercase' },
  meta: { fontFamily: F.body, fontSize: 12.5, color: T.muted, flexShrink: 1 },
  conf: { fontFamily: F.condHeavy, fontSize: 24, lineHeight: 26 },
  confBig: { fontFamily: F.condHeavy, fontSize: 38, lineHeight: 40 },
  confLabel: { fontFamily: F.condBold, fontSize: 9.5, letterSpacing: 1, textTransform: 'uppercase',
               color: T.muted2 },
  chev: { fontFamily: F.body, fontSize: 22, color: T.muted2, marginLeft: -2 },
  threex: { fontFamily: F.condBold, fontSize: 10, letterSpacing: 1, color: T.amber,
            borderWidth: 1, borderColor: `${T.amber}44`, borderRadius: 5, paddingHorizontal: 5,
            paddingVertical: 1, textTransform: 'uppercase' },
  callBox: { flexDirection: 'row', alignItems: 'baseline', gap: 8, marginTop: 14,
             paddingHorizontal: 14, paddingVertical: 10, borderRadius: T.r2, borderWidth: 1 },
  callBig: { fontFamily: F.condHeavy, fontSize: 22, letterSpacing: 1.2 },
  callLine: { fontFamily: F.condHeavy, fontSize: 26, color: T.white },
  callProp: { fontFamily: F.condBold, fontSize: 14, color: T.muted, flexShrink: 1 },
  badge: { borderWidth: 1, borderRadius: 7, paddingHorizontal: 8, paddingVertical: 3, alignSelf: 'flex-start' },
  badgeText: { fontFamily: F.condBold, fontSize: 10.5, letterSpacing: 0.8, textTransform: 'uppercase' },
  hint: { fontFamily: F.body, fontSize: 11, color: T.muted2, marginTop: 10 },
})
