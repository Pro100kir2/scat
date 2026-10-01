import { useEffect } from 'react'
import { DarkTheme, SplashScreen, Stack, ThemeProvider } from 'expo-router'
import { useFonts } from 'expo-font'
import { StatusBar } from 'expo-status-bar'
import { SafeAreaProvider } from 'react-native-safe-area-context'
import { CormorantGaramond_500Medium } from '@expo-google-fonts/cormorant-garamond/500Medium'
import { CormorantGaramond_500Medium_Italic } from '@expo-google-fonts/cormorant-garamond/500Medium_Italic'
import { JetBrainsMono_400Regular } from '@expo-google-fonts/jetbrains-mono/400Regular'
import { Manrope_400Regular } from '@expo-google-fonts/manrope/400Regular'
import { Manrope_500Medium } from '@expo-google-fonts/manrope/500Medium'
import { Manrope_600SemiBold } from '@expo-google-fonts/manrope/600SemiBold'
import { initializeAds } from '@/lib/ads'
import { useScanLifecycle } from '@/lib/scan'
import { colors, fonts } from '@/theme'

// The splash stays until the fonts are ready, so text never flashes in a fallback face.
void SplashScreen.preventAutoHideAsync()

const theme = {
  ...DarkTheme,
  colors: { ...DarkTheme.colors, background: colors.abyss, card: colors.deep, border: colors.line, text: colors.text, primary: colors.aqua },
}

export default function RootLayout() {
  const [loaded, error] = useFonts({
    Manrope_400Regular,
    Manrope_500Medium,
    Manrope_600SemiBold,
    JetBrainsMono_400Regular,
    CormorantGaramond_500Medium,
    CormorantGaramond_500Medium_Italic,
  })
  useScanLifecycle()
  useEffect(() => {
    void initializeAds()
  }, [])

  // If a font fails to load the app still opens, in the system face.
  const ready = loaded || !!error
  useEffect(() => {
    if (ready) void SplashScreen.hideAsync()
  }, [ready])
  if (!ready) return null

  return (
    <SafeAreaProvider>
      <ThemeProvider value={theme}>
        <StatusBar style="light" />
        <Stack
          screenOptions={{
            headerStyle: { backgroundColor: colors.deep },
            headerTintColor: colors.text,
            headerTitleStyle: { fontFamily: fonts.uiBold, fontSize: 16 },
            headerBackButtonDisplayMode: 'minimal',
            contentStyle: { backgroundColor: colors.abyss },
          }}
        >
          <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
          <Stack.Screen name="about" options={{ title: 'О Скате' }} />
          <Stack.Screen name="+not-found" options={{ title: 'Не найдено' }} />
        </Stack>
      </ThemeProvider>
    </SafeAreaProvider>
  )
}
