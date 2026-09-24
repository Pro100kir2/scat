<script setup lang="ts">
import { computed } from 'vue'
import { useScan } from '@/composables/useScan'
import { formatNumber } from '@/utils/format'

const scan = useScan()
const total = computed(() => scan.cache.value?.total_nodes)

const steps = computed(() => [
  {
    title: 'Погружение',
    text: total.value
      ? `Раз в сутки Скат собирает открытые списки конфигураций — в последний раз ${formatNumber(total.value)} уникальных узлов.`
      : 'Раз в сутки Скат собирает открытые списки конфигураций и отбрасывает дубликаты.',
  },
  {
    title: 'Проверка',
    text: 'Узлы проверяются настоящим подключением через Xray, пока не найдётся 25 рабочих: с проверенным TLS, выходом не через наш адрес, доступом к двум разным сайтам и реальной загрузкой данных.',
  },
  {
    title: 'Отбор',
    text: 'Каждого из 25 кандидатов замеряют пять раз и скачивают через него 2 МБ. Пять лучших по задержке, стабильности и скорости — это и есть жемчужины.',
  },
])
</script>

<template>
  <section id="how" class="how container" aria-labelledby="how-title">
    <p class="eyebrow">Как это работает</p>
    <h2 id="how-title" class="section-title">Три движения <em>плавника</em></h2>

    <ol class="how__steps">
      <li v-for="(step, i) in steps" :key="step.title" class="how__step">
        <span class="how__num mono">0{{ i + 1 }}</span>
        <h3 class="how__title">{{ step.title }}</h3>
        <p class="how__text">{{ step.text }}</p>
      </li>
    </ol>

    <div class="how__notes">
      <p>
        <strong>Один поиск на всех.</strong> Результаты хранятся сутки и общие для всех посетителей,
        поэтому страница открывается мгновенно, а источники не перегружаются.
      </p>
      <p>
        <strong>Честно о рисках.</strong> Это публичные серверы: Скат не владеет ими и не может гарантировать
        их работу или конфиденциальность. Не передавайте через них ничего важного.
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
