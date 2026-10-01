import { Platform } from 'react-native'
import ids from '../../admob.json'

/** The ad unit IDs of one build. Banner, native and app-open only; there are no rewarded ads. */
export interface AdUnits {
  banner: string
  native: string
  appOpen: string
}

/**
 * Development and debug builds use only Google's official test units, so a developer can never
 * load or click real ads by accident; release builds use Scat's own units. The App ID follows
 * the same rule at build time (plugins/withAdMobAppId.js).
 */
export function resolveAdUnits(dev: boolean): AdUnits {
  const set = dev ? ids.test : ids.production
  return { banner: set.banner, native: set.native, appOpen: set.appOpen }
}

/** AdMob is Android-only until iOS IDs exist. Read at call time so tests can switch platform. */
export const adsSupported = () => Platform.OS === 'android'

export const adUnits: AdUnits = resolveAdUnits(__DEV__)
