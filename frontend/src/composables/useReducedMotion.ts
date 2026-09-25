import { onBeforeUnmount, ref } from 'vue'

export function useReducedMotion() {
  // Build-time prerendering has no window; the browser picks the real value up on hydration.
  if (typeof window === 'undefined') return ref(false)
  const query = window.matchMedia('(prefers-reduced-motion: reduce)')
  const reduced = ref(query.matches)
  const onChange = (e: MediaQueryListEvent) => (reduced.value = e.matches)
  query.addEventListener('change', onChange)
  onBeforeUnmount(() => query.removeEventListener('change', onChange))
  return reduced
}
