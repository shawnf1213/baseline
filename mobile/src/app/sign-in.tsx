// Sign in — both doors the website has, nothing it does not (ruling 1).
import { useState } from 'react'
import { StyleSheet, Text, TextInput, View } from 'react-native'
import { Redirect, router } from 'expo-router'
import { Screen } from '@/components/Screen'
import { Button, Card, Disclaimer, Muted } from '@/components/ui'
import { signInWithDiscord, requestEmailCode, verifyEmailCode } from '@/lib/auth'
import { useSession } from '@/lib/session'
import { F, T } from '@/theme'
import { FooterLinks } from './welcome'

type Step = 'choose' | 'email' | 'code'

export default function SignIn() {
  const { apply, status } = useSession()
  const [step, setStep] = useState<Step>('choose')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // Already a member (cold start restored a keychain session, or a deep link
  // landed here) — there is nothing to sign in to.
  if (status === 'active') return <Redirect href="/(tabs)" />
  if (status === 'locked') return <Redirect href="/locked" />

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setErr(null)
    try { await fn() } catch (e: any) {
      setErr(e?.body?.detail || e?.message || 'Something went wrong')
    } finally { setBusy(false) }
  }
  const discord = (force = false) => run(async () => {
    const me = await signInWithDiscord(force)
    apply(me)
    router.replace(me.active ? '/(tabs)' : '/locked')
  })
  const sendCode = () => run(async () => { await requestEmailCode(email); setStep('code') })
  const checkCode = () => run(async () => {
    const me = await verifyEmailCode(email, code)
    apply(me)
    router.replace(me.active ? '/(tabs)' : '/locked')
  })
  const back = () => (router.canGoBack() ? router.back() : router.replace('/welcome'))

  return (
    <Screen>
      <View style={s.brand}>
        <Text style={s.wordmark}>BASE<Text style={{ color: T.green }}>LINE</Text></Text>
        <Muted size={13}>Sign in to your membership</Muted>
      </View>

      {step === 'choose' ? (
        <>
          <Button label="Continue with Discord" onPress={() => discord(false)} busy={busy} />
          <Button label="Use a different Discord account" kind="quiet" onPress={() => discord(true)} disabled={busy} />
          <View style={s.or}><View style={s.rule} /><Muted>or</Muted><View style={s.rule} /></View>
          <Button label="Sign in with email" kind="ghost" onPress={() => setStep('email')} disabled={busy} />
          <Button label="Back" kind="quiet" onPress={back} disabled={busy} />
        </>
      ) : step === 'email' ? (
        <Card>
          <Text style={s.label}>Email on your subscription</Text>
          <TextInput value={email} onChangeText={setEmail} autoCapitalize="none" autoCorrect={false}
                     keyboardType="email-address" textContentType="emailAddress" autoComplete="email"
                     placeholder="you@example.com" placeholderTextColor={T.muted2} style={s.input} editable={!busy} />
          <Button label="Email me a code" onPress={sendCode} busy={busy} disabled={!email.includes('@')} style={{ marginTop: T.s3 }} />
          <Button label="Back" kind="quiet" onPress={() => setStep('choose')} disabled={busy} />
        </Card>
      ) : (
        <Card>
          <Text style={s.label}>Check your inbox</Text>
          <Muted>If that address has a membership, a 6-digit code is on its way. It works once, for 10 minutes.</Muted>
          {/* Not number-pad and not capped at six: the emailed code is six
              digits, but the App Store reviewer's fixed code is longer and
              not numeric, and the field has to accept both. */}
          <TextInput value={code} onChangeText={v => setCode(v.replace(/\s/g, '').slice(0, 40))}
                     keyboardType="default" textContentType="oneTimeCode" autoComplete="one-time-code"
                     autoCapitalize="none" autoCorrect={false} placeholder="6-digit code"
                     placeholderTextColor={T.muted2} maxLength={40} style={[s.input, s.code]} editable={!busy} />
          <Button label="Sign in" onPress={checkCode} busy={busy} disabled={code.length < 6} style={{ marginTop: T.s3 }} />
          <Button label="Send a new code" kind="quiet" onPress={sendCode} disabled={busy} />
        </Card>
      )}

      {err ? <Text style={s.err}>{err}</Text> : null}
      <Disclaimer />
      <FooterLinks />
    </Screen>
  )
}

const s = StyleSheet.create({
  brand: { alignItems: 'center', marginTop: T.s6, marginBottom: T.s5, gap: 6 },
  wordmark: { fontFamily: F.condHeavy, fontSize: 34, letterSpacing: 6, color: T.white },
  or: { flexDirection: 'row', alignItems: 'center', gap: 12, marginVertical: T.s2 },
  rule: { flex: 1, height: StyleSheet.hairlineWidth, backgroundColor: T.glassLine },
  label: { fontFamily: F.condBold, fontSize: 11, letterSpacing: 1.3, textTransform: 'uppercase', color: T.muted, marginBottom: 8 },
  input: { backgroundColor: 'rgba(255,255,255,0.04)', borderWidth: 1, borderColor: T.glassLine, borderRadius: T.r1,
           paddingHorizontal: 14, minHeight: 50, color: T.white, fontFamily: F.body, fontSize: 16, marginTop: 8 },
  code: { fontFamily: F.condBlack, fontSize: 24, letterSpacing: 4, textAlign: 'center' },
  err: { color: T.red, fontFamily: F.bodyMed, fontSize: 13, textAlign: 'center', marginTop: T.s3 },
})
