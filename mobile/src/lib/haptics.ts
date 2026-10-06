// Light haptic feedback on key actions (operator redesign brief). Every call
// is swallowed on failure: a device without a Taptic Engine, or the simulator,
// must never turn a tap into an exception.
import * as Haptics from 'expo-haptics'

const safe = (p: Promise<void>) => p.catch(() => {})

export const tap     = () => safe(Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light))
export const select  = () => safe(Haptics.selectionAsync())
export const success = () => safe(Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success))
export const warn    = () => safe(Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning))
