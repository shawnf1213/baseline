// Light in the dark — the landing page's mesh, on the phone.
//
// AmbientGlow sits behind a whole screen: a green bloom top-left and a cool
// blue one top-right, exactly the website's two radial gradients. CardGlow
// lights a single card from one corner in its own colour (the lean, the
// surface). Both are pure SVG, drawn once, never animated — depth without a
// per-frame cost.
import { StyleSheet, View } from 'react-native'
import Svg, { Defs, RadialGradient, Rect, Stop } from 'react-native-svg'
import { T } from '@/theme'

let seq = 0
const uid = (p: string) => `${p}${++seq}`

export function AmbientGlow({ height = 560 }: { height?: number }) {
  const a = uid('ga'), b = uid('gb')
  return (
    <View pointerEvents="none" style={{ position: 'absolute', left: 0, right: 0, top: 0, height }}>
      <Svg width="100%" height="100%">
        <Defs>
          <RadialGradient id={a} cx="8%" cy="0%" rx="85%" ry="62%" fx="8%" fy="0%">
            <Stop offset="0" stopColor={T.green} stopOpacity={0.16} />
            <Stop offset="1" stopColor={T.green} stopOpacity={0} />
          </RadialGradient>
          <RadialGradient id={b} cx="100%" cy="6%" rx="70%" ry="50%" fx="100%" fy="6%">
            <Stop offset="0" stopColor={T.blue} stopOpacity={0.10} />
            <Stop offset="1" stopColor={T.blue} stopOpacity={0} />
          </RadialGradient>
        </Defs>
        <Rect x="0" y="0" width="100%" height="100%" fill={`url(#${a})`} />
        <Rect x="0" y="0" width="100%" height="100%" fill={`url(#${b})`} />
      </Svg>
    </View>
  )
}

export function CardGlow({ color, strength = 0.22, corner = 'tl' }:
  { color: string; strength?: number; corner?: 'tl' | 'tr' | 'bl' }) {
  const id = uid('gc')
  const [cx, cy] = corner === 'tr' ? ['100%', '0%'] : corner === 'bl' ? ['0%', '100%'] : ['0%', '0%']
  return (
    <View pointerEvents="none" style={StyleSheet.absoluteFill}>
      <Svg width="100%" height="100%">
        <Defs>
          <RadialGradient id={id} cx={cx} cy={cy} rx="95%" ry="110%" fx={cx} fy={cy}>
            <Stop offset="0" stopColor={color} stopOpacity={strength} />
            <Stop offset="1" stopColor={color} stopOpacity={0} />
          </RadialGradient>
        </Defs>
        <Rect x="0" y="0" width="100%" height="100%" fill={`url(#${id})`} />
      </Svg>
    </View>
  )
}
