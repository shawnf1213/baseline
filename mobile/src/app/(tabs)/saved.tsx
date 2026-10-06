import { Text } from 'react-native'
import { Screen } from '@/components/Screen'
import { Card, Muted, PageTitle } from '@/components/ui'
import { F, T } from '@/theme'

export default function Saved() {
  return (
    <Screen>
      <PageTitle>Saved</PageTitle>
      <Card>
        <Text style={{ fontFamily: F.condBlack, fontSize: 17, color: T.white }}>
          Bookmarks
        </Text>
        <Muted>On-device only (ruling 4). Built in Phase 4 from ResearchTab.jsx.</Muted>
      </Card>
    </Screen>
  )
}
