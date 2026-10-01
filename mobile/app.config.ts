import type { ConfigContext, ExpoConfig } from 'expo/config'

/**
 * Скат for iOS and Android. Native projects are generated from this file (`expo prebuild`,
 * EAS Build); there are no ios/ or android/ folders in the repository.
 *
 * Identifiers below are permanent once the app is published: change them BEFORE the first
 * store upload if you want different ones (see README, "Before the first release").
 */
const IOS_BUNDLE_ID = 'ru.scatsearch.app'
const ANDROID_PACKAGE = 'ru.scatsearch.app'

export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: 'Скат',
  slug: 'scat',
  version: '1.0.0',
  orientation: 'portrait',
  icon: './assets/icon.png',
  // Opens the app from scat:// links (and gives Expo Router its linking prefix).
  scheme: 'scat',
  userInterfaceStyle: 'dark',
  backgroundColor: '#050d14',
  ios: {
    bundleIdentifier: IOS_BUNDLE_ID,
    supportsTablet: true,
    infoPlist: {
      // The app only uses HTTPS (standard, exempt encryption): no export-compliance paperwork.
      ITSAppUsesNonExemptEncryption: false,
      CFBundleDevelopmentRegion: 'ru',
    },
  },
  android: {
    package: ANDROID_PACKAGE,
    adaptiveIcon: {
      backgroundColor: '#0a2030',
      foregroundImage: './assets/android-icon-foreground.png',
      monochromeImage: './assets/android-icon-monochrome.png',
    },
    // Only what the app needs: the network. Nothing else is requested.
    permissions: [],
    blockedPermissions: [
      'android.permission.READ_EXTERNAL_STORAGE',
      'android.permission.WRITE_EXTERNAL_STORAGE',
      'android.permission.SYSTEM_ALERT_WINDOW',
      'android.permission.VIBRATE',
      'android.permission.RECORD_AUDIO',
      'android.permission.CAMERA',
    ],
  },
  web: {
    // Browser preview for development only (npm run web); the public site is frontend/.
    bundler: 'metro',
    output: 'single',
    favicon: './assets/favicon.png',
  },
  plugins: [
    'expo-router',
    'expo-font',
    './plugins/withSceneLifecycle',
    [
      'expo-splash-screen',
      { image: './assets/splash-icon.png', imageWidth: 160, resizeMode: 'contain', backgroundColor: '#050d14' },
    ],
  ],
  experiments: { typedRoutes: true },
})
