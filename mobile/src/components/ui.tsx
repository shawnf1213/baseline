// The shared primitives. Brand identity stays (dark ground, Baseline green,
// red/amber for results, Barlow); the shapes are the phone's — 44pt+ targets,
// full-width segmented controls, plain labels.
import { ReactNode } from 'react'
import { ActivityIndicator, Pressable, StyleProp, StyleSheet, Text, View, ViewStyle } from 'react-native'
import { LinearGradient } from 'expo-linear-gradient'
import { F, GLASS_DIR, T } from '@/theme'
import { select } from '@/lib/haptics'

type CardProps = {
  children: ReactNode
  style?: StyleProp<ViewStyle>
  onPress?: () => void
  hi?: boolean
}

// GLASS, matching the landing page's cards — the website's own 160deg
// gradient over the dark ground, with a hairline. No blur, for the reason
// theme.ts records.
export function Card({ children, style, onPress, hi }: CardProps) {
  const body = (
    <LinearGradient colors={[...(hi ? T.glassHiStops : T.glassStops)]}
                    start={GLASS_DIR.start} end={GLASS_DIR.end}
                    style={[s.card, style]}>
      {children}
    </LinearGradient>
  )
  if (!onPress) return body
  return (
    <Pressable onPress={onPress}
               style={({ pressed }) => [pressed && { transform: [{ scale: 0.985 }], opacity: 0.92 }]}>
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

export function SectionLabel({ children, right, first }:
                             { children: ReactNode; right?: ReactNode; first?: boolean }) {
  return (
    <View style={[s.sectionRow, first && { marginTop: T.s2 }]}>
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
        {sub ? (typeof sub === 'string'
          ? <Text style={s.subText}>{sub}</Text>
          : <View style={s.subRow}>{sub}</View>) : null}
      </View>
      {right}
    </View>
  )
}

// Full-width segmented control, 44pt tall: the book switch, the mode switch,
// ATP/WTA. One of these replaces the website's three differently-sized tab
// rows.
export function Segmented<K extends string>({ options, value, onChange, compact }:
  { options: { key: K; label: string }[]; value: K; onChange: (k: K) => void; compact?: boolean }) {
  return (
    <View style={[s.seg, compact && { minHeight: 38 }]}>
      {options.map(o => {
        const on = o.key === value
        return (
          <Pressable key={o.key} onPress={() => { if (!on) { select(); onChange(o.key) } }}
                     style={[s.segItem, on && s.segOn]}>
            <Text style={[s.segText, compact && { fontSize: 12.5 }, on && { color: T.green }]}>
              {o.label}
            </Text>
          </Pressable>
        )
      })}
    </View>
  )
}

// A single toggle chip (surface, filter). 40pt tall.
export function Chip({ active, onPress, children }:
                     { active?: boolean; onPress: () => void; children: ReactNode }) {
  return (
    <Pressable onPress={() => { select(); onPress() }} style={[s.chip, active && s.chipOn]}>
      <Text style={[s.chipText, active && { color: T.green }]}>{children}</Text>
    </Pressable>
  )
}

export function Button({ label, onPress, kind = 'primary', disabled, busy, style }:
  { label: string; onPress: () => void; kind?: 'primary' | 'ghost' | 'quiet';
    disabled?: boolean; busy?: boolean; style?: StyleProp<ViewStyle> }) {
  const off = disabled || busy
  return (
    <Pressable onPress={onPress} disabled={off}
               style={({ pressed }) => [s.btn, kind === 'primary' && s.btnPrimary,
                 kind === 'ghost' && s.btnGhost, kind === 'quiet' && s.btnQuiet,
                 off && kind === 'primary' && s.btnOff, pressed && { opacity: 0.85 }, style]}>
      {busy ? <ActivityIndicator color={kind === 'primary' ? '#052e16' : T.green} />
            : <Text style={[s.btnText, kind === 'primary' ? (off ? { color: T.muted2 } : { color: '#052e16' })
                                                         : { color: kind === 'quiet' ? T.muted : T.white }]}>
                {label}
              </Text>}
    </Pressable>
  )
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <View style={s.empty}>
      <Text style={s.emptyTitle}>{title}</Text>
      {hint ? <Text style={s.emptyHint}>{hint}</Text> : null}
    </View>
  )
}

export function Muted({ children, size = 12, style }:
                      { children: ReactNode; size?: number; style?: StyleProp<any> }) {
  return <Text style={[{ color: T.muted2, fontFamily: F.body, fontSize: size }, style]}>{children}</Text>
}

// The one line every pick and projection screen ends with (App Store
// guideline 5.3 — informational, never advice).
export const DISCLAIMER = 'Projections are for informational purposes only.'
export function Disclaimer({ style }: { style?: StyleProp<ViewStyle> }) {
  return (
    <View style={[{ marginTop: 20, alignItems: 'center' }, style]}>
      <Text style={{ color: T.muted2, fontFamily: F.body, fontSize: 10.5, textAlign: 'center' }}>{DISCLAIMER}</Text>
    </View>
  )
}

// One person, one row — search results, saved players, recently viewed.
export function PersonRow({ name, meta, right, onPress, onLongPress }:
  { name: string; meta?: string; right?: ReactNode; onPress: () => void; onLongPress?: () => void }) {
  const t = (name || '').trim().split(/\s+/)
  const ini = ((t[0]?.[0] || '') + (t.length > 1 ? t[t.length - 1][0] : '')).toUpperCase() || '?'
  return (
    <Pressable onPress={() => { select(); onPress() }} onLongPress={onLongPress}
               style={({ pressed }) => [s.person, pressed && { opacity: 0.8 }]}>
      <View style={s.personAvatar}><Text style={s.personIni}>{ini}</Text></View>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={s.personName} numberOfLines={1}>{name}</Text>
        {meta ? <Text style={s.personMeta} numberOfLines={1}>{meta}</Text> : null}
      </View>
      {right}
      <Text style={s.personChev}>›</Text>
    </Pressable>
  )
}

const s = StyleSheet.create({
  card: {
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
  sectionLabel: { fontFamily: F.condBold, fontSize: 11.5, letterSpacing: 1.4,
                  textTransform: 'uppercase', color: T.muted },
  sectionRight: { fontFamily: F.bodyMed, fontSize: 11.5, color: T.muted2 },
  titleRow: { flexDirection: 'row', alignItems: 'flex-end', gap: T.s3, marginBottom: T.s3 },
  title: { fontFamily: F.condHeavy, fontSize: 32, letterSpacing: 0.4, color: T.white, lineHeight: 34 },
  subText: { fontFamily: F.body, fontSize: 13, color: T.muted, marginTop: 4 },
  subRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4, flexWrap: 'wrap' },
  seg: { flexDirection: 'row', gap: 4, padding: 4, borderRadius: T.r2, minHeight: 46,
         backgroundColor: 'rgba(255,255,255,0.03)', borderWidth: 1, borderColor: T.glassLine,
         marginBottom: T.s3 },
  segItem: { flex: 1, borderRadius: T.r1, alignItems: 'center', justifyContent: 'center',
             borderWidth: 1, borderColor: 'transparent' },
  segOn: { borderColor: `${T.green}55`, backgroundColor: `${T.green}1C` },
  segText: { fontFamily: F.condBlack, fontSize: 14, letterSpacing: 1, textTransform: 'uppercase',
             color: T.muted2 },
  chip: { minHeight: 40, paddingHorizontal: 16, borderRadius: 999, justifyContent: 'center',
          borderWidth: 1, borderColor: T.glassLine, backgroundColor: 'rgba(255,255,255,0.035)' },
  chipOn: { borderColor: `${T.green}77`, backgroundColor: `${T.green}1C` },
  chipText: { fontFamily: F.condBlack, fontSize: 13, letterSpacing: 1, textTransform: 'uppercase',
              color: T.muted },
  btn: { minHeight: 52, borderRadius: T.r2, alignItems: 'center', justifyContent: 'center',
         paddingHorizontal: 18 },
  btnPrimary: { backgroundColor: T.green },
  btnOff: { backgroundColor: 'transparent', borderWidth: 1, borderColor: T.glassLine,
            borderStyle: 'dashed' },
  btnGhost: { borderWidth: 1, borderColor: T.glassLineHi, backgroundColor: T.glass },
  btnQuiet: { minHeight: 44 },
  btnText: { fontFamily: F.condBlack, fontSize: 15, letterSpacing: 1.2, textTransform: 'uppercase' },
  person: { flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 60, paddingHorizontal: 12,
            paddingVertical: 8, borderRadius: T.r2, borderWidth: StyleSheet.hairlineWidth, borderColor: T.glassLine,
            backgroundColor: T.glass, marginBottom: 8 },
  personAvatar: { width: 40, height: 40, borderRadius: 20, backgroundColor: 'rgba(255,255,255,0.07)',
                  alignItems: 'center', justifyContent: 'center' },
  personIni: { fontFamily: F.condBold, fontSize: 14, color: T.muted },
  personName: { fontFamily: F.condBlack, fontSize: 17, color: T.white, letterSpacing: 0.3 },
  personMeta: { fontFamily: F.body, fontSize: 12, color: T.muted, marginTop: 1 },
  personChev: { fontFamily: F.body, fontSize: 22, color: T.muted2 },
  empty: { alignItems: 'center', paddingVertical: 36, paddingHorizontal: 20 },
  emptyTitle: { fontFamily: F.condBold, fontSize: 17, color: T.white, letterSpacing: 0.4,
                textAlign: 'center' },
  emptyHint: { fontFamily: F.body, fontSize: 13.5, color: T.muted, lineHeight: 19,
               textAlign: 'center', marginTop: 8, maxWidth: 300 },
})
