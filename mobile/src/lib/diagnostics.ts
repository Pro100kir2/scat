import { ApiError } from '@scat/shared'
import { env } from '@/config/env'

const KIND_HINT: Record<string, string> = {
  network: 'no connection to the server: DNS, routing/VPN, TLS or the server is unreachable',
  timeout: 'the server did not answer in time',
  server: 'the server answered with an HTTP error',
  malformed: 'the server answered, but not with the expected JSON',
  rate_limit: 'rate limited by the server',
  aborted: 'request cancelled',
}

/**
 * One development-log line for a failed API call: failure type, host, endpoint, HTTP status.
 * Built only from the error's own message (endpoint path and the network error text), never
 * from request bodies or links, so no VLESS configuration or secret can end up in it.
 * Users never see this: the screens show the short Russian wording from the shared package.
 */
export function describeFailure(context: string, err: unknown): string {
  let host = env.apiUrl
  try {
    host = new URL(env.apiUrl).host
  } catch {
    // keep the raw value
  }
  if (!(err instanceof ApiError)) return `[Скат] ${context}: unexpected ${String(err)} (host ${host})`
  const status = err.status === undefined ? '' : ` status=${err.status}`
  return `[Скат] ${context}: ${err.kind}${status} host=${host} — ${KIND_HINT[err.kind] ?? err.kind} (${err.message})`
}
