import { PRODUCTION_API_URL, resolveOrigin } from '@/config/env'

describe('API origin', () => {
  it('a release build never points at a development host', () => {
    for (const bad of ['http://localhost:8000', 'http://127.0.0.1:8000', 'http://192.168.1.20:8000', 'https://localhost',
      'http://api.scat-search.ru', 'https://10.0.0.5', 'https://dev.lan', 'not a url', 'https://api.example/path', '']) {
      expect(resolveOrigin(bad, PRODUCTION_API_URL, false)).toBe(PRODUCTION_API_URL)
    }
    expect(resolveOrigin(undefined, PRODUCTION_API_URL, false)).toBe(PRODUCTION_API_URL)
  })

  it('a release build accepts a public https origin, without a trailing slash', () => {
    expect(resolveOrigin('https://api.staging.example/', PRODUCTION_API_URL, false)).toBe('https://api.staging.example')
  })

  it('development may use a LAN backend', () => {
    expect(resolveOrigin('http://192.168.1.20:8000', PRODUCTION_API_URL, true)).toBe('http://192.168.1.20:8000')
  })
})
