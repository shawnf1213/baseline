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

// ── FREE LOOK, ON THE SERVER'S CLOCK ────────────────────────────────────────
// An unentitled visitor gets a short browse before the paywall closes. The
// countdown is NOT kept here: a timer in localStorage dies with a refresh, one
// in memory dies with a new tab, and neither exists at all in a private window —
// those three are exactly the bypasses this is meant to close. The server keys
// the clock on the request's address and only ever tells us how long is left.
//
// Fails CLOSED to the normal gate if the check errors: an unreachable preview
// endpoint should show the paywall, not hand out unlimited access.
async function gateOrPreview(fallbackPhase, me) {
  try {
    const p = (await api.get('/api/preview/status')).data || {}
    if (p.allowed && (p.remaining_seconds || 0) > 0) {
      return { phase: 'preview', me, left: p.remaining_seconds,
               next: fallbackPhase }
    }
  } catch { /* fall through to the gate */ }
  return { phase: fallbackPhase, me }
}

export default function AuthGate({ children }) {
  const [state, setState] = useState({ phase: 'loading' })
  const [busy, setBusy] = useState(false)
  // Declared ABOVE check(), which calls setInvite. Below it the reference sits
  // in the temporal dead zone — it happens to work because check only runs
  // after the component body finishes, and that is exactly the kind of accident
  // that breaks the moment someone calls it earlier.
  const [invite, setInvite] = useState('')
  const [linkDismissed, setLinkDismissed] = useState(false)

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

    // ── JUST PAID? LOG THEM IN ─────────────────────────────────────────────
    // Stripe returns them with session_id on the URL. Handing that to the
    // backend, which verifies it against Stripe, produces an app session bound
    // to the email on the payment — so someone who subscribed without ever
    // touching Discord lands inside the app rather than back on the paywall
    // they just paid to pass.
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
      if (cfg.invite_url) setInvite(cfg.invite_url)
    } catch { /* backend unreachable — handled below */ }

    if (!cfg.ready) {
      setState({ phase: 'legacy' })      // Discord auth not set up yet
      return
    }
    const tok = localStorage.getItem(SESSION_KEY) || ''
    if (!tok) { setState(await gateOrPreview('landing')); return }
    try {
      const me = (await api.get('/api/auth/me', {
        headers: { Authorization: `Bearer ${tok}` },
      })).data || {}
      if (me.active) { setState({ phase: 'in', me }); return }
      if (me.authenticated) { setState(await gateOrPreview('paywall', me)); return }
      localStorage.removeItem(SESSION_KEY)
      setState(await gateOrPreview('landing'))
    } catch {
      setState(await gateOrPreview('landing'))
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

  // PREVIEW COUNTDOWN. Ticks locally once a second so the number moves, but
  // RE-ASKS THE SERVER every 10s and takes its answer as final — the local tick
  // is display only. Without the re-ask, pausing JS in devtools or putting the
  // machine to sleep would stretch the window; with it, the deadline is the
  // server's and the browser cannot argue.
  useEffect(() => {
    if (state.phase !== 'preview') return
    let alive = true
    const tick = setInterval(() => {
      if (!alive) return
      setState(s => (s.phase === 'preview'
        ? { ...s, left: Math.max(0, (s.left || 0) - 1) } : s))
    }, 1000)
    const poll = setInterval(async () => {
      try {
        const p = (await api.get('/api/preview/status')).data || {}
        if (!alive) return
        if (!p.allowed || (p.remaining_seconds || 0) <= 0) {
          setState(s => ({ phase: s.next || 'landing', me: s.me }))
        } else {
          setState(s => (s.phase === 'preview'
            ? { ...s, left: p.remaining_seconds } : s))
        }
      } catch { /* keep the local countdown; it still expires on its own */ }
    }, 10000)
    return () => { alive = false; clearInterval(tick); clearInterval(poll) }
  }, [state.phase])

  // Local countdown reaching zero closes the window even if a poll is in flight.
  useEffect(() => {
    if (state.phase === 'preview' && (state.left || 0) <= 0) {
      setState(s => ({ phase: s.next || 'landing', me: s.me }))
    }
  }, [state.phase, state.left])

  const connectDiscord = async () => {
    setBusy(true)
    try {
      // Pass the current session when there is one. If it is an EMAIL session
      // the backend attaches the Discord id to that subscription, which is what
      // turns a website purchase into a server role.
      const existing = localStorage.getItem(SESSION_KEY) || ''
      const r = (await api.get('/api/auth/login', {
        params: { redirect: window.location.origin, link_session: existing },
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

  // FREE LOOK: the whole app, with a bar counting down to the paywall. Showing
  // the real product beats a screenshot tour — but it ends on the server's
  // clock, not on anything this page can be talked out of.
  if (state.phase === 'preview') {
    const left = Math.max(0, state.left || 0)
    const mm = String(Math.floor(left / 60)).padStart(1, '0')
    const ss = String(left % 60).padStart(2, '0')
    const pct = Math.max(0, Math.min(100, (left / 120) * 100))
    return (
      <div style={{ minHeight: '100vh' }}>
        <div style={{
          position: 'sticky', top: 0, zIndex: 2000,
          background: 'linear-gradient(90deg,#0d1a0d,#0a0a0a)',
          borderBottom: '1px solid #1e1e1e', padding: '8px 14px',
          display: 'flex', alignItems: 'center', gap: 12,
        }}>
          <div style={{
            fontFamily: "'Barlow Condensed', sans-serif", fontWeight: 800, fontSize: 12,
            letterSpacing: 1.2, textTransform: 'uppercase', color: '#00E676',
            whiteSpace: 'nowrap',
          }}>
            Free preview · {mm}:{ss}
          </div>
          <div style={{ flex: 1, height: 4, background: '#1e1e1e', borderRadius: 999 }}>
            <div style={{
              width: `${pct}%`, height: '100%', borderRadius: 999,
              background: left <= 30 ? '#FF4444' : '#00E676',
              transition: 'width 1s linear',
            }} />
          </div>
          <button
            onClick={() => setState(s => ({ phase: s.next || 'landing', me: s.me }))}
            style={{
              background: '#00E676', color: '#052e16', border: 'none',
              borderRadius: 999, padding: '7px 14px', fontWeight: 800,
              fontSize: 12, letterSpacing: 0.8, textTransform: 'uppercase',
              cursor: 'pointer', whiteSpace: 'nowrap',
            }}>
            Subscribe
          </button>
        </div>
        {children}
      </div>
    )
  }

  if (state.phase === 'in') {
    const m = state.me || {}
    // ── PAYING FOR A ROLE THEY CANNOT RECEIVE ──────────────────────────────
    // Someone who subscribed on the website without Discord has a subscription
    // keyed on their email, and the role sync skips any subscription with no
    // Discord id. Without this they would pay for server access that silently
    // never arrives. Dismissible, because it must not nag anyone who does not
    // want the Discord at all.
    // Two different problems with two different fixes:
    //   needsLink — bought on the website, no Discord attached at all
    //   needsJoin — Discord attached, but never joined the server, so the sync
    //               has nobody to grant the role to
    const needsLink = m.email && m.discord_linked === false
    const needsJoin = m.source === 'stripe' && m.in_guild === false && invite
    if (linkDismissed || (!needsLink && !needsJoin)) return children
    return (
      <>
        <div style={{
          background: 'linear-gradient(90deg, rgba(88,101,242,0.16), rgba(88,101,242,0.05))',
          borderBottom: '1px solid rgba(88,101,242,0.35)',
          padding: '11px 14px', display: 'flex', alignItems: 'center', gap: 12,
          fontFamily: '"Barlow", sans-serif',
        }}>
          <div style={{ flex: 1, minWidth: 0, fontSize: 13, color: '#dcdcff',
                        lineHeight: 1.45 }}>
            {needsJoin
              ? <>Your subscription is active. <b>Join the Discord</b> to pick up
                  your premium role — the role can only be granted to a member.</>
              : <>Your subscription is active. <b>Connect Discord</b> to get the
                  premium role in the server too.</>}
          </div>
          {needsJoin ? (
            <a href={invite} target="_blank" rel="noreferrer" style={{
              flex: '0 0 auto', padding: '9px 14px', borderRadius: 10,
              background: '#5865F2', color: '#fff', fontWeight: 800, fontSize: 12.5,
              letterSpacing: 0.5, textDecoration: 'none',
            }}>Join</a>
          ) : (
            <button onClick={connectDiscord} disabled={busy} style={{
              flex: '0 0 auto', padding: '9px 14px', borderRadius: 10, border: 'none',
              background: '#5865F2', color: '#fff', fontWeight: 800, fontSize: 12.5,
              letterSpacing: 0.5, cursor: 'pointer',
            }}>Connect</button>
          )}
          <button onClick={() => setLinkDismissed(true)} style={{
            flex: '0 0 auto', background: 'transparent', border: 'none',
            color: '#9a9ac0', fontSize: 19, cursor: 'pointer', padding: '0 4px',
          }}>×</button>
        </div>
        {children}
      </>
    )
  }

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
            : me.reason === 'not_in_guild' && invite
            ? <>Signed in as <b>{me.username || 'you'}</b>. No subscription on this account — subscribe below, or <a href={invite} target="_blank" rel="noreferrer" style={{ color: '#FFB300', fontWeight: 700 }}>join the Discord</a> if you have premium there.</>
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
