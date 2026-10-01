import { describe, expect, it } from 'vitest'
import { mount, RouterLinkStub } from '@vue/test-utils'
import NetworkAvailability from '@/components/results/NetworkAvailability.vue'
import PearlCard from '@/components/results/PearlCard.vue'
import type { PearlNode, Reachability, ReachState } from '@scat/shared'

const row = (provider: string, state: ReachState, latency: number | null = null): Reachability => ({
  provider_id: `prv_${provider}`, provider, state, latency_ms: latency, checked_at: null, probes: 1,
})

const stubs = { global: { stubs: { RouterLink: RouterLinkStub } } }

describe('NetworkAvailability', () => {
  it('shows every state with its own look and words', () => {
    const rows = [
      row('МТС', 'SUCCESS', 38), row('Билайн', 'FAILED'), row('T2', 'TIMEOUT'), row('МегаФон', 'CHECKING'),
      row('Ростелеком', 'NO_PROBE'), row('Дом.ру', 'OFFLINE'), row('Yota', 'NOT_TESTED'),
    ]
    const w = mount(NetworkAvailability, { props: { rows }, ...stubs })
    const beacon = (name: string) => w.findAll('.beacon').find((b) => b.text().includes(name))!
    expect(beacon('МТС').classes()).toContain('beacon--reachable')
    expect(beacon('МТС').text()).toContain('Доступен · 38 мс')
    expect(beacon('Билайн').classes()).toContain('beacon--blocked')
    expect(beacon('Билайн').text()).toContain('Недоступен')
    expect(beacon('T2').text()).toContain('Нет ответа')
    expect(beacon('МегаФон').classes()).toContain('beacon--checking')
    expect(beacon('Ростелеком').text()).toContain('Нет зонда')
    expect(beacon('Дом.ру').classes()).toContain('beacon--offline')
    expect(beacon('Yota').text()).toContain('Не проверялся')
    // Only real verdicts count: 1 of 3 (SUCCESS, FAILED, TIMEOUT).
    expect(w.find('.reach__summary').text()).toBe('Доступна в 1 из 3 сетей')
  })

  it('sorts providers by name, Russian collation', () => {
    const w = mount(NetworkAvailability, { props: { rows: [row('T2', 'SUCCESS'), row('МТС', 'SUCCESS'), row('Билайн', 'SUCCESS')] }, ...stubs })
    expect(w.findAll('.beacon__name').map((n) => n.text())).toEqual(['Билайн', 'МТС', 'T2'])
  })

  it('invites to add a provider when there are none', () => {
    const w = mount(NetworkAvailability, { props: { rows: [] }, ...stubs })
    expect(w.text()).toContain('Пока ни один провайдер не подключил зонд')
    expect(w.findComponent(RouterLinkStub).props('to')).toBe('/providers/add/')
  })
})

const node: PearlNode = {
  id: 'fp1', rank: 1, link: 'vless://x@1.1.1.1:443', label: '', country: 'DE', address: '1.1.1.1', port: 443,
  network: 'tcp', security: 'reality', sni: 'www.microsoft.com', flow: null, tls_fingerprint: 'chrome',
  latency_ms: 120, jitter_ms: 5, speed_mbps: 20, stable: true, checks_passed: 5, checks_total: 5,
  reachability: [row('МТС', 'SUCCESS', 40), row('T2', 'FAILED')],
}

describe('PearlCard with provider reachability', () => {
  it('keeps the main card compact and shows network availability on expand', async () => {
    const w = mount(PearlCard, { props: { node, spotlit: false }, ...stubs })
    expect(w.find('.card__main').text()).not.toContain('МТС')  // not cluttering the card
    const toggle = w.find('.card__toggle')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(w.classes()).not.toContain('is-open')

    await toggle.trigger('click')
    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect(w.classes()).toContain('is-open')
    expect(w.find('.reach__title').text()).toBe('Доступность в сетях')
    expect(w.findAll('.beacon')).toHaveLength(2)

    await toggle.trigger('click')
    expect(w.classes()).not.toContain('is-open')
  })

  it('works with results from an older API without reachability', () => {
    const w = mount(PearlCard, { props: { node: { ...node, reachability: undefined }, spotlit: false }, ...stubs })
    expect(w.find('.reach').exists()).toBe(false)
  })
})
