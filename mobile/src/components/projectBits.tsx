// Small pieces the three projection forms share: a labelled select, a number
// field, the confidence ring, the big-number verdict line, initials.
import { ReactNode } from 'react'
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native'
import { F, T, tier } from '@/theme'
import { tap } from '@/lib/haptics'

export const initials = (name: string) => {
  const t = (name || '').trim().split(/\s+/)
  return ((t[0]?.[0] || '') + (t.length > 1 ? t[t.length - 1][0] : '')).toUpperCase() || '?'
}

export function SelectField({ label, value, onPress, style }:
  { label: string; value: string; onPress: () => void; style?: any }) {
  return (
    <Pressable onPress={() => { tap(); onPress() }} style={[s.select, style]}>
      <Text style={s.fieldK}>{label}</Text>
      <Text style={s.fieldV} numberOfLines={1}>{value}</Text>
      <Text style={s.chev}>⌄</Text>
    </Pressable>
  )
}

export function NumField({ label, value, onChange, placeholder, signed, width = 118 }:
  { label: string; value: string; onChange: (v: string) => void; placeholder: string;
    signed?: boolean; width?: number }) {
  const clean = (v: string) => v.replace(signed ? /[^\d.\-]/g : /[^\d.]/g, '')
  return (
    <View style={[s.select, { width, flexShrink: 0, borderColor: value ? `${T.green}66` : T.glassLine }]}>
      <Text style={s.fieldK}>{label}</Text>
      <TextInput value={value} onChangeText={v => onChange(clean(v))} placeholder={placeholder}
                 placeholderTextColor={T.muted2}
                 keyboardType={signed ? 'numbers-and-punctuation' : 'decimal-pad'}
                 style={s.numInput} returnKeyType="done" />
    </View>
  )
}

export function Tile({ label, name, sub, onPress, onClear }:
  { label: string; name?: string | null; sub?: string; onPress: () => void; onClear: () => void }) {
  if (name) {
    return (
      <View style={[s.tile, s.tileOn]}>
        <Pressable onPress={() => { tap(); onClear() }} hitSlop={10} style={s.clear}>
          <Text style={s.clearX}>×</Text>
        </Pressable>
        <View style={s.avatar}><Text style={s.initials}>{initials(name)}</Text></View>
        <Text style={s.tileName} numberOfLines={2}>{name}</Text>
        <Text style={s.tileSub}>{sub || label}</Text>
      </View>
    )
  }
  return (
    <Pressable onPress={() => { tap(); onPress() }}
               style={({ pressed }) => [s.tile, s.tileEmpty, pressed && { opacity: 0.7 }]}>
      <View style={[s.avatar, s.avatarEmpty]}>
        <Text style={[s.initials, { fontSize: 22, color: T.muted2 }]}>+</Text>
      </View>
      <Text style={[s.tileName, { color: T.muted }]}>{label}</Text>
      <Text style={s.tileSub}>tap to choose</Text>
    </Pressable>
  )
}

// A read-only tile for a fact we looked up rather than a choice (NFL/NBA
// opponent from the schedule).
export function FactTile({ label, name, sub }: { label: string; name?: string | null; sub?: string }) {
  return (
    <View style={[s.tile, s.tileFact]}>
      {name ? (
        <>
          <View style={s.avatar}><Text style={s.initials}>{name.slice(0, 3).toUpperCase()}</Text></View>
          <Text style={s.tileName} numberOfLines={2}>{name}</Text>
          <Text style={s.tileSub}>{sub || label}</Text>
        </>
      ) : (
        <>
          <Text style={{ fontSize: 22, color: T.muted2 }}>?</Text>
          <Text style={[s.tileSub, { textAlign: 'center', marginTop: 8, lineHeight: 15 }]}>{label}</Text>
        </>
      )}
    </View>
  )
}

export function ConfRing({ conf, tone }: { conf: number | null | undefined; tone: string }) {
  if (conf == null) return null
  const tr = tier(conf)
  return (
    <View style={[s.ring, { borderColor: `${tone}66` }]}>
      <Text style={[s.ringNum, { color: tone }]}>{Math.round(conf)}</Text>
      <Text style={s.ringK}>{tr.label ? tr.label.toLowerCase() : 'conf.'}</Text>
    </View>
  )
}

export function BigLine({ value, badge, tone, children }:
  { value: string; badge: string; tone: string; children?: ReactNode }) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
      <Text style={s.bigNum}>{value}</Text>
      <View style={[s.leanPill, { borderColor: `${tone}55`, backgroundColor: `${tone}1C` }]}>
        <Text style={[s.leanText, { color: tone }]}>{badge}</Text>
      </View>
      {children}
    </View>
  )
}

export const pb = StyleSheet.create({
  k: { fontFamily: F.condBold, fontSize: 10.5, letterSpacing: 1.3, textTransform: 'uppercase', color: T.muted2 },
  under: { fontFamily: F.body, fontSize: 12.5, color: T.muted, marginTop: 6 },
  headName: { fontFamily: F.condBlack, fontSize: 20, color: T.white },
  verdict: { marginTop: T.s3, borderWidth: 1 },
  read: { fontFamily: F.body, fontSize: 13.5, color: T.muted, lineHeight: 19 },
  vs: { alignSelf: 'center', width: 34, height: 34, borderRadius: 17, alignItems: 'center',
        justifyContent: 'center', backgroundColor: 'rgba(255,255,255,0.05)', borderWidth: 1, borderColor: T.glassLine },
  vsText: { fontFamily: F.condBold, fontSize: 12, letterSpacing: 0.6, color: T.muted },
  sched: { fontFamily: F.body, fontSize: 12, color: T.muted2, lineHeight: 17, marginTop: 8 },
})

const s = StyleSheet.create({
  select: { minHeight: 56, borderRadius: T.r1, borderWidth: 1, borderColor: T.glassLine,
            backgroundColor: 'rgba(255,255,255,0.03)', paddingHorizontal: 12, paddingVertical: 8,
            justifyContent: 'center' },
  fieldK: { fontFamily: F.condBold, fontSize: 9, letterSpacing: 1.2, textTransform: 'uppercase', color: T.muted2 },
  fieldV: { fontFamily: F.bodySemi, fontSize: 16, color: T.white, marginTop: 3, paddingRight: 18 },
  chev: { position: 'absolute', right: 12, top: 14, fontSize: 18, color: T.muted2 },
  numInput: { fontFamily: F.condHeavy, fontSize: 22, color: T.white, padding: 0, marginTop: 1, minHeight: 26 },
  tile: { flex: 1, minWidth: 0, minHeight: 124, paddingVertical: 14, paddingHorizontal: 8, borderRadius: T.r2,
          alignItems: 'center', justifyContent: 'center' },
  tileOn: { backgroundColor: 'rgba(255,255,255,0.04)', borderWidth: 1, borderColor: T.glassLine },
  tileEmpty: { borderWidth: 1.5, borderStyle: 'dashed', borderColor: T.glassLine },
  tileFact: { backgroundColor: 'rgba(255,255,255,0.02)', borderWidth: 1, borderStyle: 'dashed', borderColor: T.glassLine },
  clear: { position: 'absolute', top: 4, right: 8, minWidth: 32, minHeight: 32, alignItems: 'center', justifyContent: 'center' },
  clearX: { fontSize: 20, color: T.muted2, lineHeight: 22 },
  avatar: { width: 50, height: 50, borderRadius: 25, backgroundColor: 'rgba(255,255,255,0.07)',
            alignItems: 'center', justifyContent: 'center' },
  avatarEmpty: { borderWidth: 1.5, borderStyle: 'dashed', borderColor: T.glassLineHi, backgroundColor: 'transparent' },
  initials: { fontFamily: F.condBold, fontSize: 17, color: T.white },
  tileName: { fontFamily: F.bodySemi, fontSize: 14, color: T.white, marginTop: 8, textAlign: 'center', lineHeight: 18 },
  tileSub: { fontFamily: F.body, fontSize: 11, color: T.muted2, marginTop: 2 },
  ring: { width: 80, height: 80, borderRadius: 40, borderWidth: 5, alignItems: 'center', justifyContent: 'center' },
  ringNum: { fontFamily: F.condHeavy, fontSize: 26, lineHeight: 28 },
  ringK: { fontFamily: F.condBold, fontSize: 9, letterSpacing: 1, textTransform: 'uppercase', color: T.muted2 },
  bigNum: { fontFamily: F.condHeavy, fontSize: 54, lineHeight: 56, color: T.white, letterSpacing: -1 },
  leanPill: { borderWidth: 1, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 3 },
  leanText: { fontFamily: F.condBlack, fontSize: 16, letterSpacing: 1, textTransform: 'uppercase' },
})
