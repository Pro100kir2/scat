import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { useCooldown } from '@/composables/useCooldown'
import { formatCountdown } from '@scat/shared'

const KEY = 'test:cooldown'

function mountCooldown(max = 600) {
  let api!: ReturnType<typeof useCooldown>
  const wrapper = mount(defineComponent({
    setup() {
      api = useCooldown(KEY, max)
      return () => h('div')
    },
  }))
  return { wrapper, api }
}

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
})
afterEach(() => vi.useRealTimers())

describe('useCooldown', () => {
  it('counts down and persists the end time across reloads', async () => {
    const first = mountCooldown()
    first.api.start(600)
    expect(first.api.remaining.value).toBe(600)
    vi.advanceTimersByTime(5_000)
    expect(first.api.remaining.value).toBe(595)
    first.wrapper.unmount()

    const reloaded = mountCooldown()
    await nextTick()
    expect(reloaded.api.remaining.value).toBe(595)
    vi.advanceTimersByTime(595_000)
    expect(reloaded.api.remaining.value).toBe(0)
    expect(localStorage.getItem(KEY)).toBeNull()
  })

  it('caps stored values beyond the maximum', async () => {
    localStorage.setItem(KEY, String(Date.now() + 10 * 86_400_000))
    const { api } = mountCooldown(600)
    await nextTick()
    expect(api.remaining.value).toBe(600)
  })

  it('ignores garbage in storage', () => {
    localStorage.setItem(KEY, 'nope')
    expect(mountCooldown().api.remaining.value).toBe(0)
  })
})

describe('formatCountdown', () => {
  it('formats minutes and seconds', () => {
    expect(formatCountdown(600)).toBe('10:00')
    expect(formatCountdown(65)).toBe('1:05')
    expect(formatCountdown(0)).toBe('0:00')
  })
})
