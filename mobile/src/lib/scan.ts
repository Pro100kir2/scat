import { useEffect, useSyncExternalStore } from 'react'
import { AppState } from 'react-native'
import { createScanStore, type ScanState } from '@scat/shared'
import { api } from './api'
import { describeFailure } from './diagnostics'

/**
 * The one scan session of the app: the shared state machine (@scat/shared), paused while
 * the app is in the background. Screens read it with useScan(); no screen has its own copy
 * of the rules.
 */
export const scanStore = createScanStore({
  api,
  isHidden: () => AppState.currentState !== 'active',
  log: (context, err) => {
    if (__DEV__) console.warn(describeFailure(context, err))
  },
})

export function useScan(): ScanState {
  return useSyncExternalStore(scanStore.subscribe, scanStore.getState, scanStore.getState)
}

/** Mount once (root layout): load results, and pick a live scan back up when the app returns. */
export function useScanLifecycle() {
  useEffect(() => {
    void scanStore.init()
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active') scanStore.resume()
    })
    return () => {
      sub.remove()
      scanStore.stop()
    }
  }, [])
}
