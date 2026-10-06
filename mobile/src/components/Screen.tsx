// The page frame every tab renders inside: the dark ground, safe-area padding
// and the 16px side gutter. `onRefresh` adds pull-to-refresh in the brand
// green; the tab bar's own height is handled by the navigator.
import { ReactNode } from 'react'
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { T } from '@/theme'

type Props = {
  children: ReactNode
  scroll?: boolean
  refreshing?: boolean
  onRefresh?: () => void
}

export function Screen({ children, scroll = true, refreshing = false, onRefresh }: Props) {
  const insets = useSafeAreaInsets()
  const pad = { paddingTop: insets.top + T.s3, paddingBottom: T.s5 }
  if (!scroll) return <View style={[s.root, pad]}>{children}</View>
  return (
    <ScrollView style={s.root} contentContainerStyle={[s.content, pad]}
                keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag"
                refreshControl={onRefresh
                  ? <RefreshControl refreshing={refreshing} onRefresh={onRefresh}
                                    tintColor={T.green} colors={[T.green]} />
                  : undefined}>
      {children}
    </ScrollView>
  )
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: T.bg },
  content: { paddingHorizontal: 16 },
})
