// The six tabs of the PWA's BottomNav, in the same order with the same labels:
// Boards · Project · Picks · Players · Search · Saved. The account sheet is not
// a tab on the web either; it opens from a header control (Phase 4).
import { Redirect, Tabs } from 'expo-router'
import { Platform, Text, View } from 'react-native'
import { F, T } from '@/theme'
import { useSession } from '@/lib/session'

const TABS = [
  { name: 'index',    label: 'Boards',  glyph: '▦' },
  { name: 'project',  label: 'Project', glyph: '◎' },
  { name: 'picks',    label: 'Picks',   glyph: '★' },
  { name: 'players',  label: 'Players', glyph: '●' },
  { name: 'search',   label: 'Search',  glyph: '⌕' },
  { name: 'saved',    label: 'Saved',   glyph: '♡' },
] as const

export default function TabsLayout() {
  // THE MEMBER GATE. Nothing under (tabs) renders for anyone but an active
  // member; the other two states are sent to their own screens. `loading`
  // shows the ground colour rather than a flash of either.
  const { status } = useSession()
  if (status === 'loading') return <View style={{ flex: 1, backgroundColor: T.bg }} />
  if (status === 'signed-out') return <Redirect href="/sign-in" />
  if (status === 'locked') return <Redirect href="/locked" />
  return (
    <Tabs screenOptions={{
      headerShown: false,
      tabBarActiveTintColor: T.green,
      tabBarInactiveTintColor: T.muted2,
      tabBarStyle: {
        backgroundColor: T.bgElev, borderTopColor: T.glassLine,
        borderTopWidth: 1, height: Platform.OS === 'ios' ? 84 : 64,
      },
      tabBarLabelStyle: { fontFamily: F.condBold, fontSize: 10.5, letterSpacing: 0.8,
                          textTransform: 'uppercase' },
      sceneStyle: { backgroundColor: T.bg },
    }}>
      {TABS.map(t => (
        <Tabs.Screen key={t.name} name={t.name} options={{
          title: t.label,
          tabBarIcon: ({ color }) =>
            <Text style={{ color, fontSize: 18, lineHeight: 22 }}>{t.glyph}</Text>,
        }} />
      ))}
    </Tabs>
  )
}
