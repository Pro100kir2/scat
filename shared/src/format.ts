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

/** Unix seconds in the past: "только что", "3 мин назад", "5 ч назад", "12 марта". */
export function formatAgo(unixSeconds: number | null, now = Date.now()): string {
  if (!unixSeconds) return 'никогда'
  const seconds = Math.max(0, Math.round(now / 1000 - unixSeconds))
  if (seconds < 60) return 'только что'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} мин назад`
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)} ч назад`
  return dayFormat.format(new Date(unixSeconds * 1000))
}

/** Seconds as a timer: 600 → "10:00", 65 → "1:05". */
export function formatCountdown(seconds: number): string {
  const s = Math.max(0, Math.ceil(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** Russian names for runtimes without Intl.DisplayNames (React Native's Hermes). */
const COUNTRY_NAMES: Record<string, string> = {
  AE: 'ОАЭ', AL: 'Албания', AM: 'Армения', AR: 'Аргентина', AT: 'Австрия', AU: 'Австралия', AZ: 'Азербайджан',
  BE: 'Бельгия', BG: 'Болгария', BR: 'Бразилия', BY: 'Беларусь', CA: 'Канада', CH: 'Швейцария', CL: 'Чили',
  CN: 'Китай', CY: 'Кипр', CZ: 'Чехия', DE: 'Германия', DK: 'Дания', EE: 'Эстония', EG: 'Египет', ES: 'Испания',
  FI: 'Финляндия', FR: 'Франция', GB: 'Великобритания', GE: 'Грузия', GR: 'Греция', HK: 'Гонконг', HR: 'Хорватия',
  HU: 'Венгрия', ID: 'Индонезия', IE: 'Ирландия', IL: 'Израиль', IN: 'Индия', IR: 'Иран', IS: 'Исландия',
  IT: 'Италия', JP: 'Япония', KG: 'Киргизия', KR: 'Южная Корея', KZ: 'Казахстан', LT: 'Литва', LU: 'Люксембург',
  LV: 'Латвия', MD: 'Молдова', MX: 'Мексика', MY: 'Малайзия', NL: 'Нидерланды', NO: 'Норвегия', NZ: 'Новая Зеландия',
  PH: 'Филиппины', PL: 'Польша', PT: 'Португалия', RO: 'Румыния', RS: 'Сербия', RU: 'Россия', SE: 'Швеция',
  SG: 'Сингапур', SI: 'Словения', SK: 'Словакия', TH: 'Таиланд', TR: 'Турция', TW: 'Тайвань', UA: 'Украина',
  US: 'США', UZ: 'Узбекистан', VN: 'Вьетнам', ZA: 'ЮАР',
}

/** Countries offered for a country search, most asked-for first. */
export const SEARCH_COUNTRIES = ['DE', 'NL', 'FI', 'US', 'GB', 'FR', 'SE', 'PL', 'TR', 'KZ', 'JP', 'SG', 'CH', 'AT', 'ES', 'IT'] as const

let regionNames: Intl.DisplayNames | null | undefined
export function countryName(code: string | null): string | null {
  if (!code) return null
  const upper = code.toUpperCase()
  if (regionNames === undefined) {
    try {
      regionNames = typeof Intl.DisplayNames === 'function' ? new Intl.DisplayNames(['ru'], { type: 'region' }) : null
    } catch {
      regionNames = null
    }
  }
  try {
    const name = regionNames?.of(upper)
    if (name && name !== upper) return name
  } catch {
    /* not a region code: fall through */
  }
  return COUNTRY_NAMES[upper] ?? code
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
