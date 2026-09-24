import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, clearCache, fetchResults, requestScan } from '@/services/api'

const status = { cache_state: 'fresh', is_scanning: false, progress: null, last_scan_failed: false, cache: null }
const results = { ...status, nodes: [{ id: 'a', link: 'vless://x', rank: 1 }] }

function reply(body: unknown, init: ResponseInit = {}) {
  return new Response(typeof body === 'string' ? body : JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('api client', () => {
  it('sends POST to clear cache', async () => {
    const fetch = vi.fn().mockResolvedValue(reply({ outcome: 'started', ...status }))
    vi.stubGlobal('fetch', fetch)
    const data = await clearCache()
    expect(data.outcome).toBe('started')
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/api/cache/clear'), expect.objectContaining({ method: 'POST' }))
  })

  it('returns validated results', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(results)))
    const data = await fetchResults()
    expect(data.nodes[0]!.link).toBe('vless://x')
  })

  it('retries idempotent requests after a 5xx', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(reply('oops', { status: 502 })).mockResolvedValueOnce(reply(results))
    vi.stubGlobal('fetch', fetch)
    await expect(fetchResults()).resolves.toMatchObject({ cache_state: 'fresh' })
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('does not retry POST and surfaces rate limits with Retry-After', async () => {
    const fetch = vi.fn().mockResolvedValue(reply({ detail: 'slow down' }, { status: 429, headers: { 'Retry-After': '42' } }))
    vi.stubGlobal('fetch', fetch)
    const err = await requestScan().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ kind: 'rate_limit', retryAfter: 42 })
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('rejects malformed payloads', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply({ nodes: 'nope' })))
    await expect(fetchResults()).rejects.toMatchObject({ kind: 'malformed' })
  })

  it('rejects invalid JSON', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply('<html>proxy error</html>')))
    await expect(requestScan()).rejects.toMatchObject({ kind: 'malformed' })
  })

  it('times out hanging requests', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) => init.signal!.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))),
      ),
    )
    const pending = requestScan().catch((e: unknown) => e)
    await vi.advanceTimersByTimeAsync(13_000)
    expect(await pending).toMatchObject({ kind: 'timeout' })
  })

  it('maps offline errors to network', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(requestScan()).rejects.toMatchObject({ kind: 'network' })
  })
})
