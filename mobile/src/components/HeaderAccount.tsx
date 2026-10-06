// The account control every tab's title carries — the button and the sheet it
// opens, together, so no screen has to hold the sheet's state itself.
import { useState } from 'react'
import { AccountButton, AccountSheet } from './AccountSheet'

export function HeaderAccount() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <AccountButton onPress={() => setOpen(true)} />
      <AccountSheet open={open} onClose={() => setOpen(false)} />
    </>
  )
}
