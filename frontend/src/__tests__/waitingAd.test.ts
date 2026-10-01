import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h, nextTick, ref } from 'vue'
import type { PearlNode, ResultsResponse, ScanRequestResponse, ScanStatus } from '@scat/shared'

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>()
  return { ...actual, fetchResults: vi.fn(), fetchStatus: vi.fn(), requestScan: vi.fn(), requestRefresh: vi.fn() }
})

import { fetchResults, fetchStatus, requestRefresh, requestScan } from '@/services/api'
import { useScan } from '@/composables/useScan'
import HeroSection from '@/components/hero/HeroSection.vue'
import WaitingAd from '@/components/hero/WaitingAd.vue'

const node = (rank: number): PearlNode => ({
  id: `fp${rank}`, rank, link: `vless://uuid@n${rank}.example:443#x`, label: '', country: 'DE', address: `n${rank}.example`,
  port: 443, network: 'tcp', security: 'reality', sni: 'a.b', flow: null, tls_fingerprint: 'chrome', latency_ms: 200,
  jitter_ms: 4, speed_mbps: 30, stable: true, checks_passed: 5, checks_total: 5,
})
const cache = { created_at: '2026-10-01T10:00:00Z', expires_at: '2026-10-02T10:00:00Z', scanner_version: '3', count: 5,
  total_nodes: 9000, tested: 400, working: 25, country: null, duration_seconds: 30 }
const status = (over: Partial<ScanStatus> = {}): ScanStatus =>
  ({ cache_state: 'fresh', is_scanning: false, progress: null, last_scan_failed: false, cache, ...over })
const scanning = (): ScanStatus => status({ is_scanning: true, cache: null, cache_state: 'empty' })
const results = (): ResultsResponse => ({ ...status(), nodes: [1, 2, 3, 4, 5].map(node) })

const ads = () => document.querySelectorAll('ins.adsbygoogle')

describe('WaitingAd', () => {
  beforeEach(() => {
    window.adsbygoogle = []
  })

  it('renders the Responsive Auto unit exactly as configured, with no fixed size', () => {
    const wrapper = mount(WaitingAd)
    const ins = wrapper.get('ins.adsbygoogle')
    expect(ins.attributes()).toMatchObject({
      'data-ad-client': 'ca-pub-9243108413661209',
      'data-ad-slot': '8787910210',
      'data-ad-format': 'auto',
      'data-full-width-responsive': 'true',
    })
    expect(ins.attributes('style')).not.toMatch(/width|height/)
    expect(ins.attributes('data-ad-layout')).toBeUndefined()
    expect(wrapper.text()).toContain('Реклама')
    expect(window.adsbygoogle).toHaveLength(1)
  })

  it('requests one ad per mount: a fresh element for every search', async () => {
    const show = ref(true)
    const Host = defineComponent({ render: () => (show.value ? h(WaitingAd) : null) })
    const wrapper = mount(Host, { attachTo: document.body })
    expect(window.adsbygoogle).toHaveLength(1)
    const first = ads()[0]
    show.value = false
    await nextTick()
    expect(ads()).toHaveLength(0)
    show.value = true
    await nextTick()
    expect(ads()).toHaveLength(1)
    expect(ads()[0]).not.toBe(first)
    expect(window.adsbygoogle).toHaveLength(2)
    wrapper.unmount()
  })

  it('does not break the page when AdSense is blocked', () => {
    Object.defineProperty(window, 'adsbygoogle', { configurable: true, get: () => ({ push: () => { throw new Error('blocked') } }), set: () => {} })
    expect(() => mount(WaitingAd)).not.toThrow()
    Reflect.deleteProperty(window, 'adsbygoogle')
  })
})

describe('the ad in the hero', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    window.adsbygoogle = []
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} unobserve() {} })
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
    vi.mocked(fetchResults).mockResolvedValue(results())
    vi.mocked(fetchStatus).mockResolvedValue(status())
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('shows for the whole live scan, leaves with it, and returns for the next search; never for a replay', async () => {
    const scan = useScan()
    await scan.init()
    const wrapper = mount(HeroSection, { attachTo: document.body, global: { stubs: { OceanScene: true } } })
    expect(ads()).toHaveLength(0) // resting

    // 1) a live search
    vi.mocked(requestScan).mockResolvedValue({ ...scanning(), outcome: 'started' } as ScanRequestResponse)
    vi.mocked(fetchStatus).mockResolvedValueOnce(scanning()).mockResolvedValueOnce(scanning()).mockResolvedValue(status())
    await wrapper.get('.hero__cta').trigger('click')
    await flushPromises()
    expect(scan.journey.value).toBe('diving')
    expect(ads()).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1500)
    expect(ads()).toHaveLength(1) // still waiting
    expect(window.adsbygoogle).toHaveLength(1) // polling never re-requests the ad
    await vi.advanceTimersByTimeAsync(1500)
    expect(ads()).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1500) // the scan is over, the results are in
    expect(scan.journey.value).toBe('surfaced')
    expect(ads()).toHaveLength(0)

    // 2) a cached replay straight after a live scan: the stale 'live' flag must not flash the ad
    vi.mocked(requestScan).mockResolvedValue({ ...status(), outcome: 'cached' } as ScanRequestResponse)
    await scan.startSearch()
    await flushPromises()
    expect(scan.diveKind.value).toBe('replay')
    expect(ads()).toHaveLength(0)
    expect(window.adsbygoogle).toHaveLength(1)
    scan.completeReplay()
    await flushPromises()

    // 3) a cache refresh is a live scan too: the ad appears again, with a new request
    vi.mocked(requestRefresh).mockResolvedValue({ ...scanning(), outcome: 'started', retry_after: 600 } as never)
    vi.mocked(fetchStatus).mockResolvedValueOnce(scanning()).mockResolvedValue(status())
    await wrapper.get('.hero__refresh').trigger('click')
    await flushPromises()
    expect(ads()).toHaveLength(1)
    expect(window.adsbygoogle).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(3000)
    expect(scan.journey.value).toBe('surfaced')
    expect(ads()).toHaveLength(0)

    wrapper.unmount()
  })
})
