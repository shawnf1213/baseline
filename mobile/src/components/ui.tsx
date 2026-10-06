// The card primitives — ports of Card, Pill, GlassTabs, SectionLabel and
// PageTitle from frontend/src/mobile/bits.jsx. Same names on purpose, so a
// screen ported from the PWA reads line for line.
import { ReactNode } from 'react'
import { Pressable, StyleSheet, Text, View, ViewStyle, StyleProp } from 'react-native'
import { F, T } from '@/theme'

type CardProps = {
  children: ReactNode
  style?: StyleProp<ViewStyle>
  onPress?: () => void
}

// GLASS, matching the landing page's cards — translucent over the dark ground
// rather than a solid dark fill, with a hairline and a soft drop. No blur, for
// the reason theme.ts records.
export function Card({ children, style, onPress }: CardProps) {
  const body = <View style={[s.card, style]}>{children}</View>
  if (!onPress) return body
  return (
    <Pressable onPress={onPress}
               style={({ pressed }) => [pressed && { transform: [{ scale: 0.985 }] }]}>
      {body}
    </Pressable>
  )
}

export function Pill({ children, tone = T.green, live }:
                     { children: ReactNode; tone?: string; live?: boolean }) {
  return (
    <View style={[s.pill, { borderColor: `${tone}55`, backgroundColor: `${tone}1C` }]}>
      {live ? <View style={[s.dot, { backgroundColor: tone }]} /> : null}
      <Text style={[s.pillText, { color: tone }]}>{children}</Text>
    </View>
  )
}

export function SectionLabel({ children, right }:
                             { children: ReactNode; right?: ReactNode }) {
  return (
    <View style={s.sectionRow}>
      <Text style={s.sectionLabel}>{children}</Text>
      {right ? <View>{typeof right === 'string'
        ? <Text style={s.sectionRight}>{right}</Text> : right}</View> : null}
    </View>
  )
}

export function PageTitle({ children, sub, right }:
                          { children: ReactNode; sub?: ReactNode; right?: ReactNode }) {
  return (
    <View style={s.titleRow}>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={s.title}>{children}</Text>
        {sub ? <View style={s.subRow}>{sub}</View> : null}
      </View>
      {right}
    </View>
  )
}

export function GlassTabs<K extends string>({ options, value, onChange }:
  { options: { key: K; label: string }[]; value: K; onChange: (k: K) => void }) {
  return (
    <View style={s.tabs}>
      {options.map(o => {
        const on = o.key === value
        return (
          <Pressable key={o.key} onPress={() => onChange(o.key)}
                     style={[s.tab, on && s.tabOn]}>
            <Text style={[s.tabText, on && { color: T.green }]}>{o.label}</Text>
          </Pressable>
        )
      })}
    </View>
  )
}

export function Muted({ children, size = 12 }: { children: ReactNode; size?: number }) {
  return <Text style={{ color: T.muted2, fontFamily: F.body, fontSize: size }}>{children}</Text>
}

const s = StyleSheet.create({
  card: {
    backgroundColor: T.glass,
    borderWidth: StyleSheet.hairlineWidth, borderColor: T.glassLine,
    borderRadius: T.r3,
    padding: T.s3,
    shadowColor: '#000', shadowOpacity: 0.45, shadowRadius: 15,
    shadowOffset: { width: 0, height: 10 },
  },
  pill: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 9, paddingVertical: 3, borderRadius: 999, borderWidth: 1,
  },
  dot: { width: 6, height: 6, borderRadius: 3 },
  pillText: { fontFamily: F.condBold, fontSize: 10.5, letterSpacing: 1.1, textTransform: 'uppercase' },
  sectionRow: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between',
                marginTop: T.s4, marginBottom: T.s2 },
  sectionLabel: { fontFamily: F.condBold, fontSize: 11, letterSpacing: 1.4,
                  textTransform: 'uppercase', color: T.muted },
  sectionRight: { fontFamily: F.body, fontSize: 11, color: T.muted2 },
  titleRow: { flexDirection: 'row', alignItems: 'flex-end', gap: T.s3, marginBottom: T.s3 },
  title: { fontFamily: F.condHeavy, fontSize: 30, letterSpacing: 0.4, color: T.white, lineHeight: 32 },
  subRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4, flexWrap: 'wrap' },
  tabs: { flexDirection: 'row', gap: 4, padding: 3, borderRadius: 999,
          backgroundColor: 'rgba(255,255,255,0.025)', borderWidth: 1, borderColor: T.glassLine,
          alignSelf: 'flex-start', marginBottom: T.s3 },
  tab: { minHeight: 32, paddingHorizontal: 13, borderRadius: 999, justifyContent: 'center',
         borderWidth: 1, borderColor: 'transparent' },
  tabOn: { borderColor: `${T.green}55`, backgroundColor: `${T.green}1C` },
  tabText: { fontFamily: F.condBlack, fontSize: 12.5, letterSpacing: 1, textTransform: 'uppercase',
             color: T.muted2 },
})
