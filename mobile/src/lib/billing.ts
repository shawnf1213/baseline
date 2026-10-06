// Subscribing — the EXISTING Stripe-hosted checkout, opened in the system
// browser (ruling 2). Nothing about payment happens inside the app: the
// backend builds a Checkout Session and returns its URL, and the device's
// default browser takes it from there. Same call the website makes.
//
// NEVER an in-app browser. Linking.openURL hands off to Safari (or whatever
// the default is); it does not use WebBrowser.openBrowserAsync, which would
// render Stripe inside the app and read as an in-app purchase flow to review.
import { Linking } from 'react-native'
import { api } from './api'

export type Plan = 'weekly' | 'monthly'

export type BillingConfig = {
  configured?: boolean
  weekly_price_set?: boolean
  monthly_price_set?: boolean
  [k: string]: unknown
}

export const fetchBillingConfig = () => api.get<BillingConfig>('/api/billing/config')

// Which plans the backend can actually sell right now. A plan whose price is
// not configured is not offered, rather than offered and failing on tap.
export function availablePlans(cfg: BillingConfig | null): Plan[] {
  const out: Plan[] = []
  if (cfg?.weekly_price_set) out.push('weekly')
  if (cfg?.monthly_price_set) out.push('monthly')
  return out
}

export async function openCheckout(plan: Plan, discordId = ''): Promise<void> {
  const r = await api.post<{ url?: string }>('/api/billing/checkout',
    { plan, discord_id: discordId })
  if (!r?.url) throw new Error('Checkout is unavailable right now')
  const ok = await Linking.canOpenURL(r.url)
  if (!ok) throw new Error('No browser available to open checkout')
  await Linking.openURL(r.url)
}
