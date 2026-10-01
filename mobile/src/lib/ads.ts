import { adsSupported, adUnits } from '@/config/admob'

let started: Promise<void> | undefined

/**
 * Starts the Google Mobile Ads SDK once (Android only). It only initializes the SDK: no ad is
 * requested or shown anywhere yet. The native module is required lazily so platforms without it
 * (iOS, tests) never load it.
 */
export function initializeAds(): Promise<void> {
  if (!adsSupported) return Promise.resolve()
  started ??= (async () => {
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const mobileAds = require('react-native-google-mobile-ads').default
      const statuses: { name: string; state: number }[] = Object.entries(await mobileAds().initialize()).map(
        ([name, s]) => ({ name, state: (s as { state: number }).state }),
      )
      if (__DEV__) console.log(`[Скат] ads: SDK initialized (test units ${adUnits.banner}); adapters ${statuses.length}`)
    } catch (err) {
      // Ads must never break the app.
      if (__DEV__) console.warn(`[Скат] ads: SDK initialization failed: ${String(err)}`)
    }
  })()
  return started
}
