import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  cappedUntil,
  countryName,
  createApi,
  createScanStore,
  diveLogView,
  remainingSeconds,
  resultsPhase,
  sceneFound,
  sceneMode,
  type PearlNode,
  type ResultsResponse,
  type ScanRequestResponse,
  type ScanStatus,
} from '@scat/shared'

const node = (rank: number): PearlNode => ({
  id: `fp${rank}`, rank, link: `vless://uuid@n${rank}.example:443?security=reality&sni=a.b#%F0%9F%87%A9`, label: '',
  country: 'DE', address: `n${rank}.example`, port: 443, network: 'tcp', security: 'reality', sni: 'a.b', flow: null,
  tls_fingerprint: 'chrome', latency_ms: 200, jitter_ms: 4, speed_mbps: 30, stable: true, checks_passed: 5, checks_total: 5,
})

const cache = { created_at: '2026-10-01T10:00:00Z', expires_at: '2026-10-02T10:00:00Z', scanner_version: '3', count: 5,
  total_nodes: 9000, tested: 400, working: 25, country: null, duration_seconds: 30 }
const status = (over: Partial<ScanStatus> = {}): ScanStatus =>
  ({ cache_state: 'fresh', is_scanning: false, progress: null, last_scan_failed: false, cache, ...over })
const progress = (working: number) => ({ stage: 'testing' as const, progress: 40, total_candidates: 9000,
  processed_candidates: 300, working_candidates: working, target_candidates: 25, elapsed_seconds: 10 })
const results = (count: number, over: Partial<ScanStatus> = {}): ResultsResponse =>
  ({ ...status(over), nodes: Array.from({ length: count }, (_, i) => node(i + 1)) })

function fakeApi() {
  return {
    fetchResults: vi.fn<() => Promise<ResultsResponse>>().mockResolvedValue(results(5)),
    fetchStatus: vi.fn<() => Promise<ScanStatus>>().mockResolvedValue(status()),
    requestScan: vi.fn<() => Promise<ScanRequestResponse>>().mockResolvedValue({ ...status(), outcome: 'cached' }),
    requestRefresh: vi.fn().mockResolvedValue({ ...status({ is_scanning: true, progress: progress(0) }), outcome: 'started', retry_after: 600 }),
  }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('scan state machine', () => {
  it('loads cached results and rests', async () => {
    const api = fakeApi()
    const store = createScanStore({ api, log: () => {} })
    const seen: string[] = []
    store.subscribe(() => seen.push(store.getState().journey))
    await store.init()
    const s = store.getState()
    expect(s).toMatchObject({ loading: false, journey: 'resting', loadError: null })
    expect(s.nodes).toHaveLength(5)
    expect(resultsPhase(s)).toBe('results')
    expect(sceneMode(s)).toBe('idle')
    expect(seen.length).toBeGreaterThan(0)
  })

  it('a cached search replays today\'s catch, then surfaces', async () => {
    const api = fakeApi()
    const store = createScanStore({ api, log: () => {} })
    await store.init()
    await store.startSearch()
    let s = store.getState()
    expect(s).toMatchObject({ journey: 'diving', diveKind: 'replay', found: 5 })
    expect(sceneMode(s)).toBe('replay')
    expect(diveLogView(s, 2)).toMatchObject({ tone: 'live', title: 'Возвращается к сегодняшним находкам', progress: 0.4 })
    store.completeReplay()
    s = store.getState()
    expect(s.journey).toBe('surfaced')
    expect(diveLogView(s, 5).title).toBe('Скат вернулся с уловом')
  })

  it('a live scan is polled until it finishes, counting discoveries', async () => {
    const api = fakeApi()
    api.fetchResults.mockResolvedValueOnce(results(0, { cache_state: 'empty', cache: null }))
    api.requestScan.mockResolvedValue({ ...status({ is_scanning: true, progress: progress(0), cache: null }), outcome: 'started' })
    api.fetchStatus
      .mockResolvedValueOnce(status({ is_scanning: true, progress: progress(3), cache: null }))
      .mockResolvedValueOnce(status({ is_scanning: true, progress: progress(9), cache: null }))
      .mockResolvedValue(status())
    const store = createScanStore({ api, log: () => {}, pollMs: 1000 })
    await store.init()
    expect(resultsPhase(store.getState())).toBe('empty')
    await store.startSearch()
    expect(store.getState()).toMatchObject({ journey: 'diving', diveKind: 'live', found: 0 })
    expect(resultsPhase(store.getState())).toBe('searching')
    await vi.advanceTimersByTimeAsync(1000)
    expect(store.getState().found).toBe(3)
    expect(diveLogView(store.getState(), 3).title).toBe('Ищет рабочие раковины')
    await vi.advanceTimersByTimeAsync(1000)
    expect(sceneFound(store.getState())).toBe(9)
    await vi.advanceTimersByTimeAsync(1000)
    expect(store.getState()).toMatchObject({ journey: 'surfaced', found: 9 })
    expect(store.getState().nodes).toHaveLength(5)
    expect(api.fetchStatus).toHaveBeenCalledTimes(3)
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.fetchStatus).toHaveBeenCalledTimes(3) // polling stopped
  })

  it('does not start a second dive while one is running', async () => {
    const api = fakeApi()
    api.requestScan.mockResolvedValue({ ...status({ is_scanning: true, progress: progress(0) }), outcome: 'started' })
    const store = createScanStore({ api, log: () => {} })
    await store.init()
    await store.startSearch()
    await store.startSearch()
    expect(api.requestScan).toHaveBeenCalledTimes(1)
    store.stop()
  })

  it('gives up polling after repeated failures and says so', async () => {
    const api = fakeApi()
    api.requestScan.mockResolvedValue({ ...status({ is_scanning: true, progress: progress(0) }), outcome: 'started' })
    api.fetchStatus.mockRejectedValue(new ApiError('network', 'offline'))
    const store = createScanStore({ api, log: () => {}, pollMs: 100 })
    await store.init()
    await store.startSearch()
    await vi.advanceTimersByTimeAsync(1000)
    const s = store.getState()
    expect(s.scanError?.kind).toBe('network')
    expect(s.journey).toBe('surfaced') // old results stay on screen
    expect(diveLogView(s, 0)).toMatchObject({ tone: 'error', retry: 'search' })
    expect(diveLogView(s, 0).detail).toContain('Связь с сервером прервалась')
  })

  it('polling pauses in the background and resumes', async () => {
    const api = fakeApi()
    let hidden = true
    api.requestScan.mockResolvedValue({ ...status({ is_scanning: true, progress: progress(0) }), outcome: 'started' })
    api.fetchStatus.mockResolvedValue(status({ is_scanning: true, progress: progress(4) }))
    const store = createScanStore({ api, log: () => {}, pollMs: 100, isHidden: () => hidden })
    await store.init()
    await store.startSearch()
    await vi.advanceTimersByTimeAsync(1000)
    expect(api.fetchStatus).not.toHaveBeenCalled()
    hidden = false
    store.resume()
    await vi.advanceTimersByTimeAsync(0)
    expect(store.getState().found).toBe(4)
    store.stop()
  })

  it('a server that cannot be reached is an error state, with a reload offered', async () => {
    const api = fakeApi()
    api.fetchResults.mockRejectedValue(new ApiError('timeout', 'slow'))
    const store = createScanStore({ api, log: () => {} })
    await store.init()
    const s = store.getState()
    expect(resultsPhase(s)).toBe('error')
    expect(diveLogView(s, 0)).toMatchObject({ title: 'Скат не отвечает', retry: 'reload' })
  })

  it('refresh: reports the cooldown, and a rate limit is not an error', async () => {
    const api = fakeApi()
    const store = createScanStore({ api, log: () => {} })
    await store.init()
    expect(await store.refreshCatch()).toBe(600)
    store.stop()

    const limited = fakeApi()
    limited.requestRefresh.mockRejectedValue(new ApiError('rate_limit', 'slow down', { retryAfter: 240 }))
    const second = createScanStore({ api: limited, log: () => {} })
    await second.init()
    expect(await second.refreshCatch()).toBe(240)
    expect(second.getState().scanError).toBeNull()
  })

  it('a failed scan with nothing cached rests with an error', async () => {
    const api = fakeApi()
    api.fetchResults.mockResolvedValue(results(0, { cache_state: 'empty', cache: null }))
    api.requestScan.mockResolvedValue({ ...status({ is_scanning: true, progress: progress(0), cache: null }), outcome: 'started' })
    api.fetchStatus.mockResolvedValue(status({ cache_state: 'empty', cache: null, last_scan_failed: true }))
    const store = createScanStore({ api, log: () => {}, pollMs: 100 })
    await store.init()
    await store.startSearch()
    await vi.advanceTimersByTimeAsync(200)
    expect(store.getState()).toMatchObject({ journey: 'resting' })
    expect(store.getState().scanError?.kind).toBe('server')
  })
})

describe('country search', () => {
  const body = { ...results(2), country: 'DE', outcome: 'started' }
  const reply = (statusCode: number, headers: Record<string, string> = {}) =>
    new Response(JSON.stringify(body), { status: statusCode, headers: { 'Content-Type': 'application/json', ...headers } })

  it('202 means "still searching", 200 means ready', async () => {
    vi.useRealTimers()
    const fetch = vi.fn().mockResolvedValueOnce(reply(202)).mockResolvedValueOnce(reply(200))
    const api = createApi({ baseUrl: 'https://api.example', fetch })
    const first = await api.searchCountry('de')
    expect(fetch.mock.calls[0]![0]).toBe('https://api.example/api/vless/search/DE')
    expect(first).toMatchObject({ ready: false, country: 'DE' })
    expect((await api.searchCountry('DE')).ready).toBe(true)
  })

  it('refuses anything but a two-letter code without a request', async () => {
    const fetch = vi.fn()
    const api = createApi({ baseUrl: '', fetch })
    await expect(api.searchCountry('../admin')).rejects.toMatchObject({ kind: 'malformed' })
    expect(fetch).not.toHaveBeenCalled()
  })

  it('a full queue (503) carries the retry time', async () => {
    vi.useRealTimers()
    const fetch = vi.fn().mockResolvedValue(new Response('{"detail":"busy"}', { status: 503, headers: { 'Retry-After': '60' } }))
    const api = createApi({ baseUrl: '', fetch })
    await expect(api.searchCountry('DE')).rejects.toMatchObject({ kind: 'server', status: 503, retryAfter: 60 })
    expect(fetch).toHaveBeenCalledTimes(2) // one retry for a 5xx
  })
})

describe('helpers', () => {
  it('country names fall back to the built-in list without Intl.DisplayNames', () => {
    expect(countryName('DE')).toBe('Германия')
    expect(countryName('nl')).toBe('Нидерланды')
    expect(countryName(null)).toBeNull()
  })

  it('a stored cooldown is capped and counted down', () => {
    const now = 1_000_000
    expect(cappedUntil(String(now + 3_600_000), 600, now)).toBe(now + 600_000)
    expect(cappedUntil('garbage', 600, now)).toBe(0)
    expect(cappedUntil(null, 600, now)).toBe(0)
    expect(remainingSeconds(now + 1500, now)).toBe(2)
    expect(remainingSeconds(now - 1, now)).toBe(0)
  })
})
