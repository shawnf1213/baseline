// A list of options in a sheet — the phone's answer to a dropdown. Every row
// is a full-width 52pt target and the current choice is marked.
import { Pressable, StyleSheet, Text, View } from 'react-native'
import { Sheet } from './Sheet'
import { F, T } from '@/theme'
import { select } from '@/lib/haptics'

export type Option<V extends string> = { value: V; label: string; sub?: string }

export function OptionSheet<V extends string>({ open, title, options, value, onSelect, onClose }:
  { open: boolean; title: string; options: Option<V>[]; value: V;
    onSelect: (v: V) => void; onClose: () => void }) {
  return (
    <Sheet open={open} onClose={onClose} title={title}>
      <View style={{ marginTop: 4 }}>
        {options.map(o => {
          const on = o.value === value
          return (
            <Pressable key={o.value} onPress={() => { select(); onSelect(o.value); onClose() }}
                       style={({ pressed }) => [s.row, on && s.rowOn, pressed && { opacity: 0.8 }]}>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={[s.label, on && { color: T.green }]} numberOfLines={1}>{o.label}</Text>
                {o.sub ? <Text style={s.sub} numberOfLines={1}>{o.sub}</Text> : null}
              </View>
              {on ? <Text style={s.check}>✓</Text> : null}
            </Pressable>
          )
        })}
      </View>
    </Sheet>
  )
}

const s = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 52, paddingHorizontal: 14,
         borderRadius: T.r1, marginBottom: 4, borderWidth: 1, borderColor: 'transparent' },
  rowOn: { backgroundColor: `${T.green}14`, borderColor: `${T.green}44` },
  label: { fontFamily: F.bodyMed, fontSize: 16, color: T.white },
  sub: { fontFamily: F.body, fontSize: 11.5, color: T.muted2, marginTop: 1 },
  check: { fontFamily: F.bodySemi, fontSize: 17, color: T.green },
})
