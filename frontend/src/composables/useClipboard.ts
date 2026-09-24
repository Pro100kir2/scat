import { onBeforeUnmount, ref } from 'vue'

export type CopyState = 'idle' | 'copied' | 'failed'

async function writeText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    await navigator.clipboard.writeText(text)
    return
  }
  // Older browsers and plain-http previews.
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none'
  document.body.appendChild(area)
  area.select()
  const ok = document.execCommand('copy')
  area.remove()
  if (!ok) throw new Error('execCommand copy failed')
}

/** Copy with a short-lived confirmation state for the button that triggered it. */
export function useClipboard(resetMs = 1800) {
  const state = ref<CopyState>('idle')
  let timer: ReturnType<typeof setTimeout> | undefined

  async function copy(text: string) {
    clearTimeout(timer)
    try {
      await writeText(text)
      state.value = 'copied'
    } catch (err) {
      console.error('[Скат] copy failed', err)
      state.value = 'failed'
    }
    timer = setTimeout(() => (state.value = 'idle'), resetMs)
  }

  onBeforeUnmount(() => clearTimeout(timer))
  return { state, copy }
}
