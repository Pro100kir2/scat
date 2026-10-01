import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { cappedUntil, remainingSeconds } from '@scat/shared'
import { storage } from '@/utils/storage'

/**
 * A countdown that survives page reloads: the end time is kept in localStorage.
 * `maxSeconds` caps whatever is stored, so a stale or edited value can't lock the button for long.
 */
export function useCooldown(key: string, maxSeconds: number) {
  const readUntil = () => cappedUntil(storage.get(key), maxSeconds)

  // Read on mount, not during setup: the prerendered page must match the first client render.
  const until = ref(0)
  const now = ref(Date.now())
  const remaining = computed(() => remainingSeconds(until.value, now.value))
  let timer: ReturnType<typeof setTimeout> | undefined

  function tick() {
    clearTimeout(timer)
    now.value = Date.now()
    if (remaining.value > 0) timer = setTimeout(tick, (until.value - now.value) % 1000 || 1000)
    else storage.remove(key)
  }

  function start(seconds: number) {
    until.value = Date.now() + Math.min(seconds, maxSeconds) * 1000
    storage.set(key, String(until.value))
    tick()
  }

  onMounted(() => {
    until.value = readUntil()
    tick()
  })
  onBeforeUnmount(() => clearTimeout(timer))

  return { remaining, start }
}
