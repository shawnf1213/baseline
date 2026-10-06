// Loading placeholders in the shape of the content they stand in for, instead
// of a spinner (operator redesign brief). A pulsing block where a card will be
// tells the reader what is coming and how much of it; a spinner tells them to
// wait.
import { useEffect, useRef } from 'react'
import { Animated, DimensionValue, StyleSheet, View, ViewStyle } from 'react-native'
import { T } from '@/theme'

export function Skeleton({ w = '100%', h = 14, r = 6, style }:
  { w?: DimensionValue; h?: number; r?: number; style?: ViewStyle }) {
  const op = useRef(new Animated.Value(0.35)).current
  useEffect(() => {
    const loop = Animated.loop(Animated.sequence([
      Animated.timing(op, { toValue: 0.7, duration: 700, useNativeDriver: true }),
      Animated.timing(op, { toValue: 0.35, duration: 700, useNativeDriver: true }),
    ]))
    loop.start()
    return () => loop.stop()
  }, [op])
  return <Animated.View style={[s.block, { width: w, height: h, borderRadius: r, opacity: op }, style]} />
}

// A pick card's silhouette: name, call line, a number on the right.
export function SkeletonCard({ hero }: { hero?: boolean }) {
  return (
    <View style={[s.card, hero && { paddingVertical: 20 }]}>
      <View style={{ flex: 1, gap: 9 }}>
        {hero ? <Skeleton w={110} h={10} /> : null}
        <Skeleton w="62%" h={hero ? 22 : 17} />
        <Skeleton w="48%" h={12} />
      </View>
      <Skeleton w={44} h={hero ? 36 : 26} r={8} />
    </View>
  )
}

const s = StyleSheet.create({
  block: { backgroundColor: 'rgba(255,255,255,0.10)' },
  card: {
    flexDirection: 'row', alignItems: 'center', gap: 14,
    borderWidth: StyleSheet.hairlineWidth, borderColor: T.glassLine, borderRadius: T.r3,
    backgroundColor: T.glass, paddingHorizontal: 16, paddingVertical: 14, marginBottom: 10,
  },
})
