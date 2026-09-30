import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, RouterLinkStub } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { adminRequest, errorText, queryString } from '@/admin/api'
import { CURSOR_KEY, decide, isMuted, MUTE_KEY, readCursor, setMuted, type NotificationsResponse } from '@/admin/notify'
import { ApiError } from '@/services/api'

function reply(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' }, ...init })
}

const event = (id: number, probe = `prb_${String(id).padStart(16, '0')}`) =>
  ({ id, ts: 1_700_000_000 + id, probe_id: probe, data: { profile: 'linux-mipsle', status: 'pending' } })

const notifications = (over: Partial<NotificationsResponse> = {}): NotificationsResponse =>
  ({ latest_id: 0, new: [], unread_count: 0, unread: [], ...over })

beforeEach(() => localStorage.clear())
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('new-probe notifications', () => {
  it('the very first poll only sets the cursor: old probes never ring', () => {
    expect(decide(null, notifications({ latest_id: 42, unread_count: 3 }))).toEqual({ cursor: 42, announce: [], sound: false })
  })

  it('a genuinely new probe is announced once, with one sound', () => {
    const first = decide(42, notifications({ latest_id: 43, new: [event(43)], unread_count: 1 }))
    expect(first.sound).toBe(true)
    expect(first.announce.map((e) => e.id)).toEqual([43])
    // The next poll carries the new cursor: the probe is still pending and unread, but no sound.
    const again = decide(first.cursor, notifications({ latest_id: 43, new: [], unread_count: 1 }))
    expect(again).toEqual({ cursor: 43, announce: [], sound: false })
  })

  it('several probes at once: one sound, every probe announced', () => {
    const d = decide(10, notifications({ latest_id: 13, new: [event(11), event(12), event(13)] }))
    expect(d.sound).toBe(true)
    expect(d.announce).toHaveLength(3)
    expect(d.cursor).toBe(13)
  })

  it('never goes backwards or replays events it already saw', () => {
    expect(decide(50, notifications({ latest_id: 43, new: [event(43)] }))).toEqual({ cursor: 50, announce: [], sound: false })
  })

  it('the cursor survives a reload; garbage in storage means "first poll"', () => {
    localStorage.setItem(CURSOR_KEY, '17')
    expect(readCursor()).toBe(17)
    localStorage.setItem(CURSOR_KEY, 'nope')
    expect(readCursor()).toBeNull()
  })

  it('the mute preference persists', () => {
    expect(isMuted()).toBe(false)
    setMuted(true)
    expect(localStorage.getItem(MUTE_KEY)).toBe('1')
    expect(isMuted()).toBe(true)
    setMuted(false)
    expect(isMuted()).toBe(false)
  })
})

describe('admin API client', () => {
  it('uses the session cookie and the CSRF header, never an Authorization header', async () => {
    const fetch = vi.fn().mockResolvedValue(reply({ items: [] }))
    vi.stubGlobal('fetch', fetch)
    await adminRequest('/probes', { query: { status: 'pending', q: '', page: 2, online: undefined } })
    const [url, init] = fetch.mock.calls[0]!
    expect(url).toBe('/api/admin/probes?status=pending&page=2')
    expect(init.credentials).toBe('include')
    expect(init.headers['X-Scat-Admin']).toBe('1')
    expect(init.headers.Authorization).toBeUndefined()
  })

  it('the token only travels in the login body', async () => {
    const fetch = vi.fn().mockResolvedValue(reply({ authenticated: true, expires_at: 1 }))
    vi.stubGlobal('fetch', fetch)
    const { login } = await import('@/admin/api')
    await login('secret-token')
    const [url, init] = fetch.mock.calls[0]!
    expect(url).toBe('/api/admin/session')
    expect(url).not.toContain('secret-token')
    expect(JSON.parse(init.body)).toEqual({ token: 'secret-token' })
    expect(JSON.stringify(localStorage)).not.toContain('secret-token')
    expect(JSON.stringify(sessionStorage)).not.toContain('secret-token')
  })

  it('reports server details as readable text', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply({ detail: 'URL refused: 10.0.0.1 is not a public address' }, { status: 422 })))
    const err = await adminRequest('/sources', { method: 'POST', body: {} }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(errorText(err)).toContain('not a public address')
    expect(errorText(new ApiError('server', 'x', { detail: [{ loc: ['body', 'priority'], msg: 'too big' }] }))).toBe('priority: too big')
  })

  it('builds query strings without empty values', () => {
    expect(queryString({ a: '', b: null, c: undefined, d: 0, e: false, f: 'x y' })).toBe('?d=0&e=false&f=x+y')
    expect(queryString({})).toBe('')
  })
})

describe('admin panel', () => {
  it('shows the login form when there is no session, and signs in without keeping the token', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(reply({ detail: 'Admin authentication required' }, { status: 401 }))  // GET /session
      .mockResolvedValueOnce(reply({ authenticated: true, expires_at: 1 }))  // POST /session
      .mockResolvedValue(reply({ latest_id: 5, new: [], unread_count: 2, unread: [] }))  // notifications
    vi.stubGlobal('fetch', fetch)
    const { default: AdminLayout } = await import('@/admin/AdminLayout.vue')
    const { admin } = await import('@/admin/state')
    admin.auth = 'unknown'
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/admin/', component: { template: '<p />' } }] })
    await router.push('/admin/')
    const w = mount(AdminLayout, { global: { plugins: [router], stubs: { RouterLink: RouterLinkStub } } })
    await flushPromises()
    expect(w.text()).toContain('Admin token')
    await w.find('input[type="password"]').setValue('the-token')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(admin.auth).toBe('in')
    expect(w.text()).toContain('Probes')
    expect(w.find('.dot').text()).toBe('2')  // unread new probes
    expect((w.vm as unknown as { token: string }).token).toBe('')
    expect(localStorage.getItem(CURSOR_KEY)).toBe('5')  // first poll: cursor set silently
    w.unmount()
  })
})
