// The six tabs of the PWA's BottomNav, in the same order with the same labels:
// Boards · Project · Picks · Players · Search · Saved. The account sheet is not
// a tab on the web either; it opens from a header control (Phase 4).
import { Tabs } from 'expo-router'
import { Platform, Text } from 'react-native'
import { F, T } from '@/theme'

const TABS = [
  { name: 'index',    label: 'Boards',  glyph: '▦' },
  { name: 'project',  label: 'Project', glyph: '◎' },
  { name: 'picks',    label: 'Picks',   glyph: '★' },
  { name: 'players',  label: 'Players', glyph: '●' },
  { name: 'search',   label: 'Search',  glyph: '⌕' },
  { name: 'saved',    label: 'Saved',   glyph: '♡' },
] as const

export default function TabsLayout() {
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
