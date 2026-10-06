// Where a tapped notification lands: the Picks tab, switched to that sport.
// Mounted once inside the providers; renders nothing.
import { useEffect } from 'react'
import { Platform } from 'react-native'
import { router } from 'expo-router'
import { routeFor } from '@/lib/push'
import { useSport } from '@/lib/sports'

export function PushRouter() {
  const { setSport } = useSport()
  useEffect(() => {
    if (Platform.OS === 'web') return
    let sub: { remove: () => void } | null = null
    let on = true
    ;(async () => {
      try {
        const Notifications = await import('expo-notifications')
        // Foreground notifications show as a banner rather than being swallowed.
        Notifications.setNotificationHandler({
          handleNotification: async () => ({
            shouldShowBanner: true, shouldShowList: true, shouldPlaySound: false, shouldSetBadge: false,
          }),
        })
        const go = (data: any) => {
          const { sport } = routeFor(data)
          if (sport) setSport(sport)
          router.push('/(tabs)')
        }
        const last = await Notifications.getLastNotificationResponseAsync()
        if (on && last?.notification?.request?.content?.data) go(last.notification.request.content.data)
        sub = Notifications.addNotificationResponseReceivedListener(r => go(r.notification.request.content.data))
      } catch { /* notifications module unavailable (web / unsupported) */ }
    })()
    return () => { on = false; sub?.remove() }
  }, [setSport])
  return null
}
