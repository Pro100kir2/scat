<script setup lang="ts">
import SkatMark from '@/components/brand/SkatMark.vue'

/** Empty and error states: the manta resting over an empty patch of sand. */
defineProps<{ title: string; text: string; tone?: 'calm' | 'error' | 'searching' }>()
</script>

<template>
  <div class="note" :class="`note--${tone ?? 'calm'}`">
    <div class="note__scene" aria-hidden="true">
      <SkatMark :size="64" class="note__manta" />
      <span class="note__sand"></span>
    </div>
    <h3 class="note__title">{{ title }}</h3>
    <p class="note__text">{{ text }}</p>
    <div class="note__actions"><slot /></div>
  </div>
</template>

<style scoped>
.note {
  display: grid;
  justify-items: center;
  gap: 10px;
  padding: 56px 24px 60px;
  border: 1px dashed var(--line-strong);
  border-radius: var(--radius);
  text-align: center;
}

.note__scene {
  position: relative;
  display: grid;
  justify-items: center;
  width: 180px;
  height: 96px;
  margin-bottom: 8px;
}

.note__manta {
  color: var(--text-2);
  animation: hover 5s var(--ease-in-out) infinite;
}

.note--searching .note__manta {
  color: var(--aqua);
  animation-duration: 2.6s;
}

.note--error .note__manta {
  color: var(--text-3);
  animation: none;
  transform: translateY(14px) rotate(-8deg);
}

.note__sand {
  position: absolute;
  bottom: 0;
  width: 100%;
  height: 10px;
  border-radius: 50%;
  background: radial-gradient(closest-side, rgba(168, 208, 222, 0.16), transparent);
}

.note__title {
  font-family: var(--font-display);
  font-size: 28px;
  line-height: 1.1;
}

.note__text {
  max-width: 420px;
  color: var(--text-2);
}

.note__actions {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}

.note__actions:empty {
  display: none;
}

@keyframes hover {
  0%,
  100% {
    transform: translateY(0) rotate(-2deg);
  }
  50% {
    transform: translateY(-8px) rotate(2deg);
  }
}
</style>
