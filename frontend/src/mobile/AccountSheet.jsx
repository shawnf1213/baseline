import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { T, SAFE_BOTTOM } from './theme'
import { openBillingPortal } from '../utils/api'

const SESSION_KEY = 'baseline_session'

// ── WHY THERE IS NO CANCEL BUTTON HERE ───────────────────────────────────────
// Cancellation is Stripe's, not ours. Their hosted portal already handles
// cancelling, resuming, changing the card, proration, dunning and invoices, and
// it stays correct when those rules change. A cancel button of our own would be
// a second implementation of all of that which silently drifts out of step —
// and it would need card and billing state in this app, which is exactly what
// Stripe-hosted checkout exists to avoid.
//
// So this sheet's job is only to GET THE SUBSCRIBER THERE, and to be honest
// when it cannot: someone entitled through Discord, Whop or a comp has no
// Stripe customer at all, and telling them so beats opening an empty portal.
export default function AccountSheet({ open, onClose }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr]   = useState('')

  const manage = async () => {
    setErr('')
    const tok = localStorage.getItem(SESSION_KEY) || ''
    if (!tok) { setErr('Sign in first.'); return }
    setBusy(true)
    try {
      const { url } = await openBillingPortal(tok)
      if (!url) throw new Error('no url')
      // Same tab: this is a billing action, and Stripe sends them back here
      // when they finish. A popup would be blocked on iOS as often as not.
      window.location.href = url
    } catch (e) {
      const status = e?.response?.status
      // 404 is not an error the subscriber caused. It means this account is
      // entitled some other way, so there is nothing for Stripe to manage.
      setErr(status === 404
        ? 'No Stripe subscription on this account. If you subscribed through Discord or were given access directly, there is nothing to cancel here — ask in the server and it can be removed for you.'
        : status === 401
          ? 'Your session expired. Reload the page and sign in again.'
          : 'Could not open the billing portal. Try again in a moment.')
      setBusy(false)   // only on failure — success navigates away
    }
  }

  const signOut = () => {
    // Clears OUR session only. It does not revoke the Discord grant and it does
    // not cancel anything — saying so prevents "I signed out, why am I still
    // being charged".
    try { localStorage.removeItem(SESSION_KEY) } catch { /* private mode */ }
    window.location.reload()
  }

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={onClose}
            style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
                     backdropFilter: 'blur(2px)', zIndex: 200 }} />
          <motion.div
            role="dialog" aria-label="Account"
            initial={{ y: '100%' }} animate={{ y: 0 }} exit={{ y: '100%' }}
            transition={{ type: 'spring', damping: 30, stiffness: 300 }}
            style={{
              position: 'fixed', left: 0, right: 0, bottom: 0, zIndex: 201,
              background: T.bgElev, borderTop: `1px solid ${T.glassLine}`,
              borderTopLeftRadius: 18, borderTopRightRadius: 18,
              padding: `18px 16px calc(${SAFE_BOTTOM} + 18px)`,
              maxWidth: 520, margin: '0 auto',
            }}>
            <div style={{ width: 38, height: 4, borderRadius: 999,
                          background: T.border, margin: '0 auto 16px' }} />

            <div style={{ fontFamily: T.cond, fontWeight: 700, fontSize: 13,
                          letterSpacing: 2, textTransform: 'uppercase',
                          color: T.muted2, marginBottom: 14 }}>Account</div>

            <button onClick={manage} disabled={busy} style={{
              width: '100%', padding: '14px 16px', borderRadius: 12,
              background: busy ? T.card : T.cardHi,
              border: `1px solid ${T.glassLine}`, color: T.white,
              fontFamily: T.font, fontSize: 15, fontWeight: 600,
              textAlign: 'left', cursor: busy ? 'default' : 'pointer',
              opacity: busy ? 0.6 : 1,
            }}>
              {busy ? 'Opening Stripe…' : 'Manage subscription'}
              <div style={{ fontSize: 12.5, fontWeight: 400, color: T.muted,
                            marginTop: 3 }}>
                Cancel, change your card, or view invoices on Stripe.
              </div>
            </button>

            {err && (
              <div style={{
                marginTop: 12, padding: '11px 13px', borderRadius: 10,
                background: 'rgba(255,68,68,0.10)',
                border: '1px solid rgba(255,68,68,0.35)',
                color: T.white, fontFamily: T.font, fontSize: 13, lineHeight: 1.45,
              }}>{err}</div>
            )}

            <button onClick={signOut} style={{
              width: '100%', marginTop: 10, padding: '13px 16px', borderRadius: 12,
              background: 'transparent', border: `1px solid ${T.border}`,
              color: T.muted, fontFamily: T.font, fontSize: 14.5, fontWeight: 600,
              textAlign: 'left', cursor: 'pointer',
            }}>
              Sign out
              <div style={{ fontSize: 12.5, fontWeight: 400, color: T.muted2,
                            marginTop: 3 }}>
                Signs out of this device only. Does not cancel your subscription.
              </div>
            </button>

            <button onClick={onClose} style={{
              width: '100%', marginTop: 14, padding: '12px', borderRadius: 12,
              background: 'transparent', border: 'none', color: T.muted2,
              fontFamily: T.font, fontSize: 14, cursor: 'pointer',
            }}>Close</button>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
