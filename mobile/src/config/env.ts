/**
 * Public, client-safe configuration. EXPO_PUBLIC_* values are compiled into the bundle, so
 * nothing secret may ever go here. A release build must never talk to a development host:
 * anything that is not a public https origin falls back to production.
 */
export const PRODUCTION_API_URL = 'https://api.scat-search.ru'
export const PRODUCTION_SITE_URL = 'https://scat-search.ru'

const LOCAL_HOST_RE = /^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|0\.0\.0\.0|\[?::1\]?)|\.(local|lan|test|internal)$/i

export function resolveOrigin(value: string | undefined, fallback: string, dev: boolean): string {
  const url = (value ?? '').trim().replace(/\/+$/, '')
  if (!url) return fallback
  const match = /^(https?):\/\/([^/:?#]+)(:\d+)?$/i.exec(url)
  if (!match) return fallback
  if (dev) return url
  const [, scheme, host] = match
  if (scheme!.toLowerCase() !== 'https' || LOCAL_HOST_RE.test(host!)) return fallback
  return url
}

export const env = {
  /** Скат API origin, no trailing slash. */
  apiUrl: resolveOrigin(process.env.EXPO_PUBLIC_API_URL, PRODUCTION_API_URL, __DEV__),
  /** The public site: adding a provider, the probe guide. */
  siteUrl: resolveOrigin(process.env.EXPO_PUBLIC_SITE_URL, PRODUCTION_SITE_URL, __DEV__),
}
