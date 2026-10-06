// Boards — placeholder until Phase 4. Styled with the real primitives so the
// shell is checkable against the website now, not after the screens exist.
import { Text } from 'react-native'
import { Screen } from '@/components/Screen'
import { Card, Muted, PageTitle, Pill, SectionLabel } from '@/components/ui'
import { F, T } from '@/theme'

export default function Boards() {
  return (
    <Screen>
      <PageTitle sub={<Pill live>Live</Pill>}>Board</PageTitle>
      <SectionLabel right="Phase 4">Full board</SectionLabel>
      <Card>
        <Text style={{ fontFamily: F.condBlack, fontSize: 17, color: T.white }}>
          Live PrizePicks & Underdog lines
        </Text>
        <Muted>Priced by the backend. Built in Phase 4 from BoardTab.jsx.</Muted>
      </Card>
    </Screen>
  )
}
