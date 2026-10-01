/**
 * The Скат public API client. No framework, no environment access: the host app says where
 * the API lives (`createApi({ baseUrl })`). Every failure becomes an ApiError with a kind the
 * UI can phrase; raw exceptions never reach the screen.
 */
import type {
  CountrySearchResponse,
  EnrollmentCreated,
  EnrollmentStatus,
  ProviderForm,
  ProvidersResponse,
  RefreshResponse,
  ReleasesResponse,
  ResultsResponse,
  ScanRequestResponse,
  ScanStatus,
} from './types'

export type ApiErrorKind = 'network' | 'timeout' | 'rate_limit' | 'server' | 'malformed' | 'aborted'

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status?: number
  readonly retryAfter?: number
  /** FastAPI's `detail`: a reason string, or field errors for a 422. */
  readonly detail?: unknown

  constructor(kind: ApiErrorKind, message: string, opts: { status?: number; retryAfter?: number; detail?: unknown } = {}) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = opts.status
    this.retryAfter = opts.retryAfter
    this.detail = opts.detail
  }
}

export const asApiError = (err: unknown): ApiError =>
  err instanceof ApiError ? err : new ApiError('malformed', String(err))

export interface ApiConfig {
  /** API origin without a trailing slash; '' means same origin. A function is read on every call. */
  baseUrl: string | (() => string)
  /** Defaults to the global fetch, looked up on every call. */
  fetch?: typeof fetch
}

interface RequestOptions {
  method?: 'GET' | 'POST'
  /** JSON body, for POST. */
  body?: unknown
  signal?: AbortSignal
  timeoutMs?: number
  /** Extra attempts for network errors and 5xx. Only used for idempotent requests. */
  retries?: number
  /** Statuses besides 2xx whose JSON body is a normal answer (e.g. 202 while a search runs). */
  accept?: number[]
}

const DEFAULT_TIMEOUT_MS = 12_000

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, ms)
    signal?.addEventListener('abort', () => {
      clearTimeout(timer)
      reject(new ApiError('aborted', 'Request aborted'))
    }, { once: true })
  })

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null

function assertStatus(data: unknown): asserts data is ScanStatus {
  if (
    !isObject(data) ||
    !['fresh', 'stale', 'empty'].includes(data.cache_state as string) ||
    typeof data.is_scanning !== 'boolean'
  ) {
    throw new ApiError('malformed', 'Unexpected status payload')
  }
}

function assertResults(data: unknown): asserts data is ResultsResponse {
  assertStatus(data)
  const nodes = (data as unknown as Record<string, unknown>).nodes
  if (!Array.isArray(nodes) || !nodes.every((n) => isObject(n) && typeof n.link === 'string' && typeof n.id === 'string')) {
    throw new ApiError('malformed', 'Unexpected results payload')
  }
}

export const SUBSCRIPTION_PATH = '/api/vless/sub'
export const COUNTRY_RE = /^[A-Za-z]{2}$/

export function createApi(config: ApiConfig) {
  const base = () => (typeof config.baseUrl === 'function' ? config.baseUrl() : config.baseUrl)

  async function requestOnce(path: string, opts: RequestOptions): Promise<{ status: number; data: unknown }> {
    const controller = new AbortController()
    let timedOut = false
    const timer = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, opts.timeoutMs ?? DEFAULT_TIMEOUT_MS)
    const onAbort = () => controller.abort()
    opts.signal?.addEventListener('abort', onAbort, { once: true })

    try {
      let response: Response
      try {
        response = await (config.fetch ?? fetch)(`${base()}${path}`, {
          method: opts.method ?? 'GET',
          signal: controller.signal,
          headers: opts.body === undefined
            ? { Accept: 'application/json' }
            : { Accept: 'application/json', 'Content-Type': 'application/json' },
          body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
        })
      } catch (err) {
        if (timedOut) throw new ApiError('timeout', `Timed out: ${path}`)
        if (opts.signal?.aborted) throw new ApiError('aborted', 'Request aborted')
        throw new ApiError('network', `Network error: ${path}: ${String(err)}`)
      }

      if (response.status === 429) {
        const retryAfter = Number(response.headers.get('Retry-After')) || 60
        throw new ApiError('rate_limit', 'Too many requests', { status: 429, retryAfter })
      }
      if (!response.ok && !opts.accept?.includes(response.status)) {
        let detail: unknown
        try {
          detail = ((await response.json()) as { detail?: unknown }).detail
        } catch {
          detail = undefined
        }
        const retryAfter = Number(response.headers.get('Retry-After')) || undefined
        throw new ApiError('server', `HTTP ${response.status} for ${path}`, { status: response.status, detail, retryAfter })
      }
      try {
        return { status: response.status, data: await response.json() }
      } catch {
        // A body cut off mid-way by a dropped connection also lands here.
        if (timedOut) throw new ApiError('timeout', `Timed out: ${path}`)
        if (opts.signal?.aborted) throw new ApiError('aborted', 'Request aborted')
        throw new ApiError('malformed', `Invalid JSON from ${path}`)
      }
    } finally {
      clearTimeout(timer)
      opts.signal?.removeEventListener('abort', onAbort)
    }
  }

  async function requestFull(path: string, opts: RequestOptions = {}) {
    const retries = opts.retries ?? 0
    for (let attempt = 0; ; attempt++) {
      try {
        return await requestOnce(path, opts)
      } catch (err) {
        const retryable =
          err instanceof ApiError &&
          (err.kind === 'network' || err.kind === 'timeout' || (err.kind === 'server' && (err.status ?? 0) >= 500))
        if (!retryable || attempt >= retries) throw err
        await sleep(600 * 2 ** attempt + Math.random() * 300, opts.signal)
      }
    }
  }

  const request = async (path: string, opts: RequestOptions = {}) => (await requestFull(path, opts)).data

  return {
    async fetchResults(signal?: AbortSignal): Promise<ResultsResponse> {
      const data = await request('/api/results', { signal, retries: 2 })
      assertResults(data)
      return data
    },

    async fetchStatus(signal?: AbortSignal): Promise<ScanStatus> {
      const data = await request('/api/scan/status', { signal, timeoutMs: 8_000, retries: 1 })
      assertStatus(data)
      return data
    },

    async requestScan(signal?: AbortSignal): Promise<ScanRequestResponse> {
      const data = await request('/api/scan', { method: 'POST', signal })
      assertStatus(data)
      return data as ScanRequestResponse
    },

    /** Ignore today's cache and rescan. The server allows this once per cooldown for everyone. */
    async requestRefresh(signal?: AbortSignal): Promise<RefreshResponse> {
      const data = await request('/api/cache/refresh', { method: 'POST', signal })
      assertStatus(data)
      const retryAfter = (data as unknown as Record<string, unknown>).retry_after
      if (typeof retryAfter !== 'number') throw new ApiError('malformed', 'Unexpected refresh payload')
      return data as RefreshResponse
    },

    /**
     * Configs that exit in one country. `ready` is false (HTTP 202) while the first search for
     * that country is queued or running: ask again in a few seconds. 503 (queue full) arrives
     * as an ApiError of kind 'server' with `retryAfter`.
     */
    async searchCountry(country: string, signal?: AbortSignal): Promise<CountrySearchResponse> {
      if (!COUNTRY_RE.test(country)) throw new ApiError('malformed', 'country must be a two-letter ISO code')
      const { status, data } = await requestFull(`/api/vless/search/${country.toUpperCase()}`, { signal, accept: [202], retries: 1 })
      assertResults(data)
      return { ...(data as ResultsResponse), country: country.toUpperCase(), ready: status !== 202 } as CountrySearchResponse
    },

    async fetchProviders(signal?: AbortSignal): Promise<ProvidersResponse> {
      const data = await request('/api/providers', { signal, retries: 2 })
      if (!isObject(data) || !Array.isArray(data.providers) || !Array.isArray(data.probes)) {
        throw new ApiError('malformed', 'Unexpected providers payload')
      }
      return data as unknown as ProvidersResponse
    },

    /** "Add your provider": returns a one-time token and the install commands. Never retried. */
    async addProvider(form: ProviderForm, signal?: AbortSignal): Promise<EnrollmentCreated> {
      const body = {
        ...form,
        country: form.country.trim().toUpperCase(),
        contact_phone: form.contact_phone.trim() || null,
      }
      const data = await request('/api/providers', { method: 'POST', body, signal })
      if (!isObject(data) || typeof data.token !== 'string' || typeof data.enrollment_id !== 'string' || !isObject(data.install)) {
        throw new ApiError('malformed', 'Unexpected enrollment payload')
      }
      return data as unknown as EnrollmentCreated
    },

    /** Installation progress. The token goes in the body, never in a URL. */
    async fetchEnrollment(enrollmentId: string, token: string, signal?: AbortSignal): Promise<EnrollmentStatus> {
      const data = await request('/api/providers/enrollment', {
        method: 'POST',
        body: { enrollment_id: enrollmentId, token },
        signal,
        timeoutMs: 8_000,
      })
      if (!isObject(data) || typeof data.state !== 'string') throw new ApiError('malformed', 'Unexpected enrollment status')
      return data as unknown as EnrollmentStatus
    },

    async fetchReleases(signal?: AbortSignal): Promise<ReleasesResponse> {
      const data = await request('/api/probes/releases', { signal, retries: 1 })
      if (!isObject(data) || !Array.isArray(data.releases)) throw new ApiError('malformed', 'Unexpected releases payload')
      return data as unknown as ReleasesResponse
    },

    /** The subscription link VPN clients add (they fetch it themselves). */
    subscriptionUrl: () => `${base()}${SUBSCRIPTION_PATH}`,
  }
}

export type Api = ReturnType<typeof createApi>
