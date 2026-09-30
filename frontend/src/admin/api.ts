import { env } from '@/config/env'
import { ApiError } from '@/services/api'

/**
 * Admin API client. Authentication is the HttpOnly session cookie set by POST
 * /api/admin/session: this code never stores ADMIN_TOKEN, it only passes it through once
 * when logging in. Every request carries X-Scat-Admin: 1, which the server requires
 * (with the cookie) as CSRF protection.
 */

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

export type Query = Record<string, string | number | boolean | null | undefined>

const listeners = new Set<() => void>()

/** Called whenever the server says the session is gone (401). */
export function onUnauthorized(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export function queryString(query?: Query): string {
  if (!query) return ''
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

export async function adminRequest<T>(path: string, opts: { method?: Method; body?: unknown; query?: Query; timeoutMs?: number } = {}): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs ?? 30_000)
  const headers: Record<string, string> = { Accept: 'application/json', 'X-Scat-Admin': '1' }
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  let response: Response
  try {
    response = await fetch(`${env.apiUrl}/api/admin${path}${queryString(opts.query)}`, {
      method: opts.method ?? 'GET',
      credentials: 'include',
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: controller.signal,
    })
  } catch (err) {
    throw new ApiError(controller.signal.aborted ? 'timeout' : 'network', `Admin request failed: ${String(err)}`)
  } finally {
    clearTimeout(timer)
  }
  if (response.status === 401 && path !== '/session') listeners.forEach((fn) => fn())
  if (!response.ok) {
    let detail: unknown
    try {
      detail = ((await response.json()) as { detail?: unknown }).detail
    } catch {
      detail = undefined
    }
    const kind = response.status === 429 ? 'rate_limit' : 'server'
    throw new ApiError(kind, `HTTP ${response.status}`, { status: response.status, detail })
  }
  return (await response.json()) as T
}

/** A human line for an error, for toasts and error states. */
export function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (typeof err.detail === 'string') return err.detail
    if (Array.isArray(err.detail)) {
      return (err.detail as { loc?: unknown[]; msg?: string }[])
        .map((d) => `${d.loc?.[d.loc.length - 1] ?? 'field'}: ${d.msg ?? 'invalid'}`)
        .join('; ')
    }
    if (err.kind === 'network') return 'API unreachable'
    if (err.kind === 'timeout') return 'Request timed out'
    if (err.kind === 'rate_limit') return 'Too many requests, wait a moment'
    return err.message
  }
  return String(err)
}

export const login = (token: string) => adminRequest<{ authenticated: boolean; expires_at: number }>('/session', { method: 'POST', body: { token } })
export const logout = () => adminRequest<{ authenticated: boolean }>('/session', { method: 'DELETE' })
export const session = () => adminRequest<{ authenticated: boolean; via: string }>('/session')
