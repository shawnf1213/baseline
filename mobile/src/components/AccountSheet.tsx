// The account sheet — who you are, how you're entitled, and what an account
// can do: re-check membership, manage the subscription where it was bought
// (Stripe, or Whop for role-based members), notification preferences, sign
// out, delete account (Apple requires in-app deletion). Plus the responsible-
// gambling helpline and the privacy / support pages.
//
// NO CANCEL BUTTON HERE, as on the website: cancellation belongs to the
// place the subscription was bought. We only get the member there.
import { useState } from 'react'
import { Alert, Linking, Pressable, StyleSheet, Text, View } from 'react-native'
import { router } from 'expo-router'
import { Sheet } from './Sheet'
import { Card, Muted } from './ui'
import { initials } from './projectBits'
import { NotificationPrefs } from './NotificationPrefs'
import { F, T } from '@/theme'
import { ApiError, billingPortalUrl, deleteAccount } from '@/lib/api'
import { useSession } from '@/lib/session'
import { unregisterPush } from '@/lib/push'
import { tap, warn } from '@/lib/haptics'

// Whop members hold the Discord premium role; their subscription lives in
// Whop's own member hub. The backend reports such members as source
// "discord" with no Stripe customer (decision recorded 2026-10-06).
export const WHOP_MANAGE_URL = 'https://whop.com/hub'
export const PRIVACY_URL = 'https://baselineev.com/privacy'
export const SUPPORT_URL = 'https://baselineev.com/support'
export const HELPLINE_URL = 'https://www.ncpgambling.org/help-treatment/'
export const HELPLINE_TEL = 'tel:18004262537'

function sourceLabel(me: any) {
  if (me?.owner) return 'Owner'
  const r = String(me?.reason || me?.source || '').toLowerCase()
  if (r.includes('reviewer')) return 'Reviewer access'
  if (r.includes('stripe') || r.includes('subscription')) return 'Subscription (Stripe)'
  if (r.includes('role') || r.includes('discord') || r.includes('premium')) return 'Premium via Discord'
  return me?.active ? 'Active membership' : 'No active membership'
}

export function AccountButton({ onPress }: { onPress: () => void }) {
  const { me } = useSession()
  const who = me?.username || me?.email || ''
  return (
    <Pressable onPress={() => { tap(); onPress() }} hitSlop={8} style={s.btn} accessibilityLabel="Account">
      <Text style={s.btnText}>{who ? initials(who.replace(/@.*$/, '').replace(/[._-]/g, ' ')) : '•'}</Text>
    </Pressable>
  )
}

export function AccountSheet({ open, onClose, children }:
  { open: boolean; onClose: () => void; children?: React.ReactNode }) {
  const { me, refresh, signOut } = useSession()
  const [busy, setBusy] = useState<'portal' | 'check' | 'delete' | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const source = String(me?.source || '').toLowerCase()
  const viaStripe = source === 'stripe'
  const viaWhop = !me?.owner && source === 'discord'

  const check = async () => {
    setBusy('check'); setMsg(null)
    await refresh()
    setBusy(null); setMsg('Membership re-checked.')
  }

  const portal = async () => {
    setBusy('portal'); setMsg(null)
    try {
      const { url } = await billingPortalUrl()
      if (!url) throw new Error('no url')
      await Linking.openURL(url)
    } catch (e: any) {
      warn()
      setMsg(e instanceof ApiError && e.status === 404
        ? 'No Stripe subscription on this account. If you subscribed through Discord or Whop, manage it there.'
        : e instanceof ApiError && e.status === 401
          ? 'Your session expired. Sign out and sign in again.'
          : 'Could not open the billing portal. Try again in a moment.')
    } finally { setBusy(null) }
  }

  const openUrl = (u: string) => { tap(); Linking.openURL(u).catch(() => setMsg('Could not open that link.')) }

  const out = async () => {
    tap()
    await signOut()
    onClose()
    router.replace('/welcome')
  }

  const del = () => {
    Alert.alert('Delete your account?',
      'This removes your personal data from Baseline and cannot be undone. A paid subscription is not cancelled by this — manage it on Stripe or Whop first if you want it stopped.',
      [{ text: 'Cancel', style: 'cancel' },
       { text: 'Delete', style: 'destructive', onPress: async () => {
         setBusy('delete'); setMsg(null)
         try {
           try { await unregisterPush() } catch { /* the server drops the rest */ }
           const r = await deleteAccount()
           await signOut()
           onClose()
           Alert.alert('Account deleted', r?.note || 'Your personal data has been removed.')
           router.replace('/welcome')
         } catch {
           warn(); setMsg('Could not delete the account right now. Try again in a moment.')
         } finally { setBusy(null) }
       } }])
  }

  const who = me?.username || me?.email || 'Signed in'
  return (
    <Sheet open={open} onClose={onClose} title="Account">
      <Card>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
          <View style={s.avatar}><Text style={s.avatarText}>{initials(who.replace(/@.*$/, '').replace(/[._-]/g, ' '))}</Text></View>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={s.who} numberOfLines={1}>{who}</Text>
            {me?.email && me?.username ? <Muted size={12}>{me.email}</Muted> : null}
            <Text style={[s.status, { color: me?.active ? T.green : T.amber }]}>{sourceLabel(me)}</Text>
          </View>
        </View>
      </Card>

      <Text style={s.section}>Membership</Text>
      <Row title={busy === 'check' ? 'Checking…' : 'Check membership again'}
           sub="Picks up a new subscription or a Discord role right away." onPress={check} disabled={!!busy} />
      {viaStripe ? (
        <Row title={busy === 'portal' ? 'Opening Stripe…' : 'Manage subscription'}
             sub="Cancel, change your card, or view invoices on Stripe. Opens in your browser."
             onPress={portal} disabled={!!busy} />
      ) : viaWhop ? (
        <Row title="Manage on Whop" sub="Your membership is through Whop. Cancel or change it there. Opens in your browser."
             onPress={() => openUrl(WHOP_MANAGE_URL)} disabled={!!busy} />
      ) : null}

      {children}

      <Text style={s.section}>Notifications</Text>
      <NotificationPrefs />

      <Text style={s.section}>Play responsibly</Text>
      <Card>
        <Text style={s.help}>Baseline is a statistics and projection tool, not betting advice. If gambling stops being fun,
          help is free and confidential: the National Problem Gambling Helpline, 1-800-GAMBLER, 24/7.</Text>
        <View style={{ flexDirection: 'row', gap: 10, marginTop: 10 }}>
          <Pressable onPress={() => openUrl(HELPLINE_TEL)} style={s.chip}><Text style={s.chipText}>Call 1-800-GAMBLER</Text></Pressable>
          <Pressable onPress={() => openUrl(HELPLINE_URL)} style={s.chip}><Text style={s.chipText}>ncpgambling.org</Text></Pressable>
        </View>
      </Card>

      <Text style={s.section}>About</Text>
      <Row title="Privacy policy" sub="What Baseline stores and why." onPress={() => openUrl(PRIVACY_URL)} />
      <Row title="Support" sub="Get help or ask a question." onPress={() => openUrl(SUPPORT_URL)} />

      <Text style={s.section}>This device</Text>
      <Row title="Sign out" sub="Signs out of this device only. Does not cancel your subscription."
           onPress={out} disabled={!!busy} />
      <Row title={busy === 'delete' ? 'Deleting…' : 'Delete account'}
           sub="Removes your personal data. Cannot be undone." onPress={del} disabled={!!busy} danger />

      {msg ? <Text style={s.msg}>{msg}</Text> : null}
    </Sheet>
  )
}

export function Row({ title, sub, onPress, disabled, danger, right }:
  { title: string; sub?: string; onPress: () => void; disabled?: boolean; danger?: boolean; right?: React.ReactNode }) {
  return (
    <Pressable onPress={onPress} disabled={disabled}
               style={({ pressed }) => [s.row, pressed && { opacity: 0.75 }, disabled && { opacity: 0.55 }]}>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={[s.rowTitle, danger && { color: T.red }]}>{title}</Text>
        {sub ? <Text style={s.rowSub}>{sub}</Text> : null}
      </View>
      {right}
    </Pressable>
  )
}

const s = StyleSheet.create({
  btn: { width: 38, height: 38, borderRadius: 19, borderWidth: 1, borderColor: T.glassLineHi,
         backgroundColor: T.glass, alignItems: 'center', justifyContent: 'center', marginBottom: 4 },
  btnText: { fontFamily: F.condBold, fontSize: 13, color: T.white, letterSpacing: 0.5 },
  avatar: { width: 46, height: 46, borderRadius: 23, backgroundColor: `${T.green}22`, borderWidth: 1,
            borderColor: `${T.green}55`, alignItems: 'center', justifyContent: 'center' },
  avatarText: { fontFamily: F.condBold, fontSize: 16, color: T.green },
  who: { fontFamily: F.condBlack, fontSize: 18, color: T.white },
  status: { fontFamily: F.bodySemi, fontSize: 12.5, marginTop: 3 },
  section: { fontFamily: F.condBold, fontSize: 10.5, letterSpacing: 1.4, textTransform: 'uppercase', color: T.muted2,
             marginTop: 20, marginBottom: 2 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 60, paddingHorizontal: 14, paddingVertical: 10,
         borderRadius: T.r2, borderWidth: 1, borderColor: T.glassLine, backgroundColor: 'rgba(255,255,255,0.03)', marginTop: 8 },
  rowTitle: { fontFamily: F.bodySemi, fontSize: 15.5, color: T.white },
  rowSub: { fontFamily: F.body, fontSize: 12, color: T.muted, marginTop: 3, lineHeight: 16 },
  help: { fontFamily: F.body, fontSize: 12.5, color: T.muted, lineHeight: 18 },
  chip: { minHeight: 40, paddingHorizontal: 12, borderRadius: 999, borderWidth: 1, borderColor: `${T.amber}55`,
          backgroundColor: `${T.amber}14`, justifyContent: 'center' },
  chipText: { fontFamily: F.condBold, fontSize: 12, letterSpacing: 0.6, color: T.amber },
  msg: { fontFamily: F.body, fontSize: 13, color: T.white, lineHeight: 18, marginTop: 14, padding: 12,
         borderRadius: T.r1, backgroundColor: 'rgba(255,255,255,0.05)', borderWidth: 1, borderColor: T.glassLine },
})
