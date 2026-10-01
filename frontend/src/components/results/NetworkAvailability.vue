<script setup lang="ts">
import { computed } from 'vue'
import type { Reachability } from '@scat/shared'
import { NETWORKS, REACH_STATE, reachSummary } from '@scat/shared'
import { plural } from '@scat/shared'

/**
 * "Network availability" inside an opened card: one underwater beacon per provider.
 * Only reachability of the endpoint from that ISP; the VLESS check is the card itself.
 */
const props = defineProps<{ rows: Reachability[] }>()

const sorted = computed(() => [...props.rows].sort((a, b) => a.provider.localeCompare(b.provider, 'ru')))
const summary = computed(() => reachSummary(props.rows))
</script>

<template>
  <section class="reach" aria-label="Доступность в сетях провайдеров">
    <div class="reach__head">
      <h4 class="reach__title">Доступность в сетях</h4>
      <p v-if="summary.tested" class="reach__summary">
        Доступна в <strong>{{ summary.reachable }}</strong> из {{ summary.tested }} {{ plural(summary.tested, NETWORKS) }}
      </p>
    </div>

    <ul v-if="sorted.length" class="reach__list">
      <li
        v-for="row in sorted"
        :key="row.provider_id"
        class="beacon"
        :class="`beacon--${REACH_STATE[row.state].tone}`"
        :title="REACH_STATE[row.state].hint"
      >
        <span class="beacon__light" aria-hidden="true">
          <i class="beacon__bubble"></i><i class="beacon__bubble"></i>
        </span>
        <span class="beacon__name">{{ row.provider }}</span>
        <span class="beacon__state">
          {{ REACH_STATE[row.state].label }}<template v-if="row.state === 'SUCCESS' && row.latency_ms !== null"><span class="mono"> · {{ row.latency_ms }} мс</span></template>
        </span>
      </li>
    </ul>

    <p v-else class="reach__empty">
      Пока ни один провайдер не подключил зонд.
      <RouterLink to="/providers/add/">Добавьте своего</RouterLink>, и здесь появится, открывается ли сервер из вашей сети.
    </p>
    <p v-if="sorted.length" class="reach__note">
      Зонды проверяют только, доступен ли сервер из сети провайдера. Работа самой конфигурации проверена выше.
    </p>
  </section>
</template>

<style scoped>
.reach {
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}

.reach__head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 4px 16px;
}

.reach__title {
  margin: 0;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-3);
}

.reach__summary {
  font-size: 13px;
  color: var(--text-2);
}

.reach__summary strong {
  color: var(--aqua);
  font-weight: 600;
}

.reach__list {
  display: grid;
  /* Two per row even on phones: the list stays short with several providers. */
  grid-template-columns: repeat(auto-fill, minmax(136px, 1fr));
  gap: 8px;
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
}

/* A marker on the sea floor: a light, the network's name, and its state in words. */
.beacon {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  grid-template-rows: auto auto;
  column-gap: 10px;
  align-items: center;
  padding: 9px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: rgba(3, 12, 18, 0.35);
}

.beacon__light {
  position: relative;
  grid-row: span 2;
  justify-self: center;
  width: 12px;
  height: 12px;
  border-radius: 50%;
}

.beacon__name {
  overflow: hidden;
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.beacon__state {
  font-size: 12px;
  color: var(--text-3);
}

/* Reachable: a clear aqua light with bubbles drifting up. */
.beacon--reachable .beacon__light {
  background: radial-gradient(circle at 35% 30%, #e6fffb, var(--aqua) 55%, #3f8f8a 100%);
  box-shadow: 0 0 12px rgba(143, 214, 207, 0.55);
}

.beacon--reachable .beacon__state {
  color: var(--aqua);
}

.beacon__bubble {
  display: none;
  position: absolute;
  left: 3px;
  bottom: 10px;
  width: 4px;
  height: 4px;
  border: 1px solid rgba(143, 214, 207, 0.7);
  border-radius: 50%;
  opacity: 0;
}

.beacon--reachable .beacon__bubble {
  display: block;
  animation: rise 3.2s var(--ease-out) infinite;
}

.beacon--reachable .beacon__bubble + .beacon__bubble {
  left: 7px;
  width: 3px;
  height: 3px;
  animation-delay: 1.4s;
}

@keyframes rise {
  0% { transform: translateY(0); opacity: 0; }
  20% { opacity: 1; }
  100% { transform: translateY(-14px); opacity: 0; }
}

/* Blocked: a dim coral light, no glow. */
.beacon--blocked .beacon__light {
  background: radial-gradient(circle at 35% 30%, #ffd9cf, var(--coral) 60%, #8a4a3e 100%);
  opacity: 0.85;
}

.beacon--blocked {
  border-color: rgba(236, 154, 134, 0.25);
}

.beacon--blocked .beacon__state {
  color: var(--coral);
}

/* Checking: sand-coloured, breathing. */
.beacon--checking .beacon__light {
  background: var(--sand);
  animation: breathe 1.6s ease-in-out infinite;
}

.beacon--checking .beacon__state {
  color: var(--sand);
}

@keyframes breathe {
  50% { opacity: 0.35; transform: scale(0.8); }
}

/* Probe offline: a light gone dark under the water. */
.beacon--offline .beacon__light {
  background: #0b1822;
  box-shadow: inset 0 0 0 1px rgba(168, 208, 222, 0.35);
}

.beacon--offline .beacon__name {
  color: var(--text-2);
}

/* No probe / not tested yet: an empty ring. */
.beacon--none .beacon__light {
  border: 1.5px solid var(--text-3);
  background: transparent;
}

.beacon--none .beacon__name {
  color: var(--text-2);
}

.reach__empty,
.reach__note {
  margin-top: 10px;
  font-size: 13px;
  color: var(--text-3);
}

.reach__empty a {
  color: var(--aqua);
}

@media (prefers-reduced-motion: reduce) {
  .beacon__bubble,
  .beacon--checking .beacon__light {
    animation: none;
  }
}
</style>
