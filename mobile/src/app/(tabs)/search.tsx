import { Text } from 'react-native'
import { Screen } from '@/components/Screen'
import { Card, Muted, PageTitle } from '@/components/ui'
import { F, T } from '@/theme'

export default function Search() {
  return (
    <Screen>
      <PageTitle>Search</PageTitle>
      <Card>
        <Text style={{ fontFamily: F.condBlack, fontSize: 17, color: T.white }}>
          Find a player
        </Text>
        <Muted>Built in Phase 4 from SearchTab.jsx.</Muted>
      </Card>
    </Screen>
  )
}
