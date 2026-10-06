// Root layout: fonts, the dark ground, and the tab stack.
//
// DARK ONLY, by standing rule (memory: "app stays dark" — a light theme was
// built for the website and rejected on sight). userInterfaceStyle is pinned
// to dark in app.json and the navigation theme is forced here, so the OS
// setting cannot flip the app to a palette that does not exist.
//
// The splash stays up until Barlow has loaded. Rendering a frame in the system
// font and then swapping is the kind of flash that reads as a bug on a product
// whose whole surface is typography.
import { useEffect } from 'react'
// Re-exported by expo-router, which is the only navigation dependency this
// project declares. Importing from @react-navigation/native directly resolves
// only when npm happens to hoist it, which is not a thing to depend on.
import { DarkTheme, Stack, ThemeProvider } from 'expo-router'
import * as SplashScreen from 'expo-splash-screen'
import { StatusBar } from 'expo-status-bar'
import { useFonts, Barlow_400Regular, Barlow_500Medium, Barlow_600SemiBold }
  from '@expo-google-fonts/barlow'
import { BarlowCondensed_600SemiBold, BarlowCondensed_700Bold,
         BarlowCondensed_800ExtraBold, BarlowCondensed_900Black }
  from '@expo-google-fonts/barlow-condensed'
import { T } from '@/theme'
import { SessionProvider } from '@/lib/session'

SplashScreen.preventAutoHideAsync()

const NAV_THEME = {
  ...DarkTheme,
  colors: { ...DarkTheme.colors, background: T.bg, card: T.bgElev,
            border: T.glassLine, primary: T.green, text: T.white },
}

export default function RootLayout() {
  const [loaded, error] = useFonts({
    Barlow_400Regular, Barlow_500Medium, Barlow_600SemiBold,
    BarlowCondensed_600SemiBold, BarlowCondensed_700Bold,
    BarlowCondensed_800ExtraBold, BarlowCondensed_900Black,
  })

  useEffect(() => {
    // A font failure must not strand the user on the splash forever — the app
    // degrades to the system font rather than never appearing.
    if (loaded || error) SplashScreen.hideAsync()
  }, [loaded, error])

  if (!loaded && !error) return null

  // The gate lives in the routes themselves ((tabs)/_layout, sign-in, locked)
  // as <Redirect>s driven by useSession(), so every entry point — cold start,
  // deep link, foreground re-check — resolves to the same three states.
  return (
    <SessionProvider>
      <ThemeProvider value={NAV_THEME}>
        <StatusBar style="light" />
        <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: T.bg } }}>
          <Stack.Screen name="(tabs)" />
          <Stack.Screen name="sign-in" options={{ animation: 'fade' }} />
          <Stack.Screen name="locked" options={{ animation: 'fade' }} />
        </Stack>
      </ThemeProvider>
    </SessionProvider>
  )
}
