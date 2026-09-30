import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, addProvider, fetchEnrollment, fetchProviders } from '@/services/api'

function reply(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' }, ...init })
}

afterEach(() => vi.unstubAllGlobals())

describe('provider API client', () => {
  it('sends the provider form as JSON, normalised', async () => {
    const created = { enrollment_id: 'enr_1', token: 'skenr_x', install: { curl: 'c' }, provider: {} }
    const fetch = vi.fn().mockResolvedValue(reply(created, { status: 201 }))
    vi.stubGlobal('fetch', fetch)
    await addProvider({ name: 'МТС', country: 'ru ', region: 'Москва', info: '', contact_phone: ' ' })
    const [url, init] = fetch.mock.calls[0]!
    expect(url).toBe('/api/providers')
    expect(init.method).toBe('POST')
    expect(init.headers['Content-Type']).toBe('application/json')
    expect(JSON.parse(init.body)).toEqual({
      name: 'МТС', country: 'RU', region: 'Москва', info: '', contact_phone: null,
    })
  })

  it('sends the optional contact phone in the body only', async () => {
    const created = { enrollment_id: 'enr_1', token: 'skenr_x', install: { curl: 'c' }, provider: {} }
    const fetch = vi.fn().mockResolvedValue(reply(created, { status: 201 }))
    vi.stubGlobal('fetch', fetch)
    await addProvider({ name: 'МТС', country: 'RU', region: '', info: '', contact_phone: '+7 912 345-67-89 ' })
    const [url, init] = fetch.mock.calls[0]!
    expect(url).not.toContain('912')
    expect(JSON.parse(init.body).contact_phone).toBe('+7 912 345-67-89')
  })

  it('keeps the enrollment token out of URLs', async () => {
    const fetch = vi.fn().mockResolvedValue(reply({ state: 'waiting' }))
    vi.stubGlobal('fetch', fetch)
    await fetchEnrollment('enr_1', 'skenr_secret')
    const [url, init] = fetch.mock.calls[0]!
    expect(url).toBe('/api/providers/enrollment')
    expect(url).not.toContain('skenr_secret')
    expect(JSON.parse(init.body)).toEqual({ enrollment_id: 'enr_1', token: 'skenr_secret' })
  })

  it('exposes field errors from a 422', async () => {
    const detail = [{ loc: ['body', 'name'], msg: 'too short' }]
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply({ detail }, { status: 422 })))
    const err = await addProvider({ name: 'x', country: 'RU', region: '', info: '', contact_phone: '' }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 422, detail })
  })

  it('validates the providers payload', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply({ providers: 'nope' })))
    await expect(fetchProviders()).rejects.toMatchObject({ kind: 'malformed' })
  })
})
