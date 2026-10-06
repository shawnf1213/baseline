// Four tabs, Board first (operator, 2026-10-06):
//   Board     the live player board, priced by the model — opens on launch
//   Project   price any matchup
//   Picks     Baseline's ranked picks — Pick of the Day first
//   Research  players, search and saved players in one place
// The sport switch sits at the top of Board, Project, Picks and Research and
// is shared. The account control is in every tab's title row.
import { Redirect, Tabs } from 'expo-router'
import { Platform, Text, View } from 'react-native'
import { SymbolView } from 'expo-symbols'
import { F, T } from '@/theme'
import { useSession } from '@/lib/session'
import type { Symbol } from '@/components/ui'

const TABS: { name: string; label: string; icon: Symbol; glyph: string }[] = [
  { name: 'index',    label: 'Board',    icon: 'list.bullet.rectangle.portrait.fill', glyph: '▦' },
  { name: 'project',  label: 'Project',  icon: 'scope',                               glyph: '◎' },
  { name: 'picks',    label: 'Picks',    icon: 'star.fill',                           glyph: '★' },
  { name: 'research', label: 'Research', icon: 'magnifyingglass',                     glyph: '⌕' },
]

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
        backgroundColor: '#0b0b0b', borderTopColor: T.glassLine,
        borderTopWidth: 1, height: Platform.OS === 'ios' ? 88 : 66, paddingTop: 7,
      },
      tabBarLabelStyle: { fontFamily: F.condBold, fontSize: 11, letterSpacing: 0.9,
                          textTransform: 'uppercase', marginTop: 2 },
      sceneStyle: { backgroundColor: T.bg },
    }}>
      {TABS.map(t => (
        <Tabs.Screen key={t.name} name={t.name} options={{
          title: t.label,
          tabBarIcon: ({ color, focused }) => (
            <SymbolView name={t.icon} size={focused ? 25 : 23} tintColor={color} type="monochrome"
                        style={{ width: 26, height: 26 }}
                        fallback={<Text style={{ color, fontSize: 20, lineHeight: 24 }}>{t.glyph}</Text>} />
          ),
        }} />
      ))}
    </Tabs>
  )
}
