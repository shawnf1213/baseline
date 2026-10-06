import { Text } from 'react-native'
import { Screen } from '@/components/Screen'
import { Card, Muted, PageTitle } from '@/components/ui'
import { F, T } from '@/theme'

export default function Picks() {
  return (
    <Screen>
      <PageTitle>Picks</PageTitle>
      <Card>
        <Text style={{ fontFamily: F.condBlack, fontSize: 17, color: T.white }}>
          Daily picks and the record
        </Text>
        <Muted>Built in Phase 4 from PicksTab.jsx, reading /api/results/record.</Muted>
      </Card>
    </Screen>
  )
}
