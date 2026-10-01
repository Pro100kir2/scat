import { useSyncExternalStore } from 'react'
import type { ScanState } from '@scat/shared'
import { adsSupported } from '@/config/admob'
import { adsSdk } from './adsSdk'

let ready = false
const listeners = new Set<() => void>()
let started: Promise<void> | undefined

const subscribe = (fn: () => void) => {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

/** True once the user's consent allows ad requests and the SDK has started. */
export const useAdsReady = () => useSyncExternalStore(subscribe, () => ready, () => false)
export const adsAreReady = () => ready

const log = (message: string) => {
  if (__DEV__) console.log(`[Скат] ads: ${message}`)
}

/**
 * Consent first (Google's UMP: the form appears only where the law requires it and only if a
 * message is set up in AdMob), then the SDK. Nothing requests an ad before this resolves with
 * `adsAreReady()`. Android only; runs once. Ads must never break the app.
 */
export function initializeAds(): Promise<void> {
  if (!adsSupported()) return Promise.resolve()
  started ??= (async () => {
    try {
      const { default: mobileAds, AdsConsent } = adsSdk()
      let canRequestAds: boolean
      try {
        canRequestAds = (await AdsConsent.gatherConsent()).canRequestAds
      } catch (err) {
        // Offline or the consent service failed: fall back to what the user decided before.
        log(`consent update failed (${String(err)}), using the stored choice`)
        canRequestAds = (await AdsConsent.getConsentInfo()).canRequestAds
      }
      if (!canRequestAds) {
        log('consent does not allow ad requests')
        return
      }
      await mobileAds().initialize()
      ready = true
      listeners.forEach((fn) => fn())
      log('SDK initialized')
    } catch (err) {
      log(`SDK initialization failed: ${String(err)}`)
    }
  })()
  return started
}

/** The native ad belongs to a live scan only; a replay of the cached catch lasts seconds. */
export function showsWaitingAd(state: ScanState): boolean {
  // `diveKind` still holds the previous dive until the server has answered, so a running scan is required too.
  return state.journey === 'diving' && state.diveKind === 'live' && !!state.status?.is_scanning
}
