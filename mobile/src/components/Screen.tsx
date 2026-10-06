// The page frame every tab renders inside: the dark ground with the landing
// page's ambient glow behind it, safe-area padding and the 16px side gutter.
// `onRefresh` adds pull-to-refresh in the brand green.
import { ReactNode } from 'react'
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { AmbientGlow } from './Glow'
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
  return (
    <View style={s.root}>
      <AmbientGlow />
      {!scroll ? <View style={[{ flex: 1 }, pad]}>{children}</View> : (
        <ScrollView style={{ flex: 1 }} contentContainerStyle={[s.content, pad]}
                    keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag"
                    refreshControl={onRefresh
                      ? <RefreshControl refreshing={refreshing} onRefresh={onRefresh}
                                        tintColor={T.green} colors={[T.green]} />
                      : undefined}>
          {children}
        </ScrollView>
      )}
    </View>
  )
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: T.bg },
  content: { paddingHorizontal: 16 },
})
