<script setup lang="ts">
import { computed } from 'vue'
import { useClipboard } from '@/composables/useClipboard'

const props = withDefaults(
  defineProps<{ text: string; label?: string; copiedLabel?: string; variant?: 'primary' | 'ghost'; small?: boolean }>(),
  { label: 'Копировать', copiedLabel: 'Скопировано', variant: 'ghost', small: false },
)

const { state, copy } = useClipboard()
const current = computed(() =>
  state.value === 'copied' ? props.copiedLabel : state.value === 'failed' ? 'Не удалось' : props.label,
)
</script>

<template>
  <button
    type="button"
    class="btn copy"
    :class="[{ 'btn--primary': variant === 'primary', 'btn--sm': small }, `is-${state}`]"
    @click.stop="copy(text)"
  >
    <svg class="icon copy__icon" viewBox="0 0 24 24" aria-hidden="true">
      <path class="copy__sheets" d="M9 9h10v10H9z M5 15V5h10" />
      <path class="copy__tick" d="M5 12.5l4.2 4.2L19 7" pathLength="1" />
    </svg>
    <span class="copy__label">
      <Transition name="swap" mode="out-in">
        <span :key="current">{{ current }}</span>
      </Transition>
    </span>
    <span class="visually-hidden" aria-live="polite">{{ state === 'copied' ? copiedLabel : '' }}</span>
  </button>
</template>

<style scoped>
.copy {
  min-width: 0;
}

.copy__label {
  display: inline-grid;
}

.copy__sheets {
  transition: opacity 0.2s var(--ease-out), transform 0.3s var(--ease-out);
  transform-origin: center;
}

.copy__tick {
  stroke-dasharray: 1;
  stroke-dashoffset: 1;
  transition: stroke-dashoffset 0.35s var(--ease-out) 0.05s;
}

.is-copied .copy__sheets {
  opacity: 0;
  transform: scale(0.7);
}

.is-copied .copy__tick {
  stroke-dashoffset: 0;
}

.is-copied:not(.btn--primary) {
  --btn-border: rgba(243, 233, 216, 0.45);
  color: var(--pearl);
}

.is-copied.btn--primary {
  --btn-bg: var(--pearl);
}

.is-failed {
  --btn-border: rgba(236, 154, 134, 0.5);
  color: var(--coral);
}

.swap-enter-active,
.swap-leave-active {
  transition: opacity 0.16s var(--ease-out), transform 0.2s var(--ease-out);
}

.swap-enter-from {
  opacity: 0;
  transform: translateY(6px);
}

.swap-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}
</style>
