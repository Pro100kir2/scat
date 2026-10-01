/**
 * What the scan state reads like: the hero's "logbook" line and the results section's phase.
 * One wording for the site and the app.
 */
import { formatMoment, formatNumber, formatUntil, plural, PEARLS_NOM } from './format'
import { scanCache, scanProgress, type ScanState } from './scan'
import type { ScanProgress } from './types'

export type SceneMode = 'idle' | 'scanning' | 'replay'

/** What the ocean scene should be doing for this state. */
export function sceneMode(state: ScanState): SceneMode {
  if (state.journey !== 'diving') return 'idle'
  return state.diveKind === 'live' ? 'scanning' : 'replay'
}

/** Discoveries the scene should have shown so far. */
export const sceneFound = (state: ScanState) => (state.journey === 'resting' ? 0 : state.found)

const STAGES: Record<string, { title: string; detail: (p: ScanProgress) => string }> = {
  downloading: { title: 'Спускается к источникам', detail: () => 'загружает открытые списки конфигураций' },
  parsing: { title: 'Разбирает находки', detail: () => 'отбрасывает дубликаты и повреждённые ссылки' },
  testing: {
    title: 'Ищет рабочие раковины',
    detail: (p) =>
      `${p.working_candidates} из ${p.target_candidates} найдено · проверено ${formatNumber(p.processed_candidates)} из ${formatNumber(p.total_candidates)}`,
  },
  rechecking: {
    title: 'Глубокая проверка',
    detail: (p) => `${p.working_candidates} кандидатов: пять замеров и загрузка 2 МБ через каждый`,
  },
  completed: { title: 'Поднимается на поверхность', detail: () => 'сохраняет улов' },
}

export interface DiveLogView {
  tone: 'quiet' | 'live' | 'done' | 'error'
  title: string
  detail: string
  /** 0..1, only while something is in progress. */
  progress?: number
  /** What "Попробовать снова" should do, when it makes sense to offer it. */
  retry?: 'search' | 'reload'
}

/** One calm line of narrative plus the real numbers behind it. `revealed` = pearls shown so far. */
export function diveLogView(state: ScanState, revealed: number): DiveLogView {
  const cache = scanCache(state)
  const err = state.scanError

  if (err) {
    if (err.kind === 'rate_limit') {
      return { tone: 'error', title: 'Скату нужна передышка', detail: `Слишком много запросов. Попробуйте через ${err.retryAfter ?? 60} с.` }
    }
    return {
      tone: 'error',
      title: 'Не удалось завершить поиск',
      detail: err.kind === 'network' || err.kind === 'timeout'
        ? 'Связь с сервером прервалась. Попробуйте ещё раз через некоторое время.'
        : 'Попробуйте ещё раз через некоторое время.',
      retry: 'search',
    }
  }

  if (state.loadError && !state.nodes.length) {
    return {
      tone: 'error',
      title: 'Скат не отвечает',
      detail: 'Сервер недоступен. Попробуйте ещё раз через некоторое время.',
      retry: 'reload',
    }
  }

  if (state.journey === 'diving') {
    if (state.diveKind === 'replay') {
      return {
        tone: 'live',
        title: 'Возвращается к сегодняшним находкам',
        detail: cache ? `поиск прошёл ${formatMoment(cache.created_at)} — результаты общие для всех на сутки` : '',
        progress: state.found ? revealed / state.found : 0,
      }
    }
    const p = scanProgress(state)
    const stage = p ? STAGES[p.stage] : undefined
    return {
      tone: 'live',
      title: stage?.title ?? 'Погружается',
      detail: p && stage ? stage.detail(p) : 'готовится к погружению',
      progress: p ? p.progress / 100 : 0,
    }
  }

  if (state.journey === 'surfaced' && cache) {
    return {
      tone: 'done',
      title: 'Скат вернулся с уловом',
      detail: `лучшие ${cache.count} из ${cache.working} рабочих · просмотрено ${formatNumber(cache.tested)} конфигураций`,
    }
  }

  if (state.cooldown) {
    return { tone: 'quiet', title: 'Последнее погружение не удалось', detail: 'Показаны предыдущие находки. Скат попробует снова чуть позже.' }
  }

  if (cache) {
    const until = formatUntil(cache.expires_at)
    return {
      tone: 'quiet',
      title: `Последнее погружение ${formatMoment(cache.created_at)}`,
      detail: `${cache.count} ${plural(cache.count, PEARLS_NOM)} · лучшие из ${cache.working} рабочих${until ? ` · следующее ${until}` : ''}`,
    }
  }

  return { tone: 'quiet', title: 'Скат ждёт у края рифа', detail: 'Начните поиск — он нырнёт и проверит открытые конфигурации.' }
}

export type ResultsPhase = 'loading' | 'results' | 'error' | 'searching' | 'empty'

export function resultsPhase(state: ScanState): ResultsPhase {
  if (state.loading) return 'loading'
  if (state.nodes.length) return 'results'
  if (state.loadError) return 'error'
  if (state.status?.is_scanning || state.journey === 'diving') return 'searching'
  return 'empty'
}

export function resultsSubtitle(state: ScanState): string {
  const cache = scanCache(state)
  if (!cache || !state.nodes.length) return ''
  return `Лучшие ${state.nodes.length} из ${cache.working} рабочих · просмотрено ${formatNumber(cache.tested)} из ${formatNumber(cache.total_nodes)} · ${formatMoment(cache.created_at)}`
}

/** Empty, searching and error notes of the results list. */
export const RESULTS_NOTES = {
  searching: {
    title: 'Скат ещё на глубине',
    text: 'Первое погружение займёт пару минут. Жемчужины появятся здесь сами — страницу можно не обновлять.',
  },
  error: {
    title: 'Не удалось связаться со Скатом',
    text: 'Сервер не ответил. Проверьте соединение или попробуйте ещё раз через некоторое время.',
  },
  empty: {
    title: 'Жемчужин не нашлось',
    text: 'Скат обследовал дно, но ни одна конфигурация не прошла проверку. Такое бывает, когда источники обновляются. Загляните чуть позже.',
  },
} as const

/** "How it works": three steps and two honest notes. `totalNodes` comes from the last scan. */
export function howItWorks(totalNodes?: number | null) {
  return {
    steps: [
      {
        title: 'Погружение',
        text: totalNodes
          ? `Раз в сутки Скат собирает открытые списки конфигураций — в последний раз ${formatNumber(totalNodes)} уникальных узлов.`
          : 'Раз в сутки Скат собирает открытые списки конфигураций и отбрасывает дубликаты.',
      },
      {
        title: 'Проверка',
        text: 'Узлы проверяются настоящим подключением через Xray, пока не найдётся 25 рабочих: с проверенным TLS, выходом не через наш адрес, доступом к двум разным сайтам и реальной загрузкой данных.',
      },
      {
        title: 'Отбор',
        text: 'Каждого из 25 кандидатов замеряют пять раз и скачивают через него 2 МБ. Пять лучших по задержке, стабильности и скорости — это и есть жемчужины.',
      },
    ],
    notes: [
      {
        lead: 'Один поиск на всех.',
        text: 'Результаты хранятся сутки и общие для всех посетителей, поэтому страница открывается мгновенно, а источники не перегружаются.',
      },
      {
        lead: 'Честно о рисках.',
        text: 'Это публичные серверы: Скат не владеет ими и не может гарантировать их работу или конфиденциальность. Не передавайте через них ничего важного.',
      },
    ],
  }
}

/** VPN clients that accept the subscription link. */
export const SUBSCRIPTION_CLIENTS = [
  { name: 'v2rayNG', where: 'Android' },
  { name: 'Hiddify', where: 'Android · iOS · ПК' },
  { name: 'Streisand', where: 'iOS · macOS' },
  { name: 'NekoBox', where: 'Android · ПК' },
] as const

/** How long "Очистить кэш" stays locked after use (the server enforces its own limit too). */
export const REFRESH_COOLDOWN_SECONDS = 10 * 60

/** End of a cooldown read back from storage, capped so a stale or edited value can't lock for long. */
export function cappedUntil(stored: unknown, maxSeconds: number, now = Date.now()): number {
  const value = Number(stored)
  return Number.isFinite(value) && value > 0 ? Math.min(value, now + maxSeconds * 1000) : 0
}

export const remainingSeconds = (until: number, now = Date.now()) => Math.max(0, Math.ceil((until - now) / 1000))
