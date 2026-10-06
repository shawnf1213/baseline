// Signed in, not entitled. Says what Baseline is and, on the US storefront
// only, offers the way in (ruling 2). Nothing on it has to be removed for the
// non-US case — the Subscribe card is added, not subtracted.
import { useEffect, useState } from 'react'
import { StyleSheet, Text, View } from 'react-native'
import { Redirect, router } from 'expo-router'
import { Screen } from '@/components/Screen'
import { Button, Card, Disclaimer, Muted } from '@/components/ui'
import { useSession } from '@/lib/session'
import { isUSStorefront } from '@/lib/region'
import { availablePlans, BillingConfig, fetchBillingConfig, openCheckout, Plan } from '@/lib/billing'
import { F, T } from '@/theme'
import { FooterLinks } from './welcome'

const PLAN_LABEL: Record<Plan, string> = { weekly: 'Weekly · free trial', monthly: 'Monthly' }

export default function Locked() {
  const { me, refresh, signOut, status } = useSession()
  const us = isUSStorefront()
  const [cfg, setCfg] = useState<BillingConfig | null>(null)
  const [buying, setBuying] = useState<Plan | null>(null)
  const [buyErr, setBuyErr] = useState<string | null>(null)
  useEffect(() => {
    if (!us) return
    let alive = true
    fetchBillingConfig().then(c => { if (alive) setCfg(c) }).catch(() => {})
    return () => { alive = false }
  }, [us])
  const plans = us ? availablePlans(cfg) : []
  const buy = async (plan: Plan) => {
    setBuying(plan); setBuyErr(null)
    try { await openCheckout(plan, me?.discord_id || '') }
    catch (e: any) { setBuyErr(e?.body?.detail || e?.message || 'Could not open checkout') }
    finally { setBuying(null) }
  }
  if (status === 'active') return <Redirect href="/(tabs)" />
  if (status === 'signed-out') return <Redirect href="/welcome" />
  const who = me?.username || me?.email || 'your account'
  const inServer = me?.reason !== 'not_in_server'

  return (
    <Screen>
      <View style={s.brand}>
        <Text style={s.wordmark}>BASE<Text style={{ color: T.green }}>LINE</Text></Text>
      </View>

      <Card>
        <Text style={s.h}>No active membership</Text>
        <Muted size={13}>You're signed in as {who}, but this account doesn't have an active Baseline membership right now.</Muted>
      </Card>

      <Card style={{ marginTop: T.s3 }}>
        <Text style={s.h}>What Baseline is</Text>
        <Muted size={13}>
          Daily player-prop projections for tennis, NFL and NBA with model-ranked boards, a Pick of the Day,
          a graded public record, and on-demand projections for any player and line.
        </Muted>
      </Card>

      {!inServer && me?.invite_url ? (
        <Muted size={12} style={{ marginTop: 10 }}>Members need to be in the Baseline Discord server for the role to apply.</Muted>
      ) : null}

      {plans.length > 0 ? (
        <Card style={{ marginTop: T.s3 }}>
          <Text style={s.h}>Subscribe</Text>
          <Muted size={12}>Opens secure checkout in your browser. Your membership activates here as soon as it completes.</Muted>
          {plans.map(p => (
            <Button key={p} label={PLAN_LABEL[p]} onPress={() => buy(p)} busy={buying === p} disabled={buying !== null} style={{ marginTop: T.s2 }} />
          ))}
          {buyErr ? <Text style={s.err}>{buyErr}</Text> : null}
        </Card>
      ) : null}

      <Button label="Check again" kind="ghost" onPress={refresh} style={{ marginTop: T.s4 }} />
      <Button label="Sign out" kind="quiet" onPress={async () => { await signOut(); router.replace('/welcome') }} />

      <Disclaimer />
      <FooterLinks />
    </Screen>
  )
}

const s = StyleSheet.create({
  brand: { alignItems: 'center', marginTop: T.s5, marginBottom: T.s4 },
  wordmark: { fontFamily: F.condHeavy, fontSize: 30, letterSpacing: 6, color: T.white },
  h: { fontFamily: F.condBlack, fontSize: 18, color: T.white, marginBottom: 6 },
  err: { color: T.red, fontFamily: F.bodyMed, fontSize: 12.5, marginTop: 8 },
})
