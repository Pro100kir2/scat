/**
 * The scan session: one state machine for "what is Скат doing and what do we show".
 * Framework-independent: the Vue site wraps it in refs, the mobile app in a React hook.
 *
 * - `resting`   nothing is happening; results (if any) are on the page
 * - `diving`    the user started a search, or the server is scanning right now
 * - `surfaced`  a dive just finished; results are ready to look at
 */
import { ApiError, asApiError, type Api } from './api'
import type { PearlNode, ScanProgress, CacheMeta, ScanRequestResponse, ScanStatus } from './types'

export type Journey = 'resting' | 'diving' | 'surfaced'
/** What the dive shows: a live server scan, or a replay of today's cached catch. */
export type DiveKind = 'live' | 'replay'

export interface ScanState {
  loading: boolean
  nodes: PearlNode[]
  status: ScanStatus | null
  loadError: ApiError | null
  scanError: ApiError | null
  cooldown: boolean
  journey: Journey
  diveKind: DiveKind
  /** Working configs found so far in this dive. The ocean scene turns increments into pearls. */
  found: number
}

export interface ScanDeps {
  api: Pick<Api, 'fetchResults' | 'fetchStatus' | 'requestScan' | 'requestRefresh'>
  /** true while the page/app is in the background: polling pauses until resume(). */
  isHidden?: () => boolean
  /** Developers get the details here; user-facing copy stays friendly. */
  log?: (context: string, err: unknown) => void
  pollMs?: number
}

const MAX_POLL_FAILURES = 4

const INITIAL: ScanState = {
  loading: true,
  nodes: [],
  status: null,
  loadError: null,
  scanError: null,
  cooldown: false,
  journey: 'resting',
  diveKind: 'replay',
  found: 0,
}

export const scanProgress = (state: ScanState): ScanProgress | null => state.status?.progress ?? null
export const scanCache = (state: ScanState): CacheMeta | null => state.status?.cache ?? null

export function createScanStore(deps: ScanDeps) {
  const { api } = deps
  const pollMs = deps.pollMs ?? 1500
  const log = deps.log ?? ((context, err) => console.error(`[Скат] ${context}`, err))
  const hidden = () => deps.isHidden?.() ?? false

  let state: ScanState = INITIAL
  const listeners = new Set<() => void>()
  let pollTimer: ReturnType<typeof setTimeout> | undefined
  let pollFailures = 0
  let controller: AbortController | null = null

  function set(patch: Partial<ScanState>) {
    state = { ...state, ...patch }
    listeners.forEach((fn) => fn())
  }

  function stopPolling() {
    clearTimeout(pollTimer)
    pollTimer = undefined
  }

  function schedulePoll() {
    stopPolling()
    pollTimer = setTimeout(poll, pollMs)
  }

  async function poll() {
    if (hidden()) return // picked back up by resume()
    try {
      const next = await api.fetchStatus(controller?.signal)
      pollFailures = 0
      if (next.is_scanning) {
        set({ status: next, found: Math.max(state.found, next.progress?.working_candidates ?? 0) })
        schedulePoll()
      } else {
        set({ status: next })
        await finishLiveDive(next)
      }
    } catch (err) {
      const apiErr = asApiError(err)
      if (apiErr.kind === 'aborted') return
      pollFailures += 1
      log(`status poll failed (${pollFailures}/${MAX_POLL_FAILURES})`, apiErr)
      if (pollFailures >= MAX_POLL_FAILURES) {
        set({ scanError: apiErr, journey: state.nodes.length ? 'surfaced' : 'resting' })
      } else {
        schedulePoll()
      }
    }
  }

  async function finishLiveDive(last: ScanStatus) {
    stopPolling()
    await refreshResults()
    if (last.last_scan_failed && !last.cache) {
      set({ scanError: new ApiError('server', 'Scan failed on the server'), journey: 'resting' })
      return
    }
    // `found` counts every working config; the catch keeps the best of them.
    set({ found: Math.max(state.found, state.nodes.length), journey: 'surfaced' })
  }

  async function refreshResults() {
    try {
      const data = await api.fetchResults(controller?.signal)
      set({ nodes: data.nodes, status: data, loadError: null })
    } catch (err) {
      const apiErr = asApiError(err)
      if (apiErr.kind === 'aborted') return
      log('loading results failed', apiErr)
      set({ loadError: apiErr })
    }
  }

  function beginLiveDive(current: ScanStatus) {
    pollFailures = 0
    set({ diveKind: 'live', journey: 'diving', found: current.progress?.working_candidates ?? 0 })
    schedulePoll()
  }

  async function init() {
    controller?.abort()
    controller = new AbortController()
    stopPolling()
    set({ loading: true })
    await refreshResults()
    set({ loading: false })
    if (state.status?.is_scanning) beginLiveDive(state.status)
  }

  /**
   * Asks the server for a dive and plays it: live when a scan runs, a replay of the
   * cached catch otherwise. Request errors reset the journey and are rethrown.
   */
  async function dive<T extends ScanRequestResponse>(send: (signal?: AbortSignal) => Promise<T>): Promise<T | null> {
    if (state.journey === 'diving') return null
    set({ scanError: null, cooldown: false, found: 0, journey: 'diving' })

    let res: T
    try {
      res = await send(controller?.signal)
    } catch (err) {
      const apiErr = asApiError(err)
      if (apiErr.kind !== 'aborted') set({ journey: state.nodes.length ? 'surfaced' : 'resting' })
      throw apiErr
    }

    set({ status: res })
    if (res.outcome === 'started' || res.outcome === 'joined') {
      beginLiveDive(res)
      return res
    }
    if (res.outcome === 'cooldown') set({ cooldown: true })
    if (!state.nodes.length) await refreshResults()
    if (!state.nodes.length) {
      set({
        journey: 'resting',
        scanError: res.outcome === 'cooldown' ? new ApiError('server', 'Scanner cooling down') : state.scanError,
      })
      return res
    }
    // Today's catch already exists: replay the dive, the scene reveals each pearl.
    set({ diveKind: 'replay', found: state.nodes.length })
    return res
  }

  function reportScanError(err: unknown) {
    const apiErr = asApiError(err)
    if (apiErr.kind === 'aborted') return
    log('starting a search failed', apiErr)
    set({ scanError: apiErr })
  }

  return {
    getState: () => state,
    subscribe(listener: () => void) {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    init,
    retry: init,

    /** The main call to action. Never triggers more than the server allows. */
    async startSearch() {
      try {
        await dive((signal) => api.requestScan(signal))
      } catch (err) {
        reportScanError(err)
      }
    },

    /**
     * Drops today's cached catch and dives for a fresh one.
     * Resolves to the seconds until the next refresh is allowed (0 when it can be retried now).
     */
    async refreshCatch(): Promise<number> {
      try {
        const res = await dive((signal) => api.requestRefresh(signal))
        return res?.retry_after ?? 0
      } catch (err) {
        const apiErr = asApiError(err)
        // Someone refreshed recently: the button's countdown explains it, no error needed.
        if (apiErr.kind === 'rate_limit') return apiErr.retryAfter ?? 60
        reportScanError(apiErr)
        return 0
      }
    },

    /** Called by the scene once the replay has shown every pearl. */
    completeReplay() {
      if (state.journey === 'diving' && state.diveKind === 'replay') set({ journey: 'surfaced' })
    },

    /** The page/app is visible again: pick a live dive back up at once. */
    resume() {
      if (!hidden() && state.journey === 'diving' && state.diveKind === 'live') void poll()
    },

    /** Forget everything and stop: back to the state before init(). */
    reset() {
      stopPolling()
      controller?.abort()
      controller = null
      pollFailures = 0
      set(INITIAL)
    },

    /** Stop timers and abort requests in flight (the host is going away). */
    stop() {
      stopPolling()
      controller?.abort()
    },
  }
}

export type ScanStore = ReturnType<typeof createScanStore>
