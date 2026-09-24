<script setup lang="ts">
import { computed } from 'vue'
import { useScan } from '@/composables/useScan'
import { formatMoment, formatNumber, formatUntil, plural, PEARLS_NOM } from '@/utils/format'
import AppIcon from '@/components/ui/AppIcon.vue'

/** The hero's "logbook": one calm line of narrative plus the real numbers behind it. */
const props = defineProps<{ revealed: number }>()

const scan = useScan()

const STAGES: Record<string, { title: string; detail: (p: NonNullable<typeof scan.progress.value>) => string }> = {
  downloading: { title: 'Спускается к источникам', detail: () => 'загружает открытые списки конфигураций' },
  parsing: { title: 'Разбирает находки', detail: () => 'отбрасывает дубликаты и повреждённые ссылки' },
  testing: {
    title: 'Ищет рабочие раковины',
    detail: (p) =>
      `${p.working_candidates} из ${p.target_candidates} найдено · проверено ${formatNumber(p.processed_candidates)} из ${formatNumber(p.total_candidates)}`,
  },
  rechecking: {
    title: 'Глубокая проверка',
    detail: (p) => `${p.working_candidates} кандидатов: пять замеров и загрузка 2 МБ через каждый`,
  },
  completed: { title: 'Поднимается на поверхность', detail: () => 'сохраняет улов' },
}

interface LogView {
  tone: 'quiet' | 'live' | 'done' | 'error'
  title: string
  detail: string
  progress?: number
  retry?: () => void
}

const view = computed<LogView>(() => {
  const cache = scan.cache.value
  const err = scan.scanError.value

  if (err) {
    if (err.kind === 'rate_limit') {
      return { tone: 'error', title: 'Скату нужна передышка', detail: `Слишком много запросов. Попробуйте через ${err.retryAfter ?? 60} с.` }
    }
    return {
      tone: 'error',
      title: 'Не удалось завершить поиск',
      detail: err.kind === 'network' || err.kind === 'timeout'
        ? 'Связь с сервером прервалась. Попробуйте ещё раз через некоторое время.'
        : 'Попробуйте ещё раз через некоторое время.',
      retry: () => void scan.startSearch(),
    }
  }

  if (scan.loadError.value && !scan.nodes.value.length) {
    return {
      tone: 'error',
      title: 'Скат не отвечает',
      detail: 'Сервер недоступен. Попробуйте ещё раз через некоторое время.',
      retry: () => void scan.retry(),
    }
  }

  if (scan.journey.value === 'diving') {
    if (scan.diveKind.value === 'replay') {
      return {
        tone: 'live',
        title: 'Возвращается к сегодняшним находкам',
        detail: cache ? `поиск прошёл ${formatMoment(cache.created_at)} — результаты общие для всех на сутки` : '',
        progress: scan.found.value ? props.revealed / scan.found.value : 0,
      }
    }
    const p = scan.progress.value
    const stage = p ? STAGES[p.stage] : undefined
    return {
      tone: 'live',
      title: stage?.title ?? 'Погружается',
      detail: p && stage ? stage.detail(p) : 'готовится к погружению',
      progress: p ? p.progress / 100 : 0,
    }
  }

  if (scan.journey.value === 'surfaced' && cache) {
    return {
      tone: 'done',
      title: 'Скат вернулся с уловом',
      detail: `лучшие ${cache.count} из ${cache.working} рабочих · просмотрено ${formatNumber(cache.tested)} конфигураций`,
    }
  }

  if (scan.cooldown.value) {
    return { tone: 'quiet', title: 'Последнее погружение не удалось', detail: 'Показаны предыдущие находки. Скат попробует снова чуть позже.' }
  }

  if (cache) {
    const until = formatUntil(cache.expires_at)
    return {
      tone: 'quiet',
      title: `Последнее погружение ${formatMoment(cache.created_at)}`,
      detail: `${cache.count} ${plural(cache.count, PEARLS_NOM)} · лучшие из ${cache.working} рабочих${until ? ` · следующее ${until}` : ''}`,
    }
  }

  return { tone: 'quiet', title: 'Скат ждёт у края рифа', detail: 'Начните поиск — он нырнёт и проверит открытые конфигурации.' }
})
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
    <button v-if="view.retry" class="btn btn--sm log__retry" type="button" @click="view.retry()">
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
