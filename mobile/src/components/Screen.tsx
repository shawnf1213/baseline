// The page frame every tab renders inside: the dark ground, safe-area padding
// and the 16px side gutter the website uses. Keeping the frame in one place is
// how six tabs end up with one set of margins instead of six.
import { ReactNode } from 'react'
import { ScrollView, StyleSheet, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { T } from '@/theme'

export function Screen({ children, scroll = true }: { children: ReactNode; scroll?: boolean }) {
  const insets = useSafeAreaInsets()
  const pad = { paddingTop: insets.top + T.s3, paddingBottom: T.s5 }
  if (!scroll) return <View style={[s.root, pad]}>{children}</View>
  return (
    <ScrollView style={s.root} contentContainerStyle={[s.content, pad]}
                keyboardShouldPersistTaps="handled">
      {children}
    </ScrollView>
  )
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: T.bg },
  content: { paddingHorizontal: 16 },
})
