// Three tabs (redesign proposal, replacing the website's six):
//   Picks     Baseline's board — Pick of the Day first, then everything else
//   Project   price any matchup
//   Research  players, search and saved players in one place (Phase 4)
// The account sheet opens from a control on the Picks header in Phase 4, not
// from a tab.
import { Redirect, Tabs } from 'expo-router'
import { Platform, Text, View } from 'react-native'
import { F, T } from '@/theme'
import { useSession } from '@/lib/session'

const TABS = [
  { name: 'index',    label: 'Picks',    glyph: '★' },
  { name: 'project',  label: 'Project',  glyph: '◎' },
  { name: 'research', label: 'Research', glyph: '⌕' },
] as const

export default function TabsLayout() {
  // THE MEMBER GATE. Nothing under (tabs) renders for anyone but an active
  // member; the other two states are sent to their own screens. `loading`
  // shows the ground colour rather than a flash of either.
  const { status } = useSession()
  if (status === 'loading') return <View style={{ flex: 1, backgroundColor: T.bg }} />
  if (status === 'signed-out') return <Redirect href="/welcome" />
  if (status === 'locked') return <Redirect href="/locked" />
  return (
    <Tabs screenOptions={{
      headerShown: false,
      tabBarActiveTintColor: T.green,
      tabBarInactiveTintColor: T.muted2,
      tabBarStyle: {
        backgroundColor: T.bgElev, borderTopColor: T.glassLine,
        borderTopWidth: 1, height: Platform.OS === 'ios' ? 86 : 66, paddingTop: 6,
      },
      tabBarLabelStyle: { fontFamily: F.condBold, fontSize: 11, letterSpacing: 0.9,
                          textTransform: 'uppercase' },
      sceneStyle: { backgroundColor: T.bg },
    }}>
      {TABS.map(t => (
        <Tabs.Screen key={t.name} name={t.name} options={{
          title: t.label,
          tabBarIcon: ({ color }) =>
            <Text style={{ color, fontSize: 20, lineHeight: 24 }}>{t.glyph}</Text>,
        }} />
      ))}
    </Tabs>
  )
}
