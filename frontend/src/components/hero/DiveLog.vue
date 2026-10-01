<script setup lang="ts">
import { computed } from 'vue'
import { diveLogView } from '@scat/shared'
import { useScan } from '@/composables/useScan'
import AppIcon from '@/components/ui/AppIcon.vue'

/** The hero's "logbook": one calm line of narrative plus the real numbers behind it. */
const props = defineProps<{ revealed: number }>()

const scan = useScan()
const view = computed(() => diveLogView(scan.state.value, props.revealed))
const retry = () => (view.value.retry === 'reload' ? scan.retry() : scan.startSearch())
</script>

<template>
  <div class="log" :class="`log--${view.tone}`" role="status" aria-live="polite">
    <div class="log__head">
      <span class="log__dot" aria-hidden="true"></span>
      <Transition name="log" mode="out-in">
        <p :key="view.title" class="log__title">{{ view.title }}</p>
      </Transition>
    </div>
    <p class="log__detail mono">{{ view.detail }}</p>
    <div v-if="view.progress !== undefined" class="log__track" aria-hidden="true">
      <span class="log__fill" :style="{ transform: `scaleX(${Math.max(0.02, view.progress)})` }"></span>
    </div>
    <button v-if="view.retry" class="btn btn--sm log__retry" type="button" @click="retry()">
      <AppIcon name="refresh" /> Попробовать снова
    </button>
  </div>
</template>

<style scoped>
.log {
  display: grid;
  gap: 6px;
  max-width: 440px;
}

.log__head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.log__dot {
  width: 7px;
  height: 7px;
  flex: none;
  border-radius: 50%;
  background: var(--text-3);
}

.log--live .log__dot {
  background: var(--aqua);
  box-shadow: 0 0 0 0 rgba(143, 214, 207, 0.5);
  animation: sonar 2.4s var(--ease-out) infinite;
}

.log--done .log__dot {
  background: var(--pearl);
  box-shadow: 0 0 12px var(--pearl-glow);
}

.log--error .log__dot {
  background: var(--coral);
}

.log__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
}

.log__detail {
  padding-left: 17px;
  font-size: 12.5px;
  color: var(--text-3);
}

.log--error .log__detail {
  color: var(--text-2);
  font-family: var(--font-ui);
  font-size: 14px;
}

.log__track {
  position: relative;
  height: 2px;
  margin: 8px 0 0 17px;
  overflow: hidden;
  border-radius: 2px;
  background: var(--line);
}

.log__fill {
  position: absolute;
  inset: 0;
  transform-origin: left;
  background: linear-gradient(90deg, rgba(143, 214, 207, 0.2), var(--aqua));
  transition: transform 0.9s var(--ease-out);
}

.log__retry {
  justify-self: start;
  margin: 8px 0 0 17px;
}

.log-enter-active,
.log-leave-active {
  transition: opacity 0.3s var(--ease-out), transform 0.4s var(--ease-out);
}

.log-enter-from {
  opacity: 0;
  transform: translateY(6px);
}

.log-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

@keyframes sonar {
  70% {
    box-shadow: 0 0 0 9px rgba(143, 214, 207, 0);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(143, 214, 207, 0);
  }
}
</style>
