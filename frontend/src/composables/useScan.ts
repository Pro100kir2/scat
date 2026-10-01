import { computed, readonly, ref, shallowRef } from 'vue'
import { createScanStore, scanCache, scanProgress, type DiveKind, type Journey, type ScanState } from '@scat/shared'
import { fetchResults, fetchStatus, requestRefresh, requestScan } from '@/services/api'

export type { DiveKind, Journey }

/**
 * The scan session for Vue: the shared state machine (@scat/shared `createScanStore`) mirrored
 * into refs. One session for every component; the rules live in the shared package.
 */
const store = createScanStore({
  // Looked up on every call, so tests can replace the API module.
  api: {
    fetchResults: (signal) => fetchResults(signal),
    fetchStatus: (signal) => fetchStatus(signal),
    requestScan: (signal) => requestScan(signal),
    requestRefresh: (signal) => requestRefresh(signal),
  },
  isHidden: () => typeof document !== 'undefined' && document.hidden,
})

const initial = store.getState()
const loading = ref(initial.loading)
const nodes = shallowRef(initial.nodes)
const status = shallowRef(initial.status)
const loadError = shallowRef(initial.loadError)
const scanError = shallowRef(initial.scanError)
const cooldown = ref(initial.cooldown)
const journey = ref<Journey>(initial.journey)
const diveKind = ref<DiveKind>(initial.diveKind)
const found = ref(initial.found)
/** The whole state as one object, for the shared view functions (diveLogView, resultsPhase). */
const state = shallowRef<ScanState>(initial)

store.subscribe(() => {
  const s = store.getState()
  state.value = s
  loading.value = s.loading
  nodes.value = s.nodes
  status.value = s.status
  loadError.value = s.loadError
  scanError.value = s.scanError
  cooldown.value = s.cooldown
  journey.value = s.journey
  diveKind.value = s.diveKind
  found.value = s.found
})

const onVisibilityChange = () => store.resume()

let mounted = 0

export function useScan() {
  return {
    /** Immutable snapshots: a new object on every change. */
    state,
    loading: readonly(loading),
    nodes,
    status,
    loadError,
    scanError,
    cooldown: readonly(cooldown),
    journey: readonly(journey),
    diveKind: readonly(diveKind),
    found: readonly(found),
    progress: computed(() => scanProgress(state.value)),
    cache: computed(() => scanCache(state.value)),
    init: store.init,
    startSearch: store.startSearch,
    refreshCatch: store.refreshCatch,
    completeReplay: store.completeReplay,
    retry: store.retry,
    /** Wire lifecycle once from the root component. */
    attach() {
      if (mounted++ === 0) {
        document.addEventListener('visibilitychange', onVisibilityChange)
        void store.init()
      }
    },
    detach() {
      if (--mounted === 0) {
        document.removeEventListener('visibilitychange', onVisibilityChange)
        store.stop()
      }
    },
  }
}
