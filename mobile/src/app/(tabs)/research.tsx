// Research — players, search and saved players in one tab (redesign
// proposal: three tabs merged into one). Built in Phase 4 once the direction
// of the Picks and Project screens is approved.
import { Screen } from '@/components/Screen'
import { Empty, PageTitle } from '@/components/ui'

export default function Research() {
  return (
    <Screen>
      <PageTitle sub="Players, search and your saved list">Research</PageTitle>
      <Empty title="Coming in Phase 4"
             hint="Search any player, open their stats and recent matches, and keep a saved list — all in this tab." />
    </Screen>
  )
}
