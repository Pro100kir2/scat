import { afterEach, describe, expect, it, vi } from 'vitest'

afterEach(() => {
  vi.unstubAllEnvs()
  vi.resetModules()
})

describe('env', () => {
  it('ignores non-http donation links', async () => {
    vi.stubEnv('VITE_DONATE_URL', 'javascript:alert(1)')
    const { env } = await import('@/config/env')
    expect(env.donateUrl).toBeNull()
  })

  it('accepts https donation links and trims the API url', async () => {
    vi.stubEnv('VITE_DONATE_URL', 'https://boosty.to/skat')
    vi.stubEnv('VITE_API_URL', 'https://api.example.com/')
    const { env } = await import('@/config/env')
    expect(env.donateUrl).toBe('https://boosty.to/skat')
    expect(env.apiUrl).toBe('https://api.example.com')
  })
})
