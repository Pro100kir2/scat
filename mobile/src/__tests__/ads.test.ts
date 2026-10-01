import { Platform } from 'react-native'
import type { ScanState } from '@scat/shared'
import { canShowAppOpen, MIN_GAP_MS } from '@/lib/appOpenAd'

const mockGather = jest.fn()
const mockGetInfo = jest.fn()
const mockInitialize = jest.fn()
jest.mock('react-native-google-mobile-ads', () => ({
  __esModule: true,
  default: () => ({ initialize: (...a: unknown[]) => mockInitialize(...a) }),
  AdsConsent: { gatherConsent: (...a: unknown[]) => mockGather(...a), getConsentInfo: (...a: unknown[]) => mockGetInfo(...a) },
}))

function load() {
  let mod!: typeof import('@/lib/ads')
  jest.isolateModules(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    mod = require('@/lib/ads')
  })
  return mod
}

describe('initializeAds', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockInitialize.mockResolvedValue({})
  })
  afterEach(() => jest.restoreAllMocks())

  it('does nothing outside Android', async () => {
    jest.replaceProperty(Platform, 'OS', 'ios')
    const ads = load()
    await ads.initializeAds()
    expect(mockGather).not.toHaveBeenCalled()
    expect(mockInitialize).not.toHaveBeenCalled()
    expect(ads.adsAreReady()).toBe(false)
  })

  it('asks for consent first, then starts the SDK', async () => {
    jest.replaceProperty(Platform, 'OS', 'android')
    mockGather.mockResolvedValue({ canRequestAds: true })
    const ads = load()
    await ads.initializeAds()
    expect(mockGather.mock.invocationCallOrder[0]!).toBeLessThan(mockInitialize.mock.invocationCallOrder[0]!)
    expect(ads.adsAreReady()).toBe(true)
    await ads.initializeAds()
    expect(mockInitialize).toHaveBeenCalledTimes(1) // once
  })

  it('never starts the SDK when consent forbids ad requests', async () => {
    jest.replaceProperty(Platform, 'OS', 'android')
    mockGather.mockResolvedValue({ canRequestAds: false })
    const ads = load()
    await ads.initializeAds()
    expect(mockInitialize).not.toHaveBeenCalled()
    expect(ads.adsAreReady()).toBe(false)
  })

  it('falls back to the stored consent when the update fails, and survives an SDK failure', async () => {
    jest.replaceProperty(Platform, 'OS', 'android')
    mockGather.mockRejectedValue(new Error('offline'))
    mockGetInfo.mockResolvedValue({ canRequestAds: true })
    mockInitialize.mockRejectedValue(new Error('boom'))
    const ads = load()
    await expect(ads.initializeAds()).resolves.toBeUndefined()
    expect(mockGetInfo).toHaveBeenCalled()
    expect(ads.adsAreReady()).toBe(false)
  })
})

describe('when the ads show', () => {
  const base = { journey: 'diving', diveKind: 'live', status: { is_scanning: true } } as unknown as ScanState
  const { showsWaitingAd } = load()

  it('the native ad: only for a live scan the server reports as running', () => {
    expect(showsWaitingAd(base)).toBe(true)
    expect(showsWaitingAd({ ...base, journey: 'surfaced' } as ScanState)).toBe(false)
    expect(showsWaitingAd({ ...base, diveKind: 'replay' } as ScanState)).toBe(false)
    // a replay right after a live scan still reads 'live' until the server has answered
    expect(showsWaitingAd({ ...base, status: { is_scanning: false } } as unknown as ScanState)).toBe(false)
  })

  it('the app-open ad: loaded, not showing, not expired, spaced out', () => {
    const now = 1_000_000_000
    const s = { loadedAt: now - 1000, showing: false, lastShownAt: null }
    expect(canShowAppOpen(s, now)).toBe(true)
    expect(canShowAppOpen({ ...s, loadedAt: null }, now)).toBe(false)
    expect(canShowAppOpen({ ...s, showing: true }, now)).toBe(false)
    expect(canShowAppOpen({ ...s, loadedAt: now - 5 * 3600_000 }, now)).toBe(false)
    expect(canShowAppOpen({ ...s, lastShownAt: now - MIN_GAP_MS + 1000 }, now)).toBe(false)
    expect(canShowAppOpen({ ...s, lastShownAt: now - MIN_GAP_MS - 1000 }, now)).toBe(true)
  })
})
