// Welcome — the first screen a brand-new download sees (operator, Task 2).
//
// WHY IT EXISTS. Non-members cannot sign in (the email code only goes to a
// subscriber's inbox, and Discord sign-in without the role lands on the locked
// screen), so without this a new user could never reach a Subscribe option.
// This screen is reachable with NO session at all: what Baseline does, the
// public record, and — on the US storefront only — the way in.
//
// NON-US: description, record and sign-in. No purchase button, no purchase
// wording. The two branches are additive: nothing is removed for non-US, a
// card is added for US.
//
// THE RECORD IS THE PUBLIC ONE. /api/results/summary is the endpoint the
// landing page reads, deliberately public, a few hundred bytes. Both the
// headline and the all-time figure are shown, because that endpoint's own
// contract says quoting one without the other is selecting a number rather
// than reporting one.
import { useEffect, useState } from 'react'
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native'
import { Redirect, router } from 'expo-router'
import { Screen } from '@/components/Screen'
import { Card, Muted } from '@/components/ui'
import { useSession } from '@/lib/session'
import { fetchSummary } from '@/lib/api'
import { isUSStorefront } from '@/lib/region'
import { availablePlans, BillingConfig, fetchBillingConfig, openCheckout, Plan } from '@/lib/billing'
import { F, T } from '@/theme'

const PLAN_LABEL: Record<Plan, string> = { weekly: 'Weekly · free trial', monthly: 'Monthly' }

type Tally = { wins: number; losses: number; total: number; win_rate: number | null }
type Summary = {
  ready?: boolean
  all_time?: Tally
  live_props?: Tally
  recent?: Tally
  recent_days?: number
  days_active?: number
  potd_month?: { month: string; wins: number; losses: number; tracked: number; win_rate: number | null }
}

function monthName(ym?: string) {
  if (!ym) return 'This month'
  const [y, m] = ym.split('-').map(Number)
  return new Date(Date.UTC(y, (m || 1) - 1, 1)).toLocaleDateString(undefined,
    { month: 'long', timeZone: 'UTC' })
}

function Stat({ label, t, sub }: { label: string; t?: Tally | null; sub?: string }) {
  const has = t && t.total > 0 && t.win_rate != null
  return (
    <View style={s.stat}>
      <Text style={s.statLabel}>{label}</Text>
      <Text style={[s.statNum, { color: has && (t!.win_rate! >= 50) ? T.green : T.white }]}>
        {has ? `${t!.win_rate!.toFixed(1)}%` : '—'}
      </Text>
      <Text style={s.statSub}>{has ? `${t!.wins}-${t!.losses}` : 'no graded plays'}{sub ? ` · ${sub}` : ''}</Text>
    </View>
  )
}

export default function Welcome() {
  const { status } = useSession()
  const us = isUSStorefront()
  const [sum, setSum] = useState<Summary | null>(null)
  const [cfg, setCfg] = useState<BillingConfig | null>(null)
  const [buying, setBuying] = useState<Plan | null>(null)
  const [buyErr, setBuyErr] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    fetchSummary().then(d => { if (alive) setSum(d) }).catch(() => { if (alive) setSum({ ready: false }) })
    if (us) fetchBillingConfig().then(c => { if (alive) setCfg(c) }).catch(() => {})
    return () => { alive = false }
  }, [us])

  // A restored session skips this screen entirely.
  if (status === 'active') return <Redirect href="/(tabs)" />
  if (status === 'locked') return <Redirect href="/locked" />

  const plans = us ? availablePlans(cfg) : []
  const buy = async (plan: Plan) => {
    setBuying(plan); setBuyErr(null)
    try { await openCheckout(plan) }
    catch (e: any) { setBuyErr(e?.body?.detail || e?.message || 'Could not open checkout') }
    finally { setBuying(null) }
  }
  const pm = sum?.potd_month
  const pmTally: Tally | null = pm
    ? { wins: pm.wins, losses: pm.losses, total: pm.wins + pm.losses, win_rate: pm.win_rate } : null

  return (
    <Screen>
      <View style={s.brand}>
        <Text style={s.wordmark}>BASE<Text style={{ color: T.green }}>LINE</Text></Text>
        <Muted size={13}>Tennis prop projections, graded in public</Muted>
      </View>

      <Card>
        <Text style={s.h}>What Baseline does</Text>
        <Text style={s.p}>
          Every day the model prices the tennis player-prop board — break points,
          double faults, fantasy score and more — against the lines the books
          post, ranks what it likes, and publishes a Pick of the Day. Every play
          is graded and the record is public. Members get the ranked board, the
          star, a projection tool for any player and line, and player research.
        </Text>
      </Card>

      <Card style={{ marginTop: T.s3 }}>
        <Text style={s.h}>The record</Text>
        {sum == null ? (
          <View style={{ paddingVertical: 14, alignItems: 'center' }}><ActivityIndicator color={T.green} /></View>
        ) : !sum.ready ? (
          <Muted>The record is unavailable right now.</Muted>
        ) : (
          <>
            <View style={s.stats}>
              <Stat label={`${monthName(pm?.month)} ⭐`} t={pmTally} sub="Pick of the Day" />
              <Stat label="Live props" t={sum.live_props} sub="all-time" />
              <Stat label="All-time" t={sum.all_time} />
            </View>
            <Muted size={11}>
              Last {sum.recent_days ?? 30} days {sum.recent && sum.recent.total > 0 && sum.recent.win_rate != null
                ? `${sum.recent.wins}-${sum.recent.losses} (${sum.recent.win_rate.toFixed(1)}%)` : '—'}
              {sum.days_active ? ` · ${sum.days_active} days tracked` : ''}
            </Muted>
          </>
        )}
      </Card>

      {plans.length > 0 ? (
        <Card style={{ marginTop: T.s3 }}>
          <Text style={s.h}>Subscribe</Text>
          <Muted size={12}>Checkout opens in your browser.</Muted>
          {plans.map(p => (
            <Pressable key={p} onPress={() => buy(p)} disabled={buying !== null}
                       style={[s.btn, s.primary]}>
              {buying === p ? <ActivityIndicator color="#052e16" />
                            : <Text style={s.primaryText}>{PLAN_LABEL[p]}</Text>}
            </Pressable>
          ))}
          <View style={s.note}>
            <Text style={s.noteText}>
              After you pay, come back here and sign in with email using the
              same address you used at checkout. Your membership is attached to it.
            </Text>
          </View>
          {buyErr ? <Text style={s.err}>{buyErr}</Text> : null}
        </Card>
      ) : null}

      <Pressable onPress={() => router.push('/sign-in')} style={[s.btn, s.ghost, { marginTop: T.s4 }]}>
        <Text style={s.ghostText}>Already a member? Sign in</Text>
      </Pressable>

      <Text style={s.foot}>Projections are for informational purposes only.</Text>
    </Screen>
  )
}

const s = StyleSheet.create({
  brand: { alignItems: 'center', marginTop: T.s5, marginBottom: T.s4, gap: 6 },
  wordmark: { fontFamily: F.condHeavy, fontSize: 34, letterSpacing: 6, color: T.white },
  h: { fontFamily: F.condBlack, fontSize: 18, color: T.white, marginBottom: 6 },
  p: { fontFamily: F.body, fontSize: 14, lineHeight: 21, color: T.muted },
  stats: { flexDirection: 'row', marginTop: 6, marginBottom: 10 },
  stat: { flex: 1, minWidth: 0, paddingRight: 8 },
  statLabel: { fontFamily: F.condBold, fontSize: 9.5, letterSpacing: 1, textTransform: 'uppercase',
               color: T.muted2 },
  statNum: { fontFamily: F.condHeavy, fontSize: 24, lineHeight: 28, marginTop: 2 },
  statSub: { fontFamily: F.body, fontSize: 10.5, color: T.muted2, marginTop: 1 },
  btn: { minHeight: 48, borderRadius: T.r2, alignItems: 'center', justifyContent: 'center',
         paddingHorizontal: 16, marginTop: T.s2 },
  primary: { backgroundColor: T.green },
  primaryText: { fontFamily: F.condBlack, fontSize: 14, letterSpacing: 1.2,
                 textTransform: 'uppercase', color: '#052e16' },
  ghost: { borderWidth: 1, borderColor: T.glassLineHi, backgroundColor: T.glass },
  ghostText: { fontFamily: F.condBlack, fontSize: 14, letterSpacing: 1.2,
               textTransform: 'uppercase', color: T.white },
  note: { marginTop: T.s3, padding: 10, borderRadius: T.r1, backgroundColor: `${T.amber}14`,
          borderWidth: 1, borderColor: `${T.amber}44` },
  noteText: { fontFamily: F.bodyMed, fontSize: 12.5, lineHeight: 18, color: T.amber },
  err: { color: T.red, fontFamily: F.bodyMed, fontSize: 12.5, marginTop: 8 },
  foot: { color: T.muted2, fontFamily: F.body, fontSize: 11, textAlign: 'center', marginTop: T.s5 },
})
