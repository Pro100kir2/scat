import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import CopyButton from '@/components/ui/CopyButton.vue'

describe('CopyButton', () => {
  it('copies and confirms in place, then resets', async () => {
    vi.useFakeTimers()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true })

    const wrapper = mount(CopyButton, { props: { text: 'vless://abc' } })
    await wrapper.trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalledWith('vless://abc')
    expect(wrapper.classes()).toContain('is-copied')
    expect(wrapper.text()).toContain('Скопировано')

    vi.advanceTimersByTime(2000)
    await flushPromises()
    expect(wrapper.classes()).toContain('is-idle')
    vi.useRealTimers()
  })
})
