// Circular gauges — confidence on every card, hit rates in the form windows.
// A number you can see the size of before you read it.
import { ReactNode } from 'react'
import { StyleSheet, Text, View } from 'react-native'
import Svg, { Circle } from 'react-native-svg'
import { F, T, tier } from '@/theme'

type RingProps = {
  value: number | null | undefined   // 0–100
  size?: number
  stroke?: number
  tone?: string
  dashed?: boolean                    // empty, dashed track (pending)
  children?: ReactNode
}

export function Ring({ value, size = 44, stroke = 4, tone = T.green, dashed, children }: RingProps) {
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const pct = value == null || dashed ? 0 : Math.max(0, Math.min(100, value)) / 100
  const mid = size / 2
  return (
    <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}>
      <Svg width={size} height={size} style={StyleSheet.absoluteFill}>
        <Circle cx={mid} cy={mid} r={r} fill="none" stroke="rgba(255,255,255,0.09)" strokeWidth={stroke}
                strokeDasharray={dashed ? '2 5' : undefined} strokeLinecap="round" />
        {pct > 0 ? (
          <Circle cx={mid} cy={mid} r={r} fill="none" stroke={tone} strokeWidth={stroke}
                  strokeDasharray={`${c * pct} ${c}`} strokeLinecap="round"
                  transform={`rotate(-90 ${mid} ${mid})`} />
        ) : null}
      </Svg>
      {children}
    </View>
  )
}

// Confidence: the number inside, the tier word under it on the larger sizes.
export function ConfRing({ conf, size = 46, stroke, label = true, state }:
  { conf: number | null | undefined; size?: number; stroke?: number; label?: boolean;
    state?: 'pending' | 'nodata' }) {
  const tr = tier(conf)
  const sw = stroke ?? Math.max(3, Math.round(size / 12))
  if (state === 'pending') {
    return <Ring value={null} size={size} stroke={sw} dashed>
      <Text style={[r.num, { fontSize: size * 0.3, color: T.muted2 }]}>···</Text>
    </Ring>
  }
  if (state === 'nodata' || conf == null) {
    return <Ring value={null} size={size} stroke={sw}>
      <Text style={[r.num, { fontSize: size * 0.34, color: T.muted2 }]}>—</Text>
    </Ring>
  }
  const big = size >= 64
  return (
    <Ring value={conf} size={size} stroke={sw} tone={tr.tone}>
      <Text style={[r.num, { fontSize: big ? size * 0.32 : size * 0.36, color: T.white }]}>{Math.round(conf)}</Text>
      {big && label ? <Text style={r.lab}>{tr.label ? tr.label.toLowerCase() : 'conf.'}</Text> : null}
    </Ring>
  )
}

const r = StyleSheet.create({
  num: { fontFamily: F.condHeavy, lineHeight: undefined, includeFontPadding: false },
  lab: { fontFamily: F.condBold, fontSize: 9.5, letterSpacing: 1, textTransform: 'uppercase', color: T.muted2, marginTop: -2 },
})
