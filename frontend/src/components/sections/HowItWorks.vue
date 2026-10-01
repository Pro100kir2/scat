<script setup lang="ts">
import { computed } from 'vue'
import { howItWorks } from '@scat/shared'
import { useScan } from '@/composables/useScan'

const scan = useScan()
const how = computed(() => howItWorks(scan.cache.value?.total_nodes))
</script>

<template>
  <section id="how" class="how container" aria-labelledby="how-title">
    <p class="eyebrow">Как это работает</p>
    <h2 id="how-title" class="section-title">Три движения <em>плавника</em></h2>

    <ol class="how__steps">
      <li v-for="(step, i) in how.steps" :key="step.title" class="how__step">
        <span class="how__num mono">0{{ i + 1 }}</span>
        <h3 class="how__title">{{ step.title }}</h3>
        <p class="how__text">{{ step.text }}</p>
      </li>
    </ol>

    <div class="how__notes">
      <p v-for="note in how.notes" :key="note.lead">
        <strong>{{ note.lead }}</strong> {{ note.text }}
      </p>
    </div>
  </section>
</template>

<style scoped>
.how {
  padding-bottom: clamp(72px, 10vw, 128px);
}

.how .eyebrow {
  margin-bottom: 10px;
}

.how__steps {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: clamp(20px, 3vw, 40px);
  margin: 40px 0 0;
  padding: 0;
  list-style: none;
  counter-reset: none;
}

.how__step {
  position: relative;
  padding-top: 22px;
  border-top: 1px solid var(--line-strong);
}

.how__step::before {
  content: '';
  position: absolute;
  top: -4px;
  left: 0;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--pearl);
  box-shadow: 0 0 12px var(--pearl-glow);
}

.how__num {
  color: var(--text-3);
  font-size: 12px;
}

.how__title {
  margin-top: 10px;
  font-family: var(--font-display);
  font-size: 28px;
}

.how__text {
  margin-top: 8px;
  color: var(--text-2);
}

.how__notes {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px 40px;
  margin-top: 56px;
  color: var(--text-3);
  font-size: 14.5px;
}

.how__notes strong {
  color: var(--text-2);
  font-weight: 600;
}

@media (max-width: 760px) {
  .how__steps,
  .how__notes {
    grid-template-columns: 1fr;
  }

  .how__steps {
    gap: 28px;
  }
}
</style>
