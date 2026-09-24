import { computed, readonly, ref, shallowRef } from 'vue'
import { ApiError, clearCache, fetchResults, fetchStatus, requestScan } from '@/services/api'
import type { PearlNode, ScanStatus } from '@/types/api'

/**
 * - `resting`   nothing is happening; results (if any) are on the page
 * - `diving`    the user started a search, or the server is scanning right now
 * - `surfaced`  a dive just finished; results are ready to look at
 */
export type Journey = 'resting' | 'diving' | 'surfaced'
/** What the dive shows: a live server scan, or a replay of today's cached catch. */
export type DiveKind = 'live' | 'replay'

const POLL_MS = 1500
const MAX_POLL_FAILURES = 4

// Module-level state: one scan session shared by every component.
const loading = ref(true)
const nodes = shallowRef<PearlNode[]>([])
const status = shallowRef<ScanStatus | null>(null)
const loadError = shallowRef<ApiError | null>(null)
const scanError = shallowRef<ApiError | null>(null)
const cooldown = ref(false)
const journey = ref<Journey>('resting')
const diveKind = ref<DiveKind>('replay')
/** Working configs found so far in this dive. The ocean scene turns increments into pearls. */
const found = ref(0)

let pollTimer: ReturnType<typeof setTimeout> | undefined
let pollFailures = 0
let controller: AbortController | null = null

function logError(context: string, err: unknown) {
  // User-facing copy stays friendly; developers get the details here.
  console.error(`[Скат] ${context}`, err)
}

function asApiError(err: unknown): ApiError {
  return err instanceof ApiError ? err : new ApiError('malformed', String(err))
}

function stopPolling() {
  clearTimeout(pollTimer)
  pollTimer = undefined
}

function schedulePoll() {
  stopPolling()
  pollTimer = setTimeout(poll, POLL_MS)
}

async function poll() {
  if (document.hidden) return // resumed by the visibilitychange listener
  try {
    const next = await fetchStatus(controller?.signal)
    pollFailures = 0
    status.value = next
    if (next.is_scanning) {
      found.value = Math.max(found.value, next.progress?.working_candidates ?? 0)
      schedulePoll()
    } else {
      await finishLiveDive(next)
    }
  } catch (err) {
    const apiErr = asApiError(err)
    if (apiErr.kind === 'aborted') return
    pollFailures += 1
    logError(`status poll failed (${pollFailures}/${MAX_POLL_FAILURES})`, apiErr)
    if (pollFailures >= MAX_POLL_FAILURES) {
      scanError.value = apiErr
      journey.value = nodes.value.length ? 'surfaced' : 'resting'
    } else {
      schedulePoll()
    }
  }
}

async function finishLiveDive(last: ScanStatus) {
  stopPolling()
  await refreshResults()
  if (last.last_scan_failed && !last.cache) {
    scanError.value = new ApiError('server', 'Scan failed on the server')
    journey.value = 'resting'
    return
  }
  // `found` counts every working config; the catch keeps the best of them.
  found.value = Math.max(found.value, nodes.value.length)
  journey.value = 'surfaced'
}

async function refreshResults() {
  try {
    const data = await fetchResults(controller?.signal)
    nodes.value = data.nodes
    status.value = data
    loadError.value = null
  } catch (err) {
    const apiErr = asApiError(err)
    if (apiErr.kind === 'aborted') return
    logError('loading results failed', apiErr)
    loadError.value = apiErr
  }
}

function beginLiveDive(current: ScanStatus) {
  diveKind.value = 'live'
  journey.value = 'diving'
  found.value = current.progress?.working_candidates ?? 0
  schedulePoll()
}

async function init() {
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  await refreshResults()
  loading.value = false
  if (status.value?.is_scanning) beginLiveDive(status.value)
}

/** The main call to action. Never triggers more than the server allows. */
async function startSearch() {
  if (journey.value === 'diving') return
  scanError.value = null
  cooldown.value = false
  found.value = 0
  journey.value = 'diving'

  try {
    const res = await requestScan(controller?.signal)
    status.value = res
    if (res.outcome === 'started' || res.outcome === 'joined') {
      beginLiveDive(res)
      return
    }
    if (res.outcome === 'cooldown') cooldown.value = true
    if (!nodes.value.length) await refreshResults()
    if (!nodes.value.length) {
      journey.value = 'resting'
      if (res.outcome === 'cooldown') scanError.value = new ApiError('server', 'Scanner cooling down')
      return
    }
    // Today's catch already exists: replay the dive, the scene reveals each pearl.
    diveKind.value = 'replay'
    found.value = nodes.value.length
  } catch (err) {
    const apiErr = asApiError(err)
    if (apiErr.kind === 'aborted') return
    logError('starting a search failed', apiErr)
    scanError.value = apiErr
    journey.value = nodes.value.length ? 'surfaced' : 'resting'
  }
}

/** Clears server cache and triggers a fresh scan. */
async function resetCache() {
  if (journey.value === 'diving') return
  scanError.value = null
  cooldown.value = false
  found.value = 0
  journey.value = 'diving'

  try {
    const res = await clearCache(controller?.signal)
    status.value = res
    if (res.outcome === 'started' || res.outcome === 'joined') {
      beginLiveDive(res)
      return
    }
    if (!nodes.value.length) await refreshResults()
    if (!nodes.value.length) {
      journey.value = 'resting'
      return
    }
    diveKind.value = 'replay'
    found.value = nodes.value.length
  } catch (err) {
    const apiErr = asApiError(err)
    if (apiErr.kind === 'aborted') return
    logError('resetting cache failed', apiErr)
    scanError.value = apiErr
    journey.value = nodes.value.length ? 'surfaced' : 'resting'
  }
}

/** Called by the scene once the replay has shown every pearl. */
function completeReplay() {
  if (journey.value === 'diving' && diveKind.value === 'replay') journey.value = 'surfaced'
}

function onVisibilityChange() {
  // poll() skips hidden tabs; pick the live dive back up as soon as the tab is visible.
  if (!document.hidden && journey.value === 'diving' && diveKind.value === 'live') void poll()
}

let mounted = 0

export function useScan() {
  return {
    loading: readonly(loading),
    nodes,
    status,
    loadError,
    scanError,
    cooldown: readonly(cooldown),
    journey: readonly(journey),
    diveKind: readonly(diveKind),
    found: readonly(found),
    progress: computed(() => status.value?.progress ?? null),
    cache: computed(() => status.value?.cache ?? null),
    init,
    startSearch,
    resetCache,
    completeReplay,
    retry: init,
    /** Wire lifecycle once from the root component. */
    attach() {
      if (mounted++ === 0) {
        document.addEventListener('visibilitychange', onVisibilityChange)
        void init()
      }
    },
    detach() {
      if (--mounted === 0) {
        document.removeEventListener('visibilitychange', onVisibilityChange)
        stopPolling()
        controller?.abort()
      }
    },
  }
}
