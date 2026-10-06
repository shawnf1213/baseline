// The bottom sheet every detail view opens in. A native iOS page sheet (swipe
// down to dismiss, the screen behind recedes) rather than a custom gesture
// view: it is what the OS does, it needs no extra native module, and it works
// in Expo Go. `onRequestClose` covers the swipe and the Android back button;
// the Done button covers everyone else.
import { ReactNode } from 'react'
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { F, T } from '@/theme'

type Props = {
  open: boolean
  onClose: () => void
  title?: ReactNode
  children: ReactNode
  footer?: ReactNode
  scroll?: boolean
}

export function Sheet({ open, onClose, title, children, footer, scroll = true }: Props) {
  const insets = useSafeAreaInsets()
  const body = (
    <>
      {children}
      <View style={{ height: 24 }} />
    </>
  )
  return (
    <Modal visible={open} animationType="slide" presentationStyle="pageSheet"
           onRequestClose={onClose}>
      <View style={s.root}>
        <View style={s.grabber} />
        <View style={s.head}>
          <View style={{ flex: 1, minWidth: 0 }}>
            {typeof title === 'string' ? <Text style={s.title} numberOfLines={1}>{title}</Text> : title}
          </View>
          <Pressable onPress={onClose} hitSlop={8} style={s.done}>
            <Text style={s.doneText}>Done</Text>
          </Pressable>
        </View>
        {scroll ? (
          <ScrollView style={{ flex: 1 }} contentContainerStyle={s.content}
                      keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag">
            {body}
          </ScrollView>
        ) : <View style={[s.content, { flex: 1 }]}>{children}</View>}
        {footer ? (
          <View style={[s.footer, { paddingBottom: Math.max(insets.bottom, 12) }]}>{footer}</View>
        ) : null}
      </View>
    </Modal>
  )
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: T.bgElev },
  grabber: { alignSelf: 'center', width: 36, height: 5, borderRadius: 3,
             backgroundColor: 'rgba(255,255,255,0.22)', marginTop: 8 },
  head: { flexDirection: 'row', alignItems: 'center', gap: 12,
          paddingHorizontal: 16, paddingTop: 10, paddingBottom: 8 },
  title: { fontFamily: F.condBlack, fontSize: 19, color: T.white, letterSpacing: 0.3 },
  done: { minHeight: 44, minWidth: 56, alignItems: 'flex-end', justifyContent: 'center' },
  doneText: { fontFamily: F.bodySemi, fontSize: 15, color: T.green },
  content: { paddingHorizontal: 16 },
  footer: { paddingHorizontal: 16, paddingTop: 10,
            borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: T.glassLine,
            backgroundColor: T.bgElev },
})
