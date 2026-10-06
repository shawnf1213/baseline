// Notification preferences — one switch per sport for each kind of push
// (board posted, day's recap posted). Saved on the device and on the backend
// against this device's token, so the fan-out respects them.
import { useEffect, useState } from 'react'
import { StyleSheet, Switch, Text, View } from 'react-native'
import { Card, Muted } from './ui'
import { DEFAULT_PREFS, PushEvent, PushPrefs, loadPrefs, registerForPush, savePrefs } from '@/lib/push'
import { SportKey, useSport } from '@/lib/sports'
import { F, T } from '@/theme'
import { select } from '@/lib/haptics'

const EVENT_LABEL: Record<PushEvent, string> = { board: 'Board posted', recap: "Day's recap" }

export function NotificationPrefs() {
  const { sports } = useSport()
  const [prefs, setPrefs] = useState<PushPrefs>(DEFAULT_PREFS)
  const [state, setState] = useState<'unknown' | 'registered' | 'denied' | 'unavailable'>('unknown')
  useEffect(() => {
    let on = true
    loadPrefs().then(p => { if (on) setPrefs(p) })
    registerForPush().then(s => { if (on) setState(s) }).catch(() => { if (on) setState('unavailable') })
    return () => { on = false }
  }, [])
  const visible = sports.filter(s => s.visible)
  const set = (ev: PushEvent, sport: SportKey, v: boolean) => {
    select()
    const next = { ...prefs, [ev]: { ...prefs[ev], [sport]: v } }
    setPrefs(next)
    savePrefs(next).catch(() => {})
  }
  return (
    <Card>
      {state === 'denied' ? (
        <Muted size={12} style={{ marginBottom: 10, color: T.amber }}>
          Notifications are off for Baseline in iOS Settings. Turn them on there to receive these.
        </Muted>
      ) : state === 'unavailable' ? (
        <Muted size={12} style={{ marginBottom: 10 }}>
          Push notifications arrive with the App Store build — these choices are saved now.
        </Muted>
      ) : null}
      <View style={s.head}>
        <Text style={[s.cell, s.k, { flex: 1 }]}>Sport</Text>
        {(['board', 'recap'] as PushEvent[]).map(ev => <Text key={ev} style={[s.cell, s.k, s.col]}>{EVENT_LABEL[ev]}</Text>)}
      </View>
      {visible.map(sp => (
        <View key={sp.key} style={s.row}>
          <Text style={[s.cell, s.sport, { flex: 1 }]}>{sp.label}</Text>
          {(['board', 'recap'] as PushEvent[]).map(ev => (
            <View key={ev} style={[s.cell, s.col]}>
              <Switch value={prefs[ev][sp.key] !== false} onValueChange={v => set(ev, sp.key, v)}
                      trackColor={{ true: T.greenDim, false: 'rgba(255,255,255,0.15)' }} thumbColor={T.white} />
            </View>
          ))}
        </View>
      ))}
      <Muted size={11} style={{ marginTop: 8, lineHeight: 15 }}>
        Sent only after the post lands in Discord. Nothing else is ever pushed.
      </Muted>
    </Card>
  )
}

const s = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'center', marginBottom: 4 },
  row: { flexDirection: 'row', alignItems: 'center', minHeight: 48 },
  cell: { paddingHorizontal: 4 },
  col: { width: 92, alignItems: 'center', textAlign: 'center' },
  k: { fontFamily: F.condBold, fontSize: 10, letterSpacing: 1.1, textTransform: 'uppercase', color: T.muted2 },
  sport: { fontFamily: F.bodySemi, fontSize: 15, color: T.white },
})
