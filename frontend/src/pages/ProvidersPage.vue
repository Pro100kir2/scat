<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import StateNote from '@/components/results/StateNote.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import { fetchProviders } from '@/services/api'
import type { ProvidersResponse } from '@scat/shared'
import { countryName, formatAgo, plural } from '@scat/shared'

/** Read-only overview. Loading it never starts any checks. */
const REFRESH_MS = 30_000
const PROBES = ['зонд', 'зонда', 'зондов'] as const

const data = shallowRef<ProvidersResponse | null>(null)
const failed = ref(false)
const now = ref(Date.now())
let timer: ReturnType<typeof setTimeout> | undefined
let controller: AbortController | null = null

async function load() {
  clearTimeout(timer)
  controller?.abort()
  controller = new AbortController()
  try {
    data.value = await fetchProviders(controller.signal)
    failed.value = false
  } catch (err) {
    if ((err as { kind?: string }).kind === 'aborted') return
    failed.value = true
  }
  now.value = Date.now()
  timer = setTimeout(() => (document.hidden ? (timer = setTimeout(load, REFRESH_MS)) : void load()), REFRESH_MS)
}

onMounted(load)
onBeforeUnmount(() => {
  clearTimeout(timer)
  controller?.abort()
})

// Russian collation, as in the cards' network availability (Cyrillic names first).
const providers = computed(() => [...(data.value?.providers ?? [])].sort((a, b) => a.name.localeCompare(b.name, 'ru')))
const probes = computed(() => data.value?.probes ?? [])
const online = computed(() => probes.value.filter((p) => p.online).length)
// "дольше 2 минут": genitive after "дольше".
const MINUTES = ['минуты', 'минут', 'минут'] as const
const offlineMinutes = computed(() => Math.max(1, Math.ceil((data.value?.offline_after_seconds ?? 100) / 60)))
</script>

<template>
  <main id="top" class="page container">
    <header class="intro">
      <p class="eyebrow">Провайдеры</p>
      <h1 class="page-title">Как видно <em>из разных сетей</em></h1>
      <p class="page-lead">
        Скат сам проверяет, что конфигурация работает. Но один и тот же сервер может открываться у одного
        провайдера и быть заблокирован у другого. Поэтому лучшие 25 найденных серверов дополнительно проверяют
        зонды: маленькие программы на устройствах добровольцев внутри сетей провайдеров.
      </p>
      <div class="page-actions">
        <RouterLink class="btn btn--primary" to="/providers/add/">
          <AppIcon name="plus" /> Добавить своего провайдера
        </RouterLink>
        <RouterLink class="btn btn--quiet" to="/providers/install/">Как устроен зонд</RouterLink>
      </div>
    </header>

    <section class="block" aria-labelledby="available-title">
      <div class="block__head">
        <h2 id="available-title" class="section-title">Доступные <em>провайдеры</em></h2>
        <p v-if="data" class="block__meta">
          {{ online }} {{ plural(online, PROBES) }} на связи из {{ probes.length }}
        </p>
      </div>

      <div v-if="!data && !failed" class="skeleton" aria-hidden="true">
        <span v-for="i in 3" :key="i"></span>
      </div>
      <StateNote
        v-else-if="failed && !data"
        tone="error"
        title="Не удалось загрузить провайдеров"
        text="Сервер не ответил. Попробуйте обновить страницу чуть позже."
      >
        <button class="btn btn--sm" type="button" @click="load"><AppIcon name="refresh" /> Попробовать снова</button>
      </StateNote>
      <StateNote
        v-else-if="!providers.length"
        title="Пока ни одного провайдера"
        text="Здесь появятся сети, из которых зонды проверяют доступность серверов. Станьте первым."
      >
        <RouterLink class="btn btn--sm" to="/providers/add/">Добавить своего провайдера</RouterLink>
      </StateNote>

      <ul v-else class="providers">
        <li v-for="p in providers" :key="p.id" class="provider" :class="`is-${p.status}`">
          <span class="provider__light" aria-hidden="true"></span>
          <div class="provider__body">
            <p class="provider__name">{{ p.name }}</p>
            <p class="provider__where">
              <span class="mono provider__cc">{{ p.country }}</span>
              {{ countryName(p.country) }}<template v-if="p.regions.length"> · {{ p.regions.join(', ') }}</template>
            </p>
            <dl class="provider__facts">
              <div>
                <dt>Статус</dt>
                <dd>{{ p.status === 'online' ? 'На связи' : p.status === 'offline' ? 'Не на связи' : 'Нет зондов' }}</dd>
              </div>
              <div>
                <dt>Зонды</dt>
                <dd>{{ p.probes_online }} из {{ p.probes_total }} онлайн</dd>
              </div>
              <div v-if="p.asns.length">
                <dt>ASN</dt>
                <dd class="mono">{{ p.asns.map((a) => `AS${a}`).join(', ') }}</dd>
              </div>
              <div>
                <dt>Последний сигнал</dt>
                <dd>{{ formatAgo(p.last_heartbeat, now) }}</dd>
              </div>
            </dl>
          </div>
        </li>
      </ul>
    </section>

    <section v-if="probes.length" class="block" aria-labelledby="probes-title">
      <div class="block__head">
        <h2 id="probes-title" class="section-title">Зонды</h2>
        <p class="block__meta">Зонд считается не на связи, если молчит дольше {{ offlineMinutes }} {{ plural(offlineMinutes, MINUTES) }}.</p>
      </div>
      <table class="probes">
        <thead>
          <tr>
            <th scope="col">Состояние</th>
            <th scope="col">Провайдер</th>
            <th scope="col">Город</th>
            <th scope="col">ASN</th>
            <th scope="col">Платформа</th>
            <th scope="col">Версия</th>
            <th scope="col">Последний сигнал</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="probe in probes" :key="probe.id" :class="probe.online ? 'is-online' : 'is-offline'">
            <td data-label="Состояние">
              <span class="dot" aria-hidden="true"></span>{{ probe.online ? 'Online' : 'Offline' }}
            </td>
            <td data-label="Провайдер">{{ probe.provider ?? '—' }}</td>
            <td data-label="Город">{{ probe.region ?? '—' }}</td>
            <td data-label="ASN" class="mono">{{ probe.asn ? `AS${probe.asn}` : '—' }}</td>
            <td data-label="Платформа">{{ probe.platform ?? '—' }}</td>
            <td data-label="Версия" class="mono">{{ probe.version ?? '—' }}</td>
            <td data-label="Последний сигнал">{{ formatAgo(probe.last_seen, now) }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </main>
</template>

<style scoped>
.block {
  margin-top: clamp(56px, 8vw, 96px);
}

.block__head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px 24px;
  margin-bottom: 24px;
}

.block__meta {
  color: var(--text-3);
  font-size: 14px;
}

.providers {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.provider {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: 14px;
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: linear-gradient(180deg, rgba(16, 38, 52, 0.55), rgba(10, 26, 37, 0.55));
}

.provider__light {
  width: 12px;
  height: 12px;
  margin-top: 8px;
  border-radius: 50%;
  border: 1.5px solid var(--text-3);
}

.provider.is-online .provider__light {
  border: 0;
  background: radial-gradient(circle at 35% 30%, #e6fffb, var(--aqua) 55%, #3f8f8a 100%);
  box-shadow: 0 0 14px rgba(143, 214, 207, 0.55);
}

.provider.is-offline .provider__light {
  border: 0;
  background: #0b1822;
  box-shadow: inset 0 0 0 1px rgba(168, 208, 222, 0.35);
}

.provider__name {
  font-family: var(--font-display);
  font-size: 26px;
  line-height: 1.1;
}

.provider__where {
  margin-top: 4px;
  color: var(--text-2);
  font-size: 14px;
}

.provider__cc {
  margin-right: 4px;
  padding: 1px 6px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  font-size: 11px;
  color: var(--text-3);
}

.provider__facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 16px;
  margin: 16px 0 0;
}

.provider__facts dt {
  font-size: 12px;
  color: var(--text-3);
}

.provider__facts dd {
  margin: 2px 0 0;
  font-size: 14px;
}

.probes {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

.probes th {
  padding: 0 12px 10px 0;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-3);
  text-align: left;
  white-space: nowrap;
}

.probes td {
  padding: 12px 12px 12px 0;
  border-top: 1px solid var(--line);
  color: var(--text-2);
}

.probes td:first-child {
  color: var(--text);
  white-space: nowrap;
}

.dot {
  display: inline-block;
  width: 9px;
  height: 9px;
  margin-right: 8px;
  border-radius: 50%;
  vertical-align: 0;
}

.is-online .dot {
  background: var(--aqua);
  box-shadow: 0 0 10px rgba(143, 214, 207, 0.6);
}

.is-offline .dot {
  background: #0b1822;
  box-shadow: inset 0 0 0 1px rgba(168, 208, 222, 0.4);
}

.skeleton {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
}

.skeleton span {
  height: 170px;
  border-radius: var(--radius);
  background: rgba(16, 38, 52, 0.45);
  animation: shimmer 1.6s ease-in-out infinite;
}

@keyframes shimmer {
  50% { opacity: 0.5; }
}

/* Phones: each probe becomes a small card of label/value rows. */
@media (max-width: 720px) {
  .probes thead {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
  }

  .probes tr {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px 16px;
    padding: 14px 0;
    border-top: 1px solid var(--line);
  }

  .probes td {
    padding: 0;
    border: 0;
  }

  .probes td::before {
    content: attr(data-label);
    display: block;
    font-size: 11px;
    color: var(--text-3);
  }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton span {
    animation: none;
  }
}
</style>
