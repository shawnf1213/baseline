// The one place the app knows whether it is signed in and whether that
// sign-in is entitled. Every screen reads this; none of them call /api/auth/me
// themselves, so there is exactly one notion of "active" in the app.
//
// Three states and nothing in between:
//   signed-out   no session token in the keychain
//   locked       a session, but /api/auth/me says active: false
//   active       a session with an active membership (or the reviewer flag)
// plus `loading` while the first check runs behind the splash.
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { AppState } from 'react-native'
import { fetchMe, Me, signOut as _signOut } from './auth'
import { registerForPush, unregisterPush } from './push'

type Status = 'loading' | 'signed-out' | 'locked' | 'active'
type Ctx = {
  status: Status
  me: Me | null
  error: string | null
  refresh: () => Promise<void>
  apply: (me: Me) => void
  signOut: () => Promise<void>
}

const SessionCtx = createContext<Ctx | null>(null)

function statusOf(me: Me | null): Status {
  if (!me || !me.authenticated) return 'signed-out'
  return me.active ? 'active' : 'locked'
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [status, setStatus] = useState<Status>('loading')
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const m = await fetchMe()
      setMe(m); setStatus(statusOf(m)); setError(null)
    } catch {
      // Offline or backend down: keep whatever state we had rather than
      // bouncing a paying member to the sign-in screen. A never-checked
      // session falls to signed-out, which is the safe default.
      setError('Could not reach Baseline')
      setStatus(s => (s === 'loading' ? 'signed-out' : s))
    }
  }, [])

  const apply = useCallback((m: Me) => { setMe(m); setStatus(statusOf(m)); setError(null) }, [])
  // The device is forgotten by the backend BEFORE the session is cleared —
  // the unregister call needs it — so a signed-out phone gets nothing.
  const signOut = useCallback(async () => {
    try { await unregisterPush() } catch { /* best effort */ }
    await _signOut(); setMe(null); setStatus('signed-out')
  }, [])

  useEffect(() => { refresh() }, [refresh])

  // REGISTER FOR PUSH ONCE PER LAUNCH, when (and only when) the membership is
  // active. A quiet no-op until the EAS project id exists or if permission
  // is declined — nothing here can block the app.
  const pushDone = useRef(false)
  useEffect(() => {
    if (status !== 'active' || pushDone.current) return
    pushDone.current = true
    registerForPush().catch(() => {})
  }, [status])

  // RE-CHECK ON FOREGROUND. Entitlement is a server fact that changes while
  // the app is closed — a cancelled subscription, a role removed — and the
  // website re-checks on every load. Coming back to the foreground is the
  // app's equivalent of a page load.
  useEffect(() => {
    const sub = AppState.addEventListener('change', s => { if (s === 'active') refresh() })
    return () => sub.remove()
  }, [refresh])

  const value = useMemo(() => ({ status, me, error, refresh, apply, signOut }),
                        [status, me, error, refresh, apply, signOut])
  return <SessionCtx.Provider value={value}>{children}</SessionCtx.Provider>
}

export function useSession(): Ctx {
  const c = useContext(SessionCtx)
  if (!c) throw new Error('useSession outside SessionProvider')
  return c
}
