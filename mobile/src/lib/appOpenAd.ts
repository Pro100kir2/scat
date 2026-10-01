import { AppState } from 'react-native'
import { adUnits, adsSupported } from '@/config/admob'
import { adsAreReady } from './ads'
import { adsSdk } from './adsSdk'

const FOUR_HOURS = 4 * 60 * 60 * 1000
/** Away for less than this: the user is only switching apps, not coming back to Скат. */
export const MIN_AWAY_MS = 30_000
/** At most one app-open ad per this long. */
export const MIN_GAP_MS = 4 * 60 * 1000

export interface AppOpenState {
  loadedAt: number | null
  showing: boolean
  lastShownAt: number | null
}

/** App-open ads expire after four hours; show at most one per MIN_GAP_MS and never over another. */
export function canShowAppOpen(s: AppOpenState, now: number): boolean {
  if (s.showing || s.loadedAt === null) return false
  if (now - s.loadedAt >= FOUR_HOURS) return false
  return s.lastShownAt === null || now - s.lastShownAt >= MIN_GAP_MS
}

/**
 * An app-open ad once the app is up (call after the first screen is ready) and again when the
 * user returns after being away. Returns a cleanup. Does nothing where ads are unsupported.
 */
export function startAppOpenAds(): () => void {
  if (!adsSupported() || !adsAreReady()) return () => {}
  const { AppOpenAd, AdEventType } = adsSdk()

  const s: AppOpenState = { loadedAt: null, showing: false, lastShownAt: null }
  let ad: ReturnType<typeof AppOpenAd.createForAdRequest> | null = null
  let stopped = false
  let wentAwayAt: number | null = null
  let removers: (() => void)[] = []

  function load() {
    removers.forEach((fn) => fn())
    ad = AppOpenAd.createForAdRequest(adUnits.appOpen)
    s.loadedAt = null
    removers = [
      ad.addAdEventListener(AdEventType.LOADED, () => {
        s.loadedAt = Date.now()
        // The first one is shown as soon as it is ready; later ones when the user returns.
        if (s.lastShownAt === null) show()
      }),
      ad.addAdEventListener(AdEventType.CLOSED, () => {
        s.showing = false
        if (!stopped) load()
      }),
      ad.addAdEventListener(AdEventType.ERROR, () => {
        s.loadedAt = null
        s.showing = false
      }),
    ]
    ad.load()
  }

  function show() {
    if (stopped || !ad || !canShowAppOpen(s, Date.now())) return
    s.showing = true
    s.lastShownAt = Date.now()
    ad.show().catch(() => {
      s.showing = false
    })
  }

  load()
  const sub = AppState.addEventListener('change', (next) => {
    if (s.showing) return // the ad itself pauses the app
    if (next === 'background') wentAwayAt = Date.now()
    else if (next === 'active' && wentAwayAt !== null) {
      const away = Date.now() - wentAwayAt
      wentAwayAt = null
      if (away >= MIN_AWAY_MS) {
        if (s.loadedAt === null) load() // the previous one expired or failed
        else show()
      }
    }
  })

  return () => {
    stopped = true
    sub.remove()
    removers.forEach((fn) => fn())
  }
}
