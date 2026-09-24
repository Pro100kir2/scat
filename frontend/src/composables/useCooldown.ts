import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { storage } from '@/utils/storage'

/**
 * A countdown that survives page reloads: the end time is kept in localStorage.
 * `maxSeconds` caps whatever is stored, so a stale or edited value can't lock the button for long.
 */
export function useCooldown(key: string, maxSeconds: number) {
  const readUntil = () => {
    const stored = Number(storage.get(key))
    return Number.isFinite(stored) ? Math.min(stored, Date.now() + maxSeconds * 1000) : 0
  }

  const until = ref(readUntil())
  const now = ref(Date.now())
  const remaining = computed(() => Math.max(0, Math.ceil((until.value - now.value) / 1000)))
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

  onMounted(tick)
  onBeforeUnmount(() => clearTimeout(timer))

  return { remaining, start }
}
