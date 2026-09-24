<template>
  <div class="skeleton" aria-hidden="true">
    <div v-for="i in 5" :key="i" class="skeleton__row" :style="{ animationDelay: `${i * 90}ms` }">
      <span class="dot"></span>
      <span class="bar" style="width: 28%"></span>
      <span class="bar" style="width: 14%"></span>
      <span class="bar" style="width: 12%; margin-left: auto"></span>
    </div>
  </div>
</template>

<style scoped>
.skeleton {
  display: grid;
  gap: 12px;
}

.skeleton__row {
  display: flex;
  align-items: center;
  gap: 24px;
  height: 76px;
  padding: 0 24px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  animation: shimmer 1.8s var(--ease-in-out) infinite;
}

.dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--line-strong);
}

.bar {
  height: 10px;
  border-radius: 5px;
  background: var(--line-strong);
}

@keyframes shimmer {
  50% {
    opacity: 0.45;
  }
}
</style>
