import { fireEvent, screen, waitFor } from '@testing-library/react-native'
import { renderRouter } from 'expo-router/testing-library'
import { ApiError } from '@scat/shared'
import { api } from '@/lib/api'
import { scanStore } from '@/lib/scan'
import { results, status } from '@/test-utils/fixtures'

// The ocean needs a GPU surface; the screens are tested without it.
jest.mock('@/ocean/OceanView', () => ({ OceanView: () => null }))
jest.mock('expo-font', () => ({ useFonts: () => [true, null], isLoaded: () => true, loadAsync: jest.fn() }))
jest.mock('@/lib/api', () => ({
  api: {
    fetchResults: jest.fn(),
    fetchStatus: jest.fn(),
    requestScan: jest.fn(),
    requestRefresh: jest.fn(),
    searchCountry: jest.fn(),
    fetchProviders: jest.fn(),
    subscriptionUrl: () => 'https://api.scat-search.ru/api/vless/sub',
  },
}))

const mocked = api as jest.Mocked<typeof api>
const app = () => renderRouter('./src/app', { initialUrl: '/' })

beforeEach(() => {
  jest.clearAllMocks()
  mocked.fetchResults.mockResolvedValue(results(5))
  mocked.fetchStatus.mockResolvedValue(status())
  mocked.fetchProviders.mockResolvedValue({ providers: [], probes: [], offline_after_seconds: 100 })
})
afterEach(() => scanStore.reset())

describe('catch screen', () => {
  it('loads and lists the catch, with the tabs of the app', async () => {
    await app()
    expect(await screen.findByTestId('pearl-1')).toBeTruthy()
    expect(screen.getByTestId('pearl-5')).toBeTruthy()
    expect(screen.getByText('Копировать все')).toBeTruthy()
    for (const tab of ['Улов', 'Страны', 'Подписка', 'Провайдеры']) expect(screen.getAllByText(tab).length).toBeGreaterThan(0)
    // Nothing of the admin panel exists in the app.
    expect(screen.queryByText(/admin/i)).toBeNull()
  })

  it('opens a configuration from the list', async () => {
    await app()
    await fireEvent.press(await screen.findByTestId('pearl-2'))
    expect(await screen.findByTestId('sheet-link')).toBeTruthy()
    expect(screen.getByText('Жемчужина № 2')).toBeTruthy()
  })

  it('an unreachable server is an error state with a working retry', async () => {
    mocked.fetchResults.mockRejectedValueOnce(new ApiError('network', 'offline'))
    await app()
    expect(await screen.findByText('Не удалось связаться со Скатом')).toBeTruthy()
    expect(screen.queryByText(/ApiError|offline|Network error/)).toBeNull() // no raw errors on screen
    await fireEvent.press(screen.getByTestId('results-retry'))
    expect(await screen.findByTestId('pearl-1')).toBeTruthy()
  })

  it('an empty catch says so', async () => {
    mocked.fetchResults.mockResolvedValue(results(0, { cache_state: 'empty', cache: null }))
    await app()
    expect(await screen.findByText('Жемчужин не нашлось')).toBeTruthy()
  })

  it('a scan in progress shows the searching state and the live stage', async () => {
    const scanning = { cache_state: 'empty' as const, cache: null, is_scanning: true, progress: { stage: 'testing' as const,
      progress: 40, total_candidates: 9000, processed_candidates: 300, working_candidates: 7, target_candidates: 25,
      elapsed_seconds: 10 } }
    mocked.fetchResults.mockResolvedValue(results(0, scanning))
    mocked.fetchStatus.mockResolvedValue(status(scanning))
    await app()
    expect(await screen.findByText('Скат ещё на глубине')).toBeTruthy()
    expect(screen.getByText('Ищет рабочие раковины')).toBeTruthy()
    expect(screen.getByText('Скат на глубине')).toBeTruthy()
  })

  it('starting a search asks the server once and replays the cached catch', async () => {
    mocked.requestScan.mockResolvedValue({ ...status(), outcome: 'cached' })
    await app()
    await screen.findByTestId('pearl-1')
    await fireEvent.press(screen.getByTestId('hero-cta'))
    await waitFor(() => expect(mocked.requestScan).toHaveBeenCalledTimes(1))
    expect(await screen.findByText('Возвращается к сегодняшним находкам')).toBeTruthy()
  })
})

describe('navigation', () => {
  it('reaches the about screen with the FAQ', async () => {
    await app()
    await fireEvent.press(await screen.findByTestId('open-about'))
    expect(await screen.findByText('Что такое Скат?')).toBeTruthy()
    await fireEvent.press(screen.getByTestId('faq-what'))
    expect(await screen.findByText(/бесплатный поиск рабочих VPN-конфигураций/)).toBeTruthy()
  })

  it('shows the subscription link from the API origin', async () => {
    await renderRouter('./src/app', { initialUrl: '/subscription' })
    expect(await screen.findByTestId('sub-url')).toHaveTextContent('https://api.scat-search.ru/api/vless/sub')
  })

  it('providers: empty and failed states', async () => {
    await renderRouter('./src/app', { initialUrl: '/providers' })
    expect(await screen.findByText('Пока ни одного провайдера')).toBeTruthy()
  })

  it('providers: a failed load offers a retry', async () => {
    mocked.fetchProviders.mockRejectedValueOnce(new ApiError('timeout', 'slow'))
    await renderRouter('./src/app', { initialUrl: '/providers' })
    expect(await screen.findByText('Не удалось загрузить провайдеров')).toBeTruthy()
    await fireEvent.press(screen.getByTestId('providers-retry'))
    expect(await screen.findByText('Пока ни одного провайдера')).toBeTruthy()
  })
})

describe('country search', () => {
  it('waits while the server searches (202), then lists the result', async () => {
    const pending = { ...results(0, { cache_state: 'empty', cache: null, is_scanning: true }), country: 'DE', ready: false }
    const ready = { ...results(3), country: 'DE', ready: true }
    mocked.searchCountry.mockResolvedValueOnce(pending).mockResolvedValue(ready)
    jest.useFakeTimers()
    try {
      await renderRouter('./src/app', { initialUrl: '/countries' })
      await fireEvent.press(await screen.findByTestId('country-DE'))
      expect(await screen.findByText(/Скат ищет: Германия/)).toBeTruthy()
      await jest.advanceTimersByTimeAsync(4100)
      expect(await screen.findByTestId('pearl-3')).toBeTruthy()
      expect(mocked.searchCountry).toHaveBeenCalledTimes(2)
    } finally {
      jest.useRealTimers()
    }
  })

  it('a busy server (503) is explained, not shown raw', async () => {
    mocked.searchCountry.mockRejectedValue(new ApiError('server', 'HTTP 503', { status: 503, retryAfter: 60 }))
    await renderRouter('./src/app', { initialUrl: '/countries' })
    await fireEvent.press(await screen.findByTestId('country-NL'))
    expect(await screen.findByText('Скат занят')).toBeTruthy()
    expect(screen.queryByText(/503/)).toBeNull()
  })
})
