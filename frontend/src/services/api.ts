import { env } from '@/config/env'
import type { RefreshResponse, ResultsResponse, ScanRequestResponse, ScanStatus } from '@/types/api'

export type ApiErrorKind = 'network' | 'timeout' | 'rate_limit' | 'server' | 'malformed' | 'aborted'

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status?: number
  readonly retryAfter?: number

  constructor(kind: ApiErrorKind, message: string, opts: { status?: number; retryAfter?: number } = {}) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = opts.status
    this.retryAfter = opts.retryAfter
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST'
  signal?: AbortSignal
  timeoutMs?: number
  /** Extra attempts for network errors and 5xx. Only used for idempotent requests. */
  retries?: number
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

async function requestOnce(path: string, opts: RequestOptions): Promise<unknown> {
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
      response = await fetch(`${env.apiUrl}${path}`, {
        method: opts.method ?? 'GET',
        signal: controller.signal,
        headers: { Accept: 'application/json' },
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
    if (!response.ok) {
      throw new ApiError('server', `HTTP ${response.status} for ${path}`, { status: response.status })
    }
    try {
      return await response.json()
    } catch {
      throw new ApiError('malformed', `Invalid JSON from ${path}`)
    }
  } finally {
    clearTimeout(timer)
    opts.signal?.removeEventListener('abort', onAbort)
  }
}

async function request(path: string, opts: RequestOptions = {}): Promise<unknown> {
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

// --- response validation --------------------------------------------------

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

// --- endpoints --------------------------------------------------------------

export async function fetchResults(signal?: AbortSignal): Promise<ResultsResponse> {
  const data = await request('/api/results', { signal, retries: 2 })
  assertResults(data)
  return data
}

export async function fetchStatus(signal?: AbortSignal): Promise<ScanStatus> {
  const data = await request('/api/scan/status', { signal, timeoutMs: 8_000, retries: 1 })
  assertStatus(data)
  return data
}

export async function requestScan(signal?: AbortSignal): Promise<ScanRequestResponse> {
  const data = await request('/api/scan', { method: 'POST', signal })
  assertStatus(data)
  return data as ScanRequestResponse
}

/** Ignore today's cache and rescan. The server allows this once per cooldown for everyone. */
export async function requestRefresh(signal?: AbortSignal): Promise<RefreshResponse> {
  const data = await request('/api/cache/refresh', { method: 'POST', signal })
  assertStatus(data)
  const retryAfter = (data as unknown as Record<string, unknown>).retry_after
  if (typeof retryAfter !== 'number') throw new ApiError('malformed', 'Unexpected refresh payload')
  return data as RefreshResponse
}
