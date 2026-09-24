import { ref } from 'vue'

/** Rank of the result card to draw attention to (a pearl clicked in the scene). */
const spotlight = ref<number | null>(null)
let timer: ReturnType<typeof setTimeout> | undefined

export function useSpotlight() {
  function focusRank(rank: number) {
    spotlight.value = rank
    clearTimeout(timer)
    timer = setTimeout(() => (spotlight.value = null), 2600)
  }
  return { spotlight, focusRank }
}
