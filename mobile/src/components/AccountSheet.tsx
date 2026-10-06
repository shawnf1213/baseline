// The account sheet — who you are, how you're entitled, and the three things
// an account can do: re-check membership, manage the subscription on Stripe,
// sign out. Plus delete account, which Apple requires in-app.
//
// NO CANCEL BUTTON HERE, as on the website: cancellation is Stripe's. The
// portal handles cancel, card changes and invoices and stays correct when
// those rules change; opening it in the default browser is the whole job.
import { useState } from 'react'
import { Alert, Linking, Pressable, StyleSheet, Text, View } from 'react-native'
import { router } from 'expo-router'
import { Sheet } from './Sheet'
import { Card, Muted } from './ui'
import { initials } from './projectBits'
import { F, T } from '@/theme'
import { ApiError, billingPortalUrl, deleteAccount } from '@/lib/api'
import { useSession } from '@/lib/session'
import { tap, warn } from '@/lib/haptics'

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

export function AccountSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { me, refresh, signOut } = useSession()
  const [busy, setBusy] = useState<'portal' | 'check' | 'delete' | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

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
        ? 'No Stripe subscription on this account. If you subscribed through Discord or were given access directly, there is nothing to manage here.'
        : e instanceof ApiError && e.status === 401
          ? 'Your session expired. Sign out and sign in again.'
          : 'Could not open the billing portal. Try again in a moment.')
    } finally { setBusy(null) }
  }

  const out = async () => {
    tap()
    await signOut()
    onClose()
    router.replace('/welcome')
  }

  const del = () => {
    Alert.alert('Delete your account?',
      'This removes your personal data from Baseline and cannot be undone. A paid subscription is not cancelled by this — manage it on Stripe first if you want it stopped.',
      [{ text: 'Cancel', style: 'cancel' },
       { text: 'Delete', style: 'destructive', onPress: async () => {
         setBusy('delete'); setMsg(null)
         try {
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

      <Row title={busy === 'check' ? 'Checking…' : 'Check membership again'}
           sub="Picks up a new subscription or a Discord role right away." onPress={check} disabled={!!busy} />
      <Row title={busy === 'portal' ? 'Opening Stripe…' : 'Manage subscription'}
           sub="Cancel, change your card, or view invoices on Stripe. Opens in your browser."
           onPress={portal} disabled={!!busy} />
      <Row title="Sign out" sub="Signs out of this device only. Does not cancel your subscription."
           onPress={out} disabled={!!busy} />
      <Row title={busy === 'delete' ? 'Deleting…' : 'Delete account'}
           sub="Removes your personal data. Cannot be undone." onPress={del} disabled={!!busy} danger />

      {msg ? <Text style={s.msg}>{msg}</Text> : null}
    </Sheet>
  )
}

function Row({ title, sub, onPress, disabled, danger }:
  { title: string; sub: string; onPress: () => void; disabled?: boolean; danger?: boolean }) {
  return (
    <Pressable onPress={onPress} disabled={disabled}
               style={({ pressed }) => [s.row, pressed && { opacity: 0.75 }, disabled && { opacity: 0.55 }]}>
      <Text style={[s.rowTitle, danger && { color: T.red }]}>{title}</Text>
      <Text style={s.rowSub}>{sub}</Text>
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
  row: { minHeight: 64, paddingHorizontal: 14, paddingVertical: 11, borderRadius: T.r2, borderWidth: 1,
         borderColor: T.glassLine, backgroundColor: 'rgba(255,255,255,0.03)', marginTop: 10, justifyContent: 'center' },
  rowTitle: { fontFamily: F.bodySemi, fontSize: 15.5, color: T.white },
  rowSub: { fontFamily: F.body, fontSize: 12, color: T.muted, marginTop: 3, lineHeight: 16 },
  msg: { fontFamily: F.body, fontSize: 13, color: T.white, lineHeight: 18, marginTop: 14, padding: 12,
         borderRadius: T.r1, backgroundColor: 'rgba(255,255,255,0.05)', borderWidth: 1, borderColor: T.glassLine },
})
