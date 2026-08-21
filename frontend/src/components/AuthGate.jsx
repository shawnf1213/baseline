import { useState, useEffect, useCallback } from 'react'
import PasswordGate from './PasswordGate.jsx'
import { api } from '../utils/api'

// ── AUTH GATE ────────────────────────────────────────────────────────────────
// The first thing anyone sees: what Baseline is, and the two ways in — connect
// Discord, or subscribe.
//
// TWO DOORS BECAUSE THERE ARE TWO KINDS OF PERSON. Someone already in the
// server has premium and needs to prove it, not buy it again; someone who has
// never heard of the Discord needs to buy. Showing a single "subscribe" button
// would charge existing members twice, and showing only "connect Discord" would
// dead-end everyone else.
//
// ENTITLEMENT IS DECIDED SERVER-SIDE. Nothing here grants access; this reads
// /api/auth/me and renders what it is told. A gate that decides in the browser
// is the one this replaces — it compared a password that shipped inside the JS
// bundle.
//
// FALLS BACK TO THE OLD PASSWORD GATE WHILE DISCORD AUTH IS UNCONFIGURED, so
// deploying this cannot lock the owner out of a live app before the env vars
// are set. The moment /api/auth/config reports ready, the real gate takes over.

const BG = '/baseline-bg.png'
const LOGO = '/baseline-logo.png'
const SESSION_KEY = 'baseline_session'

const Shell = ({ children }) => (
  <div style={{
    minHeight: '100dvh', width: '100%', boxSizing: 'border-box',
    background: `linear-gradient(rgba(6,6,6,0.86), rgba(6,6,6,0.94)), url(${BG}) center/cover no-repeat`,
    display: 'flex', flexDirection: 'column', alignItems: 'center',
    justifyContent: 'center', padding: '32px 22px',
    fontFamily: '"Barlow", -apple-system, BlinkMacSystemFont, sans-serif',
  }}>
    <div style={{ width: '100%', maxWidth: 420 }}>{children}</div>
  </div>
)

const Btn = ({ onClick, children, primary, disabled }) => (
  <button onClick={onClick} disabled={disabled} style={{
    width: '100%', minHeight: 54, borderRadius: 14, border: primary ? 'none' : '1px solid #2a2a2a',
    background: disabled ? '#141414' : primary ? '#00E676' : '#141414',
    color: disabled ? '#555' : primary ? '#052e16' : '#fff',
    fontFamily: '"Barlow Condensed", sans-serif', fontWeight: 800, fontSize: 17,
    letterSpacing: 1.1, textTransform: 'uppercase', cursor: disabled ? 'default' : 'pointer',
    marginBottom: 10, WebkitTapHighlightColor: 'transparent',
  }}>{children}</button>
)

export default function AuthGate({ children }) {
  const [state, setState] = useState({ phase: 'loading' })
  const [busy, setBusy] = useState(false)

  const check = useCallback(async () => {
    // A session arriving on the URL is the OAuth callback handing it back.
    // Stored, then stripped from the address bar so it does not sit in history
    // or get shared by copy-paste.
    const url = new URL(window.location.href)
    const fromUrl = url.searchParams.get('session')
    if (fromUrl) {
      localStorage.setItem(SESSION_KEY, fromUrl)
      url.searchParams.delete('session')
      window.history.replaceState({}, '', url.toString())
    }

    // ── JUST PAID? LOG THEM IN ─────────────────────────────────────────────
    // Stripe returns them with session_id on the URL. Handing that to the
    // backend, which verifies it against Stripe, produces an app session bound
    // to the email on the payment — so someone who subscribed without ever
    // touching Discord lands inside the app rather than back on the paywall
    // they just paid to pass.
    // ── ARRIVING FROM A SIGN-IN EMAIL ──────────────────────────────────────
    // Exchanged for a session immediately and stripped from the address bar, so
    // the token does not sit in history where a shared screenshot or a synced
    // browser would hand somebody else a valid login.
    const magic = url.searchParams.get('magic')
    if (magic) {
      try {
        const r = (await api.post('/api/auth/magic/verify', { token: magic })).data
        if (r?.session) localStorage.setItem(SESSION_KEY, r.session)
      } catch { /* expired or cancelled — the normal check below shows why */ }
      url.searchParams.delete('magic')
      window.history.replaceState({}, '', url.toString())
    }

    const stripeSid = url.searchParams.get('session_id')
    if (stripeSid) {
      try {
        const r = (await api.post('/api/billing/claim', { session_id: stripeSid })).data
        if (r?.session) localStorage.setItem(SESSION_KEY, r.session)
      } catch { /* fall through to the normal check below */ }
      url.searchParams.delete('session_id')
      window.history.replaceState({}, '', url.toString())
    }
    let cfg = { ready: false }
    try {
      cfg = (await api.get('/api/auth/config')).data || {}
    } catch { /* backend unreachable — handled below */ }

    if (!cfg.ready) {
      setState({ phase: 'legacy' })      // Discord auth not set up yet
      return
    }
    const tok = localStorage.getItem(SESSION_KEY) || ''
    if (!tok) { setState({ phase: 'landing' }); return }
    try {
      const me = (await api.get('/api/auth/me', {
        headers: { Authorization: `Bearer ${tok}` },
      })).data || {}
      if (me.active) { setState({ phase: 'in', me }); return }
      if (me.authenticated) { setState({ phase: 'paywall', me }); return }
      localStorage.removeItem(SESSION_KEY)
      setState({ phase: 'landing' })
    } catch {
      setState({ phase: 'landing' })
    }
  }, [])

  useEffect(() => { check() }, [check])

  // COMING BACK FROM STRIPE MUST NOT LEAVE THE PAGE DEAD. Every checkout sets
  // busy=true and then navigates away, so the success path never clears it.
  // Press Back and the browser restores this page from its back-forward cache
  // with busy still true — every button disabled, no way to retry, nothing on
  // screen explaining why.
  //
  // pageshow.persisted fires on a bfcache restore; visibilitychange covers
  // returning to the tab without one. Both also re-check entitlement, so
  // somebody who actually completed the purchase is let straight in rather than
  // being shown the paywall they just paid to get past.
  useEffect(() => {
    const revive = () => { setBusy(false); check() }
    const onShow = (e) => { if (e.persisted) revive() }
    const onVis = () => { if (document.visibilityState === 'visible') revive() }
    window.addEventListener('pageshow', onShow)
    document.addEventListener('visibilitychange', onVis)
    return () => {
      window.removeEventListener('pageshow', onShow)
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [check])

  const connectDiscord = async () => {
    setBusy(true)
    try {
      const r = (await api.get('/api/auth/login', {
        params: { redirect: window.location.origin },
      })).data
      if (r?.url) window.location.href = r.url
    } catch { setBusy(false) }
  }

  const [emailMode, setEmailMode] = useState(false)
  const [email, setEmail] = useState('')
  const [emailSent, setEmailSent] = useState(false)

  const requestMagic = async () => {
    setBusy(true)
    try {
      await api.post('/api/auth/magic/request', { email })
      // Shown whatever the answer was. The backend deliberately does not say
      // whether that address has a subscription, and echoing a difference here
      // would undo that.
      setEmailSent(true)
    } catch { /* only a malformed address reaches here */ }
    setBusy(false)
  }

  const subscribe = async (plan) => {
    const tok = localStorage.getItem(SESSION_KEY) || ''
    const me = state.me || {}
    // No Discord required. A buyer with a connected account gets their id
    // attached so the role syncs too; everyone else is identified by the email
    // Stripe collects, and the session id on the way back logs them straight in
    // (see the claim step in check()).
    setBusy(true)
    try {
      const r = (await api.post('/api/billing/checkout', {
        plan, discord_id: me.discord_id || '',
      }, { headers: tok ? { Authorization: `Bearer ${tok}` } : {} })).data
      if (r?.url) window.location.href = r.url
      else setBusy(false)
    } catch { setBusy(false) }
  }

  // Safety net: if navigation to Stripe has not happened within a few seconds,
  // release the buttons. A disabled screen with no explanation is the worst
  // possible outcome of a slow or blocked redirect.
  useEffect(() => {
    if (!busy) return
    const t = setTimeout(() => setBusy(false), 8000)
    return () => clearTimeout(t)
  }, [busy])

  if (state.phase === 'loading') {
    return <Shell><div style={{ color: '#666', textAlign: 'center' }}>Loading…</div></Shell>
  }

  // Discord auth not configured yet — keep the app usable behind the old gate
  // rather than locking everyone out mid-setup.
  if (state.phase === 'legacy') return <PasswordGate>{children}</PasswordGate>

  if (state.phase === 'in') return children

  const me = state.me || {}
  // The paywall copy is chosen by WHY they were denied. A member sitting in the
  // server without the role needs to be told to get premium there; someone
  // outside needs to subscribe. Giving either one the other's instructions is
  // the difference between a sale and a dead end.
  const inGuildNoRole = me.reason === 'in_guild_no_role'

  return (
    <Shell>
      <img src={LOGO} alt="Baseline" style={{ width: 128, display: 'block',
        margin: '0 auto 20px', filter: 'drop-shadow(0 4px 18px rgba(0,230,118,0.25))' }} />

      <h1 style={{ fontFamily: '"Barlow Condensed", sans-serif', fontSize: 34,
        fontWeight: 800, color: '#fff', textAlign: 'center', margin: '0 0 10px',
        letterSpacing: 0.5, lineHeight: 1.05 }}>
        MODEL-BACKED TENNIS PROPS
      </h1>

      <p style={{ color: '#9a9a9a', fontSize: 14.5, lineHeight: 1.6,
        textAlign: 'center', margin: '0 0 22px' }}>
        Baseline prices every ATP and WTA prop on the PrizePicks and Underdog
        boards — aces, break points, games won, fantasy score — from surface
        splits, hold and return rates, court speed and match-length modelling.
        You get the projection, the edge against the line, and the last ten
        results behind it.
      </p>

      <div style={{ display: 'flex', gap: 8, justifyContent: 'center',
        marginBottom: 24, flexWrap: 'wrap' }}>
        {['Daily boards', 'Live line moves', 'Tracked record'].map(t => (
          <span key={t} style={{ fontSize: 11, color: '#00E676', fontWeight: 700,
            border: '1px solid rgba(0,230,118,0.28)', background: 'rgba(0,230,118,0.07)',
            borderRadius: 999, padding: '6px 11px', letterSpacing: 0.4 }}>{t}</span>
        ))}
      </div>

      {state.phase === 'paywall' && (
        <div style={{ background: 'rgba(255,179,0,0.08)', border: '1px solid rgba(255,179,0,0.3)',
          borderRadius: 12, padding: '11px 13px', marginBottom: 16, color: '#FFB300',
          fontSize: 13, lineHeight: 1.45 }}>
          {inGuildNoRole
            ? <>Signed in as <b>{me.username || 'you'}</b>. You’re in the Discord but don’t have the premium role yet — get premium there and it unlocks here automatically.</>
            : <>Signed in as <b>{me.username || 'you'}</b>. No active subscription on this account.</>}
        </div>
      )}

      {state.phase === 'landing' && (
        <>
          <Btn primary onClick={connectDiscord} disabled={busy}>
            Connect Discord
          </Btn>
          <p style={{ color: '#6b6b6b', fontSize: 12, textAlign: 'center',
            margin: '0 0 18px', lineHeight: 1.5 }}>
            Already have premium in the Baseline Discord? Connect and you’re in —
            no payment needed.
          </p>
        </>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '4px 0 14px' }}>
        <div style={{ flex: 1, height: 1, background: '#242424' }} />
        <span style={{ color: '#555', fontSize: 11, letterSpacing: 1 }}>
          {state.phase === 'landing' ? 'OR SUBSCRIBE' : 'SUBSCRIBE'}
        </span>
        <div style={{ flex: 1, height: 1, background: '#242424' }} />
      </div>

      <Btn onClick={() => subscribe('weekly')} disabled={busy}>
        Weekly · free trial
      </Btn>
      <Btn onClick={() => subscribe('monthly')} disabled={busy}>Monthly</Btn>

      <p style={{ color: '#6b6b6b', fontSize: 11.5, textAlign: 'center',
                  margin: '2px 0 0', lineHeight: 1.5 }}>
        No Discord needed — you'll be signed in automatically after checkout.
      </p>

      {state.phase === 'paywall' && (
        <button onClick={() => { localStorage.removeItem(SESSION_KEY); check() }}
          style={{ width: '100%', background: 'transparent', border: 'none',
            color: '#6b6b6b', fontSize: 12.5, marginTop: 8, cursor: 'pointer' }}>
          Sign out
        </button>
      )}

      {/* ── ALREADY PAID, NEW DEVICE ────────────────────────────────────────
          A subscriber who cleared their browser or picked up a different phone
          has a live subscription and no way in. This is that way in. */}
      <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid #1e1e1e' }}>
        {!emailMode ? (
          <button onClick={() => setEmailMode(true)} style={{
            width: '100%', background: 'transparent', border: 'none',
            color: '#8a8a8a', fontSize: 13, cursor: 'pointer', padding: 6,
          }}>
            Already subscribed? <span style={{ color: '#00E676' }}>Sign in with email</span>
          </button>
        ) : emailSent ? (
          <div style={{ color: '#00E676', fontSize: 13, textAlign: 'center',
                        lineHeight: 1.55 }}>
            Check your inbox — if that address has a subscription, a sign-in
            link is on its way. It expires in 15 minutes.
          </div>
        ) : (
          <>
            <input
              value={email}
              onChange={e => setEmail(e.target.value)}
              type="email" inputMode="email" autoComplete="email"
              placeholder="The email you paid with"
              style={{
                width: '100%', boxSizing: 'border-box', minHeight: 48,
                padding: '0 14px', background: '#111',
                border: '1px solid #2a2a2a', borderRadius: 12, color: '#fff',
                fontSize: 16, outline: 'none', marginBottom: 8,
              }}
            />
            <Btn onClick={requestMagic} disabled={busy || !email.includes('@')}>
              Email me a link
            </Btn>
          </>
        )}
      </div>

      <p style={{ color: '#4a4a4a', fontSize: 10.5, textAlign: 'center',
        marginTop: 20, lineHeight: 1.5 }}>
        Model projections, not betting advice. Payments handled by Stripe —
        card details never touch Baseline.
      </p>
    </Shell>
  )
}
