// Is this device on the United States storefront? (ruling 2 / Phase 3)
//
// WHAT THIS ACTUALLY READS, stated plainly: the device's region setting via
// expo-localization. The App Store storefront itself is exposed only through
// StoreKit (Storefront.current), which needs an in-app-purchase library this
// app deliberately does not carry. Region is the closest honest proxy — for
// the overwhelming majority of devices the two agree, and the failure mode is
// conservative: a US member with a non-US region setting sees sign-in only,
// which is the safe direction for review. A US-region device on a non-US
// storefront is the rare reverse case and is the residual risk here.
//
// Read once per app launch. A region change mid-session is not a thing worth
// reacting to.
import { getLocales } from 'expo-localization'

let _cached: boolean | null = null

export function isUSStorefront(): boolean {
  if (_cached !== null) return _cached
  try {
    const locales = getLocales()
    const region = (locales[0]?.regionCode || '').toUpperCase()
    _cached = region === 'US'
  } catch {
    // If the region cannot be read, do NOT show purchase UI. Unknown is
    // treated as non-US, never as US.
    _cached = false
  }
  return _cached
}
