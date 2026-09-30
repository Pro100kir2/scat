import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, RouterLinkStub } from '@vue/test-utils'
import type { EnrollmentStatus, ProvidersResponse, VerificationStatus } from '@/types/api'

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>()
  return {
    ...actual,
    fetchProviders: vi.fn(),
    addProvider: vi.fn(),
    fetchEnrollment: vi.fn(),
    fetchReleases: vi.fn(),
  }
})

import { ApiError, addProvider, fetchEnrollment, fetchProviders, fetchReleases } from '@/services/api'
import ProvidersPage from '@/pages/ProvidersPage.vue'
import AddProviderPage from '@/pages/AddProviderPage.vue'
import InstallGuidePage from '@/pages/InstallGuidePage.vue'
import InstallStatus from '@/components/providers/InstallStatus.vue'

const stubs = { global: { stubs: { RouterLink: RouterLinkStub } } }
const now = Date.now() / 1000

const overview: ProvidersResponse = {
  offline_after_seconds: 100,
  providers: [
    { id: 'prv_1', name: 'МТС', country: 'RU', region: null, asn: 8359, regions: ['Москва', 'Казань'], asns: [8359],
      probes_total: 2, probes_online: 1, status: 'online', last_heartbeat: now - 20 },
    { id: 'prv_2', name: 'T2', country: 'RU', region: null, asn: null, regions: [], asns: [],
      probes_total: 1, probes_online: 0, status: 'offline', last_heartbeat: now - 7200 },
  ],
  probes: [
    { id: 'prb_1', provider: 'МТС', status: 'approved', online: true, last_seen: now - 20, region: 'Москва', asn: 8359,
      platform: 'Linux MIPS little-endian (softfloat)', version: '1.0.0' },
    { id: 'prb_2', provider: 'T2', status: 'approved', online: false, last_seen: now - 7200, region: null, asn: null,
      platform: 'Linux ARM64 (aarch64)', version: '1.0.0' },
  ],
}

const created = {
  enrollment_id: 'enr_0123456789abcdef', token: 'skenr_tok', expires_at: now + 86400,
  provider: { id: 'prv_1', name: 'МТС', country: 'RU', region: 'Москва', asn: 8359 },
  install: {
    script_url: 'https://api.scat-search.ru/api/probes/install.sh',
    curl: 'curl -fsSL https://api.scat-search.ru/api/probes/install.sh | sh -s -- --token skenr_tok',
    wget: 'wget -qO- https://api.scat-search.ru/api/probes/install.sh | sh -s -- --token skenr_tok',
    diagnose_only: 'curl -fsSL https://api.scat-search.ru/api/probes/install.sh | sh -s -- --token skenr_tok --diagnose-only',
    releases_url: 'https://api.scat-search.ru/api/probes/releases',
  },
}

const status = (over: Partial<EnrollmentStatus>): EnrollmentStatus => ({
  enrollment_id: created.enrollment_id, state: 'waiting', expires_at: now + 86400, diagnostics: null, probe: null,
  verification: null, ...over,
})

const probe = { id: 'prb_1', provider: 'МТС', status: 'pending' as const, online: true, last_seen: now, region: null,
                asn: null, platform: 'Linux x86_64', version: '1.0.0' }
const verification = (over: Partial<VerificationStatus> = {}): VerificationStatus => ({
  state: 'running', reason: null, finished_at: null, deadline: now + 600, attempts: 1, will_retry: false, online: true,
  ...over,
})

const unsupportedDiag = {
  verdict: 'UNSUPPORTED' as const, profile: 'linux-mipsle', profile_name: null, service: 'procd',
  reasons: [{ code: 'storage_insufficient', message: '2 MB of free storage' }],
  warnings: [],
  alternatives: [{ code: 'openwrt_storage', message: 'OpenWrt: free space on /overlay (remove unused packages) or set up extroot on a USB drive.' }],
  checks: [
    { name: 'arch', ok: true, value: 'mips', required: 'x86_64, ...' },
    { name: 'storage_free', ok: false, value: '2 MB', required: '>= 6 MB' },
  ],
  summary: { os: 'Linux', distro: 'OpenWrt 23.05.3', machine: 'mips', cpu_model: 'MediaTek MT7621 ver:1 eco:3',
             cpu_cores: 4, mem_total_kb: 249072, mem_available_kb: 182000, storage_free_kb: 2000, init: 'procd' },
}

beforeEach(() => {
  vi.mocked(fetchProviders).mockReset()
  vi.mocked(addProvider).mockReset()
  vi.mocked(fetchEnrollment).mockReset()
  vi.mocked(fetchReleases).mockReset()
  sessionStorage.clear()
})
afterEach(() => vi.useRealTimers())

describe('Providers page', () => {
  it('lists available providers and the probe dashboard', async () => {
    vi.mocked(fetchProviders).mockResolvedValue(overview)
    const w = mount(ProvidersPage, stubs)
    await flushPromises()
    const cards = w.findAll('.provider')
    expect(cards).toHaveLength(2)
    expect(cards[0]!.text()).toContain('МТС')
    expect(cards[0]!.text()).toContain('Москва, Казань')
    expect(cards[0]!.text()).toContain('AS8359')
    expect(cards[0]!.text()).toContain('1 из 2 онлайн')
    expect(cards[0]!.classes()).toContain('is-online')
    expect(cards[1]!.classes()).toContain('is-offline')
    expect(cards[1]!.text()).toContain('Не на связи')

    const rows = w.findAll('.probes tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('Online')
    expect(rows[0]!.text()).toContain('Linux MIPS little-endian')
    expect(rows[0]!.text()).toContain('только что')
    expect(rows[1]!.classes()).toContain('is-offline')
    expect(rows[1]!.text()).toContain('Offline')
    expect(rows[1]!.text()).toContain('2 ч назад')
    w.unmount()
  })

  it('invites the first provider when there are none', async () => {
    vi.mocked(fetchProviders).mockResolvedValue({ providers: [], probes: [], offline_after_seconds: 100 })
    const w = mount(ProvidersPage, stubs)
    await flushPromises()
    expect(w.text()).toContain('Пока ни одного провайдера')
    expect(w.find('.probes').exists()).toBe(false)
    w.unmount()
  })

  it('shows an error state with retry', async () => {
    vi.mocked(fetchProviders).mockRejectedValueOnce(new ApiError('network', 'down')).mockResolvedValueOnce(overview)
    const w = mount(ProvidersPage, stubs)
    await flushPromises()
    expect(w.text()).toContain('Не удалось загрузить провайдеров')
    await w.find('.note button').trigger('click')
    await flushPromises()
    expect(w.findAll('.provider')).toHaveLength(2)
    w.unmount()
  })
})

describe('Add provider', () => {
  it('validates the form before sending', async () => {
    const w = mount(AddProviderPage, stubs)
    await w.find('input[name="name"]').setValue('x')
    await w.find('input[name="country"]').setValue('RUS')
    await w.find('input[name="asn"]').setValue('ASX')
    await w.find('form').trigger('submit')
    expect(addProvider).not.toHaveBeenCalled()
    expect(w.findAll('[aria-invalid="true"]')).toHaveLength(3)
  })

  it('submits, then offers interactive and manual installation', async () => {
    vi.mocked(addProvider).mockResolvedValue(created)
    vi.mocked(fetchEnrollment).mockResolvedValue(status({}))
    const w = mount(AddProviderPage, stubs)
    await w.find('input[name="name"]').setValue(' МТС ')
    await w.find('input[name="region"]').setValue('Москва')
    await w.find('input[name="asn"]').setValue('AS8359')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(addProvider).toHaveBeenCalledWith({ name: 'МТС', country: 'RU', region: 'Москва', asn: 'AS8359', info: '', contact_phone: '' })
    const methods = w.findAll('.method')
    expect(methods.map((m) => m.find('.method__title').text())).toEqual(['Интерактивная установка', 'Ручная установка'])

    await methods[0]!.trigger('click')
    await flushPromises()
    expect(w.text()).toContain(created.install.curl)
    expect(w.text()).toContain(created.install.wget)
    expect(fetchEnrollment).toHaveBeenCalledWith(created.enrollment_id, 'skenr_tok')
    expect(w.find('.station.is-active').text()).toContain('Диагностика')
    w.unmount()
  })

  it('manual installation shows checksum-verified commands for the chosen build', async () => {
    vi.mocked(addProvider).mockResolvedValue(created)
    vi.mocked(fetchEnrollment).mockResolvedValue(status({}))
    vi.mocked(fetchReleases).mockResolvedValue({ version: '1.0.0', releases: [
      { profile: 'linux-amd64', name: 'Linux x86_64', sha256: 'a'.repeat(64), size: 5_000_000, url: 'https://api/x/linux-amd64' },
      { profile: 'linux-mipsle', name: 'Linux MIPS little-endian (softfloat)', sha256: 'b'.repeat(64), size: 5_500_000, url: 'https://api/x/linux-mipsle' },
    ] })
    const w = mount(AddProviderPage, stubs)
    await w.find('input[name="name"]').setValue('МТС')
    await w.find('form').trigger('submit')
    await flushPromises()
    await w.findAll('.method')[1]!.trigger('click')
    await flushPromises()
    expect(w.text()).toContain(created.install.diagnose_only)
    expect(w.text()).toContain(`curl -fLo /usr/local/bin/scat-probe https://api/x/linux-amd64`)
    expect(w.text()).toContain(`${'a'.repeat(64)}  /usr/local/bin/scat-probe" | sha256sum -c -`)
    expect(w.text()).toContain('enroll --api https://api.scat-search.ru --token skenr_tok')
    await w.find('select').setValue('linux-mipsle')
    expect(w.text()).toContain('wget -O /usr/bin/scat-probe https://api/x/linux-mipsle')  // OpenWrt
    w.unmount()
  })

  it('shows rate limiting and server field errors', async () => {
    vi.mocked(addProvider).mockRejectedValueOnce(new ApiError('rate_limit', 'x', { status: 429 }))
    const w = mount(AddProviderPage, stubs)
    await w.find('input[name="name"]').setValue('МТС')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.find('[role="alert"]').text()).toContain('Слишком много заявок')

    vi.mocked(addProvider).mockRejectedValueOnce(new ApiError('server', 'x', { status: 422, detail: [{ loc: ['body', 'info'] }] }))
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.find('textarea[aria-invalid="true"]').exists()).toBe(true)
  })

  it('resumes after a reload of the tab', async () => {
    sessionStorage.setItem('skat:enrollment', JSON.stringify({ enrollment: created, step: 'interactive' }))
    vi.mocked(fetchEnrollment).mockResolvedValue(status({}))
    const w = mount(AddProviderPage, stubs)
    await flushPromises()
    expect(w.text()).toContain(created.install.curl)
    w.unmount()
  })
})

describe('Installation status', () => {
  async function statusFor(s: EnrollmentStatus) {
    vi.mocked(fetchEnrollment).mockResolvedValue(s)
    const w = mount(InstallStatus, { props: { enrollmentId: created.enrollment_id, token: 'skenr_tok', pollMs: 60_000 }, ...stubs })
    await flushPromises()
    return w
  }

  it('diagnostic state: waiting for the command', async () => {
    const w = await statusFor(status({ state: 'waiting' }))
    expect(w.find('.station.is-active .station__title').text()).toBe('Диагностика')
    w.unmount()
  })

  it('unsupported device: reasons in Russian, checks, alternatives, nothing installed', async () => {
    const w = await statusFor(status({ state: 'unsupported', diagnostics: unsupportedDiag }))
    expect(w.find('.station.is-failed .station__title').text()).toBe('Совместимость')
    expect(w.text()).toContain('Ничего не установлено')
    expect(w.find('.status__fail').text()).toContain('Не хватает места для файла зонда.')
    expect(w.find('.status__fail').text()).toContain('extroot')
    expect(w.find('.checks').attributes('open')).toBeDefined()
    expect(w.find('.checks__list .is-bad').text()).toContain('Свободное место')
    expect(w.find('.checks__summary').text()).toContain('MediaTek MT7621')
    w.unmount()
  })

  it('registered: the probe is connecting, verification about to start', async () => {
    const w = await statusFor(status({ state: 'registered', probe, verification: verification({ state: 'none', online: false }) }))
    expect(w.find('.banner--wait').text()).toContain('Зонд зарегистрирован')
    expect(w.find('.banner').text()).toContain('автоматическая проверка')
    w.unmount()
  })

  it('verifying: online and checking, never a generic error', async () => {
    const w = await statusFor(status({ state: 'verifying', probe, verification: verification() }))
    const banner = w.find('.banner--wait').text()
    expect(banner).toContain('Проверяем зонд')
    expect(banner).toContain('Зонд на связи')
    expect(w.find('.station.is-active .station__title').text()).toBe('Автоматическая проверка')
    for (const raw of ['pending', 'verification_failed', 'Invalid status']) expect(banner).not.toContain(raw)
    w.unmount()
  })

  it('verified: ready, taking part, polling stops', async () => {
    vi.useFakeTimers()
    const w = await statusFor(status({
      state: 'verified', probe: { ...probe, status: 'approved' }, verification: verification({ state: 'passed' }),
    }))
    expect(w.findAll('.station.is-done')).toHaveLength(5)
    expect(w.find('.banner--ok').text()).toContain('Зонд проверен')
    expect(w.find('.banner--ok').text()).toContain('участвует в проверке VLESS')
    vi.advanceTimersByTime(120_000)
    expect(fetchEnrollment).toHaveBeenCalledTimes(1)
    w.unmount()
  })

  it('verification failed: a reason and what to do in plain words, raw codes only in details', async () => {
    const w = await statusFor(status({
      state: 'verification_failed', probe,
      verification: verification({ state: 'failed', reason: 'api_unreachable', will_retry: true }),
    }))
    const banner = w.find('.banner--fail')
    expect(banner.text()).toContain('Проверка зонда не пройдена')
    expect(banner.text()).toContain('Причина')
    expect(banner.text()).toContain('не смог открыть проверочный адрес')
    expect(banner.text()).toContain('Что делать')
    expect(banner.text()).toContain('повторим проверку')
    expect(banner.text()).not.toContain('api_unreachable')
    expect(w.find('details.checks').text()).toContain('api_unreachable')
    expect(w.find('.station.is-failed .station__title').text()).toBe('Автоматическая проверка')
    w.unmount()
  })

  it('verified probe that went offline', async () => {
    const w = await statusFor(status({
      state: 'verified', probe: { ...probe, status: 'approved' }, verification: verification({ state: 'passed', online: false }),
    }))
    expect(w.find('.station.is-failed').text()).toContain('сейчас не на связи')
    w.unmount()
  })

  it('expired enrollment', async () => {
    vi.mocked(fetchEnrollment).mockRejectedValue(new ApiError('server', 'x', { status: 404 }))
    const w = mount(InstallStatus, { props: { enrollmentId: 'enr_x', token: 'skenr_x' }, ...stubs })
    await flushPromises()
    expect(w.find('.status__lost').exists()).toBe(true)
    w.unmount()
  })
})

describe('Manual installation guide', () => {
  it('explains what the probe does and never does, with install and removal commands', () => {
    const w = mount(InstallGuidePage, stubs)
    const text = w.text()
    for (const phrase of ['Не запускает VLESS и Xray', 'Не открывает входящие порты', 'Не выполняет команды',
      'Требования', 'Linux MIPS little-endian', 'OpenWrt', '--uninstall', '--diagnose-only']) {
      expect(text).toContain(phrase)
    }
    expect(w.findAll('.platforms tbody tr').length).toBeGreaterThanOrEqual(6)
  })
})
