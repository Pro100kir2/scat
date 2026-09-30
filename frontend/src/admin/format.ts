/** Formatting for the admin panel (English, precise, compact). */

const dateTime = new Intl.DateTimeFormat('en-GB', {
  year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
})
const clock = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit' })

export function when(ts: number | null | undefined): string {
  return ts ? dateTime.format(new Date(ts * 1000)) : '—'
}

export function hhmm(ts: number | null | undefined): string {
  return ts ? clock.format(new Date(ts * 1000)) : '—'
}

export function ago(ts: number | null | undefined, now = Date.now()): string {
  if (!ts) return 'never'
  const s = Math.round(now / 1000 - ts)
  if (s < 0) return `in ${duration(-s)}`
  if (s < 10) return 'just now'
  return `${duration(s)} ago`
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  const s = Math.round(seconds)
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60 ? `${s % 60}s` : ''}`.trim()
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`
}

export function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = n
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i++
  }
  return `${value < 10 && i ? value.toFixed(1) : Math.round(value)} ${units[i]}`
}

export const kb = (n: number | null | undefined) => (n === null || n === undefined ? '—' : bytes(n * 1024))

export function num(n: number | null | undefined): string {
  return n === null || n === undefined ? '—' : new Intl.NumberFormat('en-GB').format(n)
}

/** Probe lifecycle → label and tone. Raw codes stay visible in details, not as the headline. */
export const LIFECYCLE: Record<string, { label: string; tone: 'ok' | 'warn' | 'bad' | 'muted' | 'info' }> = {
  registered: { label: 'Registered', tone: 'info' },
  verifying: { label: 'Verifying', tone: 'warn' },
  verified: { label: 'Verified', tone: 'ok' },
  verification_failed: { label: 'Verification failed', tone: 'bad' },
  disabled: { label: 'Disabled', tone: 'muted' },
}

export const VERIFY_REASON: Record<string, string> = {
  never_connected: 'Registered but never contacted the server again',
  jobs_not_fetched: 'Online but did not fetch its verification jobs',
  no_result: 'Fetched the verification job but never reported a result',
  api_unreachable: 'Could not open the verification URL from its network',
  unexpected_response: 'Verification URL answered with an unexpected status (proxy or captive portal?)',
  nonce_not_received: 'Reported success, but the server never received its request',
  probe_error: 'Could not run the verification check',
  server_misconfigured: 'Server has no public URL to verify against (PUBLIC_API_URL)',
  spot_check_failed: 'Reported success for a spot check it did not run',
  superseded: 'Replaced by a newer verification',
}

export const reasonText = (code: string | null | undefined) => (code ? VERIFY_REASON[code] ?? code : '')

export const STATUS_TONE = { completed: 'ok', running: 'warn', failed: 'bad', cancelled: 'muted' } as const
