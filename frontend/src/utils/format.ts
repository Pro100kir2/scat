/** Russian plural: plural(5, ['жемчужина', 'жемчужины', 'жемчужин']) → 'жемчужин'. */
export function plural(n: number, forms: readonly [string, string, string]): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return forms[0]
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return forms[1]
  return forms[2]
}

export const PEARLS = ['жемчужина', 'жемчужину', 'жемчужин'] as const
export const PEARLS_NOM = ['жемчужина', 'жемчужины', 'жемчужин'] as const

const numberFormat = new Intl.NumberFormat('ru-RU')
export const formatNumber = (n: number) => numberFormat.format(n)

const timeFormat = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' })
const dayFormat = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' })

/** "сегодня в 17:57", "вчера в 09:10", "12 марта в 10:00". */
export function formatMoment(iso: string, now = new Date()): string {
  const date = new Date(iso)
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const days = Math.round((startOfDay(now) - startOfDay(date)) / 86_400_000)
  const time = timeFormat.format(date)
  if (days === 0) return `сегодня в ${time}`
  if (days === 1) return `вчера в ${time}`
  return `${dayFormat.format(date)} в ${time}`
}

/** "через 23 ч", "через 40 мин", or null when already past. */
export function formatUntil(iso: string, now = Date.now()): string | null {
  const minutes = Math.round((new Date(iso).getTime() - now) / 60_000)
  if (minutes <= 0) return null
  if (minutes < 60) return `через ${minutes} мин`
  return `через ${Math.round(minutes / 60)} ч`
}

let regionNames: Intl.DisplayNames | null = null
export function countryName(code: string | null): string | null {
  if (!code) return null
  try {
    regionNames ??= new Intl.DisplayNames(['ru'], { type: 'region' })
    return regionNames.of(code) ?? code
  } catch {
    return code
  }
}

export type Quality = 'excellent' | 'good' | 'fair' | 'slow'

/**
 * Latency here is a full HTTPS request through the proxy (connect + TLS + response),
 * so it is several times higher than a ping. Tiers are tuned for that measurement.
 */
export function latencyQuality(ms: number | null): Quality {
  if (ms === null) return 'slow'
  if (ms < 600) return 'excellent'
  if (ms < 900) return 'good'
  if (ms < 1400) return 'fair'
  return 'slow'
}

export const QUALITY_LABEL: Record<Quality, string> = {
  excellent: 'Отлично',
  good: 'Хорошо',
  fair: 'Терпимо',
  slow: 'Медленно',
}

export const QUALITY_LEVEL: Record<Quality, number> = { excellent: 4, good: 3, fair: 2, slow: 1 }

const NETWORK_LABEL: Record<string, string> = {
  tcp: 'TCP',
  ws: 'WebSocket',
  grpc: 'gRPC',
  xhttp: 'XHTTP',
  httpupgrade: 'HTTPUpgrade',
}
export const networkLabel = (n: string) => NETWORK_LABEL[n] ?? n.toUpperCase()

const SECURITY_LABEL: Record<string, string> = { reality: 'REALITY', tls: 'TLS', none: 'Без TLS' }
export const securityLabel = (s: string) => SECURITY_LABEL[s] ?? s.toUpperCase()

const SECURITY_HINT: Record<string, string> = {
  reality: 'Трафик выглядит как обычное посещение крупного сайта',
  tls: 'Соединение зашифровано TLS',
  none: 'Транспорт без шифрования — используйте только для нечувствительных задач',
}
export const securityHint = (s: string) => SECURITY_HINT[s] ?? ''
