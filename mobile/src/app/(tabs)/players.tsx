import { Text } from 'react-native'
import { Screen } from '@/components/Screen'
import { Card, Muted, PageTitle } from '@/components/ui'
import { F, T } from '@/theme'

export default function Players() {
  return (
    <Screen>
      <PageTitle>Players</PageTitle>
      <Card>
        <Text style={{ fontFamily: F.condBlack, fontSize: 17, color: T.white }}>
          Player dashboard
        </Text>
        <Muted>Built in Phase 4 from PlayersTab.jsx and PlayerDashboard.jsx.</Muted>
      </Card>
    </Screen>
  )
}
