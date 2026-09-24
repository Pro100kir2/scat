<script setup lang="ts">
import { computed, nextTick, reactive, ref, shallowRef, watch } from 'vue'
import { useScan } from '@/composables/useScan'
import { useSpotlight } from '@/composables/useSpotlight'
import type { PearlNode } from '@/types/api'
import PearlCard from './PearlCard.vue'
import QrDialog from './QrDialog.vue'
import StateNote from './StateNote.vue'
import ResultsSkeleton from './ResultsSkeleton.vue'
import CopyButton from '@/components/ui/CopyButton.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import DonateLink from '@/components/ui/DonateLink.vue'
import { env } from '@/config/env'
import { countryName, formatMoment, formatNumber, networkLabel, securityLabel } from '@/utils/format'

type SortKey = 'rank' | 'latency' | 'country'

const scan = useScan()
const { spotlight } = useSpotlight()

const query = ref('')
const picked = reactive<Record<'country' | 'security' | 'network', string | null>>({
  country: null,
  security: null,
  network: null,
})
const sort = ref<SortKey>('rank')
const qrNode = shallowRef<PearlNode | null>(null)
const INITIAL_VISIBLE = 10
/** Search, sort and filters only earn their place in a longer list. */
const TOOLBAR_MIN = 8
const expanded = ref(false)

const nodes = computed(() => scan.nodes.value)

/** Filter options only for values that actually occur, most common first. */
function options(pick: (n: PearlNode) => string | null) {
  const counts = new Map<string, number>()
  for (const n of nodes.value) {
    const v = pick(n)
    if (v) counts.set(v, (counts.get(v) ?? 0) + 1)
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([value, count]) => ({ value, count }))
}

const countries = computed(() => options((n) => n.country).slice(0, 8))
const securities = computed(() => options((n) => n.security))
const networks = computed(() => options((n) => n.network))

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  const list = nodes.value.filter((n) => {
    if (picked.country && n.country !== picked.country) return false
    if (picked.security && n.security !== picked.security) return false
    if (picked.network && n.network !== picked.network) return false
    if (!q) return true
    const hay = `${n.address} ${n.country ?? ''} ${countryName(n.country) ?? ''} ${n.label} ${n.sni ?? ''}`.toLowerCase()
    return hay.includes(q)
  })
  if (sort.value === 'latency') return [...list].sort((a, b) => (a.latency_ms ?? 1e9) - (b.latency_ms ?? 1e9))
  if (sort.value === 'country') {
    return [...list].sort((a, b) => (countryName(a.country) ?? 'я').localeCompare(countryName(b.country) ?? 'я', 'ru'))
  }
  return list
})

const visible = computed(() =>
  expanded.value || hasFilters.value ? filtered.value : filtered.value.slice(0, INITIAL_VISIBLE),
)
const hidden = computed(() => filtered.value.length - visible.value.length)

// A pearl clicked in the ocean: make sure its card is rendered, then bring it into view.
watch(spotlight, async (rank) => {
  if (rank === null) return
  if (!visible.value.some((n) => n.rank === rank)) {
    resetFilters()
    expanded.value = true
    await nextTick()
  }
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  document.getElementById(`pearl-${rank}`)?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'center' })
})

const hasFilters = computed(() => !!(query.value || picked.country || picked.security || picked.network))
const allLinks = computed(() => filtered.value.map((n) => n.link).join('\n'))

function toggle(key: keyof typeof picked, value: string) {
  picked[key] = picked[key] === value ? null : value
}

function resetFilters() {
  query.value = ''
  picked.country = picked.security = picked.network = null
}

const phase = computed(() => {
  if (scan.loading.value) return 'loading'
  if (nodes.value.length) return 'results'
  if (scan.loadError.value) return 'error'
  if (scan.status.value?.is_scanning || scan.journey.value === 'diving') return 'searching'
  return 'empty'
})

const subtitle = computed(() => {
  const cache = scan.cache.value
  if (!cache || !nodes.value.length) return ''
  return `Лучшие ${nodes.value.length} из ${cache.working} рабочих · просмотрено ${formatNumber(cache.tested)} из ${formatNumber(cache.total_nodes)} · ${formatMoment(cache.created_at)}`
})
</script>

<template>
  <section id="catch" class="results container" aria-labelledby="catch-title">
    <header class="results__head">
      <div>
        <p class="eyebrow">Улов</p>
        <h2 id="catch-title" class="section-title">Жемчужины <em>дня</em></h2>
        <p v-if="subtitle" class="results__sub">{{ subtitle }}</p>
      </div>
      <div v-if="phase === 'results'" class="results__head-actions">
        <span v-if="scan.status.value?.cache_state === 'stale'" class="results__stale">
          <span class="results__stale-dot"></span> Обновляется
        </span>
        <CopyButton :text="allLinks" :label="hasFilters ? `Копировать ${filtered.length}` : 'Копировать все'" copied-label="Все скопированы" />
      </div>
    </header>

    <template v-if="phase === 'results'">
      <div v-if="nodes.length > TOOLBAR_MIN" class="toolbar" role="search">
        <label class="toolbar__search">
          <span class="visually-hidden">Поиск по стране или адресу</span>
          <AppIcon name="search" class="toolbar__search-icon" />
          <input v-model="query" class="field" type="search" placeholder="Страна, адрес или SNI" autocomplete="off" />
        </label>
        <label class="toolbar__sort">
          <span class="toolbar__sort-label">Сортировка</span>
          <select v-model="sort" class="toolbar__select">
            <option value="rank">Лучшие сначала</option>
            <option value="latency">По скорости</option>
            <option value="country">По стране</option>
          </select>
          <AppIcon name="chevron" class="toolbar__select-icon" />
        </label>
      </div>

      <div v-if="nodes.length > TOOLBAR_MIN" class="filters">
        <div v-if="countries.length > 1" class="filters__group" role="group" aria-label="Страна">
          <button
            v-for="c in countries"
            :key="c.value"
            class="chip"
            type="button"
            :aria-pressed="picked.country === c.value"
            @click="toggle('country', c.value)"
          >
            {{ countryName(c.value) }} <span class="mono chip__count">{{ c.count }}</span>
          </button>
        </div>
        <div v-if="securities.length > 1" class="filters__group" role="group" aria-label="Защита">
          <button v-for="s in securities" :key="s.value" class="chip" type="button" :aria-pressed="picked.security === s.value" @click="toggle('security', s.value)">
            {{ securityLabel(s.value) }}
          </button>
        </div>
        <div v-if="networks.length > 1" class="filters__group" role="group" aria-label="Транспорт">
          <button v-for="n in networks" :key="n.value" class="chip" type="button" :aria-pressed="picked.network === n.value" @click="toggle('network', n.value)">
            {{ networkLabel(n.value) }}
          </button>
        </div>
      </div>

      <TransitionGroup tag="div" name="surface" class="list" appear>
        <PearlCard
          v-for="(node, i) in visible"
          :key="node.id"
          :node="node"
          :spotlit="spotlight === node.rank"
          :style="{ '--i': Math.min(i, 12) }"
          @qr="qrNode = $event"
        />
      </TransitionGroup>
      <div v-if="hidden > 0" class="more">
        <button class="btn" type="button" @click="expanded = true">
          Показать ещё {{ hidden }}
          <AppIcon name="chevron" />
        </button>
      </div>
      <StateNote
        v-if="!filtered.length"
        title="Здесь пусто"
        text="Под эти фильтры не подошла ни одна жемчужина. Попробуйте ослабить условия."
      >
        <button class="btn btn--sm" type="button" @click="resetFilters">Сбросить фильтры</button>
      </StateNote>

      <aside v-if="env.donateUrl" class="support">
        <p>
          Скат бесплатный и без рекламы. Если он помог — поддержите его погружения:
          серверы и проверки стоят денег.
        </p>
        <DonateLink />
      </aside>
    </template>

    <ResultsSkeleton v-else-if="phase === 'loading'" />

    <StateNote
      v-else-if="phase === 'searching'"
      tone="searching"
      title="Скат ещё на глубине"
      text="Первое погружение займёт пару минут. Жемчужины появятся здесь сами — страницу можно не обновлять."
    />

    <StateNote
      v-else-if="phase === 'error'"
      tone="error"
      title="Не удалось связаться со Скатом"
      text="Сервер не ответил. Проверьте соединение или попробуйте ещё раз через некоторое время."
    >
      <button class="btn btn--sm" type="button" @click="scan.retry()"><AppIcon name="refresh" /> Попробовать снова</button>
    </StateNote>

    <StateNote
      v-else
      title="Жемчужин не нашлось"
      text="Скат обследовал дно, но ни одна конфигурация не прошла проверку. Такое бывает, когда источники обновляются. Загляните чуть позже."
    />

    <QrDialog :node="qrNode" @close="qrNode = null" />
  </section>
</template>

<style scoped>
.results {
  position: relative;
  padding-top: clamp(56px, 8vw, 96px);
  padding-bottom: clamp(64px, 10vw, 120px);
}

.results__head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 28px;
}

.results__head .eyebrow {
  margin-bottom: 10px;
}

.results__sub {
  margin-top: 10px;
  color: var(--text-3);
  font-size: 14px;
}

.results__head-actions {
  display: flex;
  align-items: center;
  gap: 14px;
}

.results__stale {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--text-3);
  font-size: 13px;
}

.results__stale-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--aqua);
  animation: blink 1.6s var(--ease-in-out) infinite;
}

.toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 14px;
}

.toolbar__search {
  position: relative;
  flex: 1 1 auto;
  max-width: 420px;
}

.toolbar__search-icon {
  position: absolute;
  top: 13px;
  left: 16px;
  color: var(--text-3);
  pointer-events: none;
}

.toolbar__sort {
  position: relative;
  display: flex;
  align-items: center;
  margin-left: auto;
}

.toolbar__sort-label {
  position: absolute;
  left: 18px;
  color: var(--text-3);
  font-size: 13px;
  pointer-events: none;
}

.toolbar__select {
  min-height: 44px;
  padding: 0 40px 0 110px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: rgba(8, 22, 32, 0.6);
  color: var(--text);
  font: inherit;
  font-size: 14px;
  appearance: none;
  cursor: pointer;
}

.toolbar__select:hover {
  border-color: var(--line-strong);
}

.toolbar__select-icon {
  position: absolute;
  right: 14px;
  width: 16px;
  color: var(--text-3);
  pointer-events: none;
}

.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
  margin-bottom: 26px;
}

.filters__group {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chip__count {
  color: var(--text-3);
  font-size: 12px;
}

.list {
  display: grid;
  gap: 10px;
}

.more {
  display: flex;
  justify-content: center;
  margin-top: 18px;
}

.surface-enter-active {
  transition: opacity 0.6s var(--ease-out), transform 0.7s var(--ease-out);
  transition-delay: calc(var(--i) * 45ms);
}

.surface-enter-from {
  opacity: 0;
  transform: translateY(14px);
}

.surface-leave-active {
  display: none;
}

.surface-move {
  transition: transform 0.5s var(--ease-out);
}

.support {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-top: 40px;
  padding: 22px 24px;
  border: 1px solid rgba(243, 233, 216, 0.14);
  border-radius: var(--radius);
  background: radial-gradient(120% 140% at 0% 0%, rgba(243, 233, 216, 0.06), transparent 60%);
}

.support p {
  max-width: 560px;
  color: var(--text-2);
  font-size: 15px;
}

@keyframes blink {
  50% {
    opacity: 0.3;
  }
}

@media (max-width: 720px) {
  .results__head {
    flex-direction: column;
    align-items: flex-start;
  }

  .toolbar {
    flex-direction: column;
  }

  .toolbar__search {
    max-width: none;
  }

  .toolbar__sort {
    margin-left: 0;
  }

  .toolbar__select {
    width: 100%;
  }

  .filters {
    flex-wrap: nowrap;
    margin-inline: calc(var(--gutter) * -1);
    padding-inline: var(--gutter);
    overflow-x: auto;
    scrollbar-width: none;
  }

  .filters__group {
    flex-wrap: nowrap;
  }

  .chip {
    flex: none;
  }

  .support {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
