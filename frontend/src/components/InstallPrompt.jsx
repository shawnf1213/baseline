import { useState, useEffect } from 'react'
import { T, SAFE_BOTTOM } from '../mobile/theme'

// Install affordance for the PWA.
//  • Android/Chrome: capture beforeinstallprompt, show an "Install App" button
//    that fires the native prompt.
//  • iOS Safari: no beforeinstallprompt exists, so show a one-time dismissible
//    sheet explaining Share → Add to Home Screen. Dismissal is remembered.
// Renders nothing when already installed (standalone) or nothing to offer.

const IOS_DISMISS_KEY = 'baseline_pwa_ios_dismissed'

function isStandalone() {
  return (
    window.matchMedia?.('(display-mode: standalone)').matches ||
    window.navigator.standalone === true
  )
}
function isIOS() {
  const ua = window.navigator.userAgent || ''
  return /iphone|ipad|ipod/i.test(ua) && !window.MSStream
}

export default function InstallPrompt() {
  const [deferred, setDeferred] = useState(null)   // Android BeforeInstallPromptEvent
  const [showAndroid, setShowAndroid] = useState(false)
  const [showIOS, setShowIOS] = useState(false)

  useEffect(() => {
    if (isStandalone()) return

    const onBIP = (e) => {
      e.preventDefault()
      setDeferred(e)
      setShowAndroid(true)
    }
    const onInstalled = () => {
      setShowAndroid(false)
      setDeferred(null)
    }
    window.addEventListener('beforeinstallprompt', onBIP)
    window.addEventListener('appinstalled', onInstalled)

    // iOS: no install event — offer the manual instruction sheet once.
    if (isIOS() && localStorage.getItem(IOS_DISMISS_KEY) !== '1') {
      const t = setTimeout(() => setShowIOS(true), 1200)
      return () => {
        clearTimeout(t)
        window.removeEventListener('beforeinstallprompt', onBIP)
        window.removeEventListener('appinstalled', onInstalled)
      }
    }
    return () => {
      window.removeEventListener('beforeinstallprompt', onBIP)
      window.removeEventListener('appinstalled', onInstalled)
    }
  }, [])

  const install = async () => {
    if (!deferred) return
    deferred.prompt()
    try { await deferred.userChoice } catch { /* ignore */ }
    setShowAndroid(false)
    setDeferred(null)
  }

  const dismissIOS = () => {
    localStorage.setItem(IOS_DISMISS_KEY, '1')
    setShowIOS(false)
  }

  if (showAndroid) {
    return (
      <button
        onClick={install}
        style={{
          position: 'fixed', left: 16, right: 16,
          bottom: `calc(76px + ${SAFE_BOTTOM})`,
          zIndex: 1200,
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10,
          minHeight: 52,
          background: T.green, color: '#000',
          fontFamily: T.cond, fontWeight: 800, fontSize: 16, letterSpacing: 1,
          textTransform: 'uppercase',
          border: 'none', borderRadius: 14,
          boxShadow: '0 8px 24px rgba(0,0,0,0.5), 0 0 18px rgba(0,230,118,0.25)',
          cursor: 'pointer',
        }}
      >
        <DownloadIcon /> Install App
      </button>
    )
  }

  if (showIOS) {
    return (
      <div style={{ position: 'fixed', inset: 0, zIndex: 1300, display: 'flex', alignItems: 'flex-end' }}>
        <div onClick={dismissIOS} style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.6)' }} />
        <div style={{
          position: 'relative', width: '100%',
          background: T.card, borderTop: `1px solid ${T.border}`,
          borderTopLeftRadius: 20, borderTopRightRadius: 20,
          padding: `22px 20px calc(24px + ${SAFE_BOTTOM})`,
        }}>
          <div style={{ width: 36, height: 4, background: T.border, borderRadius: 2, margin: '0 auto 18px' }} />
          <div style={{ fontFamily: T.cond, fontWeight: 800, fontSize: 20, color: T.white, letterSpacing: 0.5 }}>
            Install Baseline
          </div>
          <div style={{ color: T.muted, fontSize: 14, marginTop: 8, lineHeight: 1.5 }}>
            Add Baseline to your home screen for a full-screen app experience.
          </div>
          <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Step n="1">Tap the <b style={{ color: T.white }}>Share</b> button <ShareIcon /> in Safari's toolbar</Step>
            <Step n="2">Scroll and tap <b style={{ color: T.white }}>Add to Home Screen</b></Step>
            <Step n="3">Tap <b style={{ color: T.white }}>Add</b> in the top corner</Step>
          </div>
          <button
            onClick={dismissIOS}
            style={{
              marginTop: 20, width: '100%', minHeight: 48,
              background: 'transparent', color: T.muted,
              border: `1px solid ${T.border}`, borderRadius: 12,
              fontFamily: T.cond, fontWeight: 700, fontSize: 15, letterSpacing: 1,
              textTransform: 'uppercase', cursor: 'pointer',
            }}
          >
            Got it
          </button>
        </div>
      </div>
    )
  }

  return null
}

function Step({ n, children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
      <div style={{
        flex: '0 0 26px', width: 26, height: 26, borderRadius: '50%',
        background: 'rgba(0,230,118,0.12)', color: T.green,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: T.cond, fontWeight: 800, fontSize: 14,
      }}>{n}</div>
      <div style={{ color: T.muted, fontSize: 14, lineHeight: 1.4 }}>{children}</div>
    </div>
  )
}

const DownloadIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#000" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 3v12M7 11l5 5 5-5M4 21h16" />
  </svg>
)
const ShareIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={T.green} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ verticalAlign: 'middle', display: 'inline' }}>
    <path d="M12 16V4M8 8l4-4 4 4M6 12v7a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-7" />
  </svg>
)
