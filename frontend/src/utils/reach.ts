import type { Reachability, ReachState } from '@/types/api'

/**
 * How each reachability state looks and reads. Tones map to the ocean palette:
 * reachable = aqua light, blocked = coral, checking = sand, offline = dark water,
 * none = an empty ring. Every state has its own words, so colour is never the only cue.
 */
export type ReachTone = 'reachable' | 'blocked' | 'checking' | 'offline' | 'none'

export const REACH_STATE: Record<ReachState, { label: string; hint: string; tone: ReachTone }> = {
  SUCCESS: { label: 'Доступен', hint: 'Зонд в сети провайдера подключился к серверу', tone: 'reachable' },
  FAILED: { label: 'Недоступен', hint: 'Соединение отклонено или сброшено в сети провайдера', tone: 'blocked' },
  TIMEOUT: { label: 'Нет ответа', hint: 'Сервер не ответил зонду вовремя: похоже на блокировку', tone: 'blocked' },
  CHECKING: { label: 'Проверяется', hint: 'Зонд получил задание и сейчас проверяет сервер', tone: 'checking' },
  NO_PROBE: { label: 'Нет зонда', hint: 'У провайдера сейчас нет активного зонда', tone: 'none' },
  OFFLINE: { label: 'Зонд офлайн', hint: 'Зонды провайдера сейчас не на связи', tone: 'offline' },
  NOT_TESTED: { label: 'Не проверялся', hint: 'Этот сервер ещё не проверяли из сети провайдера', tone: 'none' },
}

/** "Доступна в 3 из 4 сетей": only providers with a real verdict count. */
export function reachSummary(rows: Reachability[]): { reachable: number; tested: number } {
  const tested = rows.filter((r) => ['SUCCESS', 'FAILED', 'TIMEOUT'].includes(r.state))
  return { reachable: tested.filter((r) => r.state === 'SUCCESS').length, tested: tested.length }
}

export const NETWORKS = ['сети', 'сетей', 'сетей'] as const
