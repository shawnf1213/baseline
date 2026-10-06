// Signed in, not entitled. Says what Baseline is and leaves it there.
//
// NO PURCHASE WORDING HERE YET. Phase 3 adds the Subscribe button, and only
// on the US storefront; everywhere else this screen is the whole experience
// for a non-member. Written so that nothing on it has to be removed for the
// non-US case — the button is added, not subtracted.
import { useEffect, useState } from 'react'
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native'
import { Redirect, router } from 'expo-router'
import { Screen } from '@/components/Screen'
import { Card, Muted } from '@/components/ui'
import { useSession } from '@/lib/session'
import { isUSStorefront } from '@/lib/region'
import { availablePlans, BillingConfig, fetchBillingConfig, openCheckout, Plan } from '@/lib/billing'
import { F, T } from '@/theme'

// The website's paywall labels, verbatim (AuthGate.jsx).
const PLAN_LABEL: Record<Plan, string> = { weekly: 'Weekly · free trial', monthly: 'Monthly' }

export default function Locked() {
  const { me, refresh, signOut, status } = useSession()
  // ── PHASE 3: SUBSCRIBE, UNITED STATES STOREFRONT ONLY (ruling 2) ─────────
  // Outside the US this screen carries no purchase button and no purchase
  // wording at all; the hooks below are unconditional (React rules) but the
  // fetch is skipped and nothing renders unless the region check passes.
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
  // "Check again" or a foreground re-check can flip this account to active —
  // when it does, the gate moves the user on rather than leaving them here.
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
        <Muted size={13}>
          You're signed in as {who}, but this account doesn't have an active
          Baseline membership right now.
        </Muted>
      </Card>

      <Card style={{ marginTop: T.s3 }}>
        <Text style={s.h}>What Baseline is</Text>
        <Muted size={13}>
          Daily player-prop projections for tennis, NFL and NBA with model-ranked
          boards, a Pick of the Day, a graded public record, and on-demand
          projections for any player and line. Every number is produced by the
          same model that posts to the Baseline Discord.
        </Muted>
      </Card>

      {!inServer && me?.invite_url ? (
        <Muted size={12}>Members need to be in the Baseline Discord server for the
          role to apply.</Muted>
      ) : null}

      {plans.length > 0 ? (
        <Card style={{ marginTop: T.s3 }}>
          <Text style={s.h}>Subscribe</Text>
          <Muted size={12}>Opens secure checkout in your browser. Your membership
            activates here as soon as it completes.</Muted>
          {plans.map(p => (
            <Pressable key={p} onPress={() => buy(p)} disabled={buying !== null}
                       style={[s.btn, s.primary, { marginTop: T.s2 }]}>
              {buying === p ? <ActivityIndicator color="#052e16" />
                            : <Text style={s.primaryText}>{PLAN_LABEL[p]}</Text>}
            </Pressable>
          ))}
          {buyErr ? <Text style={s.err}>{buyErr}</Text> : null}
        </Card>
      ) : null}

      <Pressable onPress={refresh} style={[s.btn, s.ghost]}>
        <Text style={s.ghostText}>Check again</Text>
      </Pressable>
      <Pressable onPress={async () => { await signOut(); router.replace('/welcome') }}
                 style={s.link}>
        <Text style={s.linkText}>Sign out</Text>
      </Pressable>

      <Text style={s.foot}>Projections are for informational purposes only.</Text>
    </Screen>
  )
}

const s = StyleSheet.create({
  brand: { alignItems: 'center', marginTop: T.s5, marginBottom: T.s4 },
  wordmark: { fontFamily: F.condHeavy, fontSize: 30, letterSpacing: 6, color: T.white },
  h: { fontFamily: F.condBlack, fontSize: 18, color: T.white, marginBottom: 6 },
  btn: { minHeight: 48, borderRadius: T.r2, alignItems: 'center', justifyContent: 'center',
         marginTop: T.s4 },
  ghost: { borderWidth: 1, borderColor: T.glassLineHi, backgroundColor: T.glass },
  primary: { backgroundColor: T.green, marginTop: 0 },
  primaryText: { fontFamily: F.condBlack, fontSize: 14, letterSpacing: 1.2,
                 textTransform: 'uppercase', color: '#052e16' },
  err: { color: T.red, fontFamily: F.bodyMed, fontSize: 12.5, marginTop: 8 },
  ghostText: { fontFamily: F.condBlack, fontSize: 14, letterSpacing: 1.2,
               textTransform: 'uppercase', color: T.white },
  link: { alignItems: 'center', paddingVertical: 12 },
  linkText: { fontFamily: F.body, fontSize: 12.5, color: T.muted },
  foot: { color: T.muted2, fontFamily: F.body, fontSize: 11, textAlign: 'center', marginTop: T.s5 },
})
