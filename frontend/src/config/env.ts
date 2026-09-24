function cleanUrl(value: string | undefined): string {
  return (value ?? '').trim().replace(/\/+$/, '')
}

function safeHttpUrl(value: string | undefined): string | null {
  const url = cleanUrl(value)
  if (!url) return null
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : null
  } catch {
    return null
  }
}

export const env = {
  /** Backend origin; empty means same origin (Vite dev proxy or a reverse proxy). */
  apiUrl: cleanUrl(import.meta.env.VITE_API_URL),
  donateUrl: safeHttpUrl(import.meta.env.VITE_DONATE_URL),
}

/** Absolute URL for an API path, usable outside fetch (subscription links). */
export function apiHref(path: string): string {
  return `${env.apiUrl || window.location.origin}${path}`
}
