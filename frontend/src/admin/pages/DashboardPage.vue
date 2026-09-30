<script setup lang="ts">
import { computed, ref } from 'vue'
import { adminRequest, errorText } from '../api'
import Badge from '../components/Badge.vue'
import StateBlock from '../components/StateBlock.vue'
import { ago, hhmm, num } from '../format'
import { admin, usePoll } from '../state'
import type { DashboardView, HealthView } from '../types'

const data = ref<DashboardView | null>(null)
const health = ref<HealthView | null>(null)
const error = ref<string | null>(null)

const { refresh } = usePoll(async () => {
  try {
    ;[data.value, health.value] = await Promise.all([
      adminRequest<DashboardView>('/dashboard'),
      adminRequest<HealthView>('/health'),
    ])
    error.value = null
  } catch (err) {
    error.value = errorText(err)
  }
}, () => (data.value?.scanner.state === 'scanning' ? 5_000 : 20_000))

const overall = computed(() => {
  const h = health.value
  if (!h) return null
  if (h.status === 'ok') return { tone: 'ok', text: '🟢 System healthy' }
  const broken = Object.entries(h.components).filter(([, c]) => c.status !== 'ok')
    .map(([name, c]) => `${name}: ${c.summary}`)
  return { tone: h.status, text: `${h.status === 'error' ? '🔴' : '🟡'} ${broken.join(' · ')}` }
})

const SEVERITY = { info: 'info', warning: 'warn', error: 'bad' } as const
</script>

<template>
  <div class="admin__top"><h1>Dashboard</h1></div>
  <StateBlock :loading="!data && !error" :error="!data ? error : null" @retry="refresh">
    <template v-if="data">
      <div v-if="overall" class="health" :class="`health--${overall.tone}`" role="status">{{ overall.text }}</div>

      <div class="cards section">
        <RouterLink to="/admin/probes/" class="card" style="text-decoration: none; color: inherit">
          <span class="card__label">Probes <span v-if="admin.unread" class="dot">{{ admin.unread }} new</span></span>
          <span class="card__value">{{ data.probes.approved }} <small class="muted">approved</small></span>
          <span class="card__lines">
            <span>{{ data.probes.pending }} pending · {{ data.probes.disabled }} disabled</span>
            <span>{{ data.probes.online }} online · {{ data.probes.verifying }} verifying</span>
            <span v-if="data.probes.verification_failed" style="color: var(--coral)">
              {{ data.probes.verification_failed }} failed verification
            </span>
          </span>
        </RouterLink>
        <RouterLink to="/admin/sources/" class="card" style="text-decoration: none; color: inherit">
          <span class="card__label">Sources</span>
          <span class="card__value">{{ data.sources.enabled }} <small class="muted">enabled</small></span>
          <span class="card__lines">
            <span>{{ data.sources.healthy }} healthy</span>
            <span :style="data.sources.failed ? 'color: var(--coral)' : ''">{{ data.sources.failed }} failed</span>
            <span v-if="data.sources.never_fetched">{{ data.sources.never_fetched }} not fetched yet</span>
          </span>
        </RouterLink>
        <RouterLink to="/admin/scanner/" class="card" style="text-decoration: none; color: inherit">
          <span class="card__label">Scanner</span>
          <span class="card__value" style="text-transform: capitalize">{{ data.scanner.state }}</span>
          <span class="card__lines">
            <span v-if="data.scanner.state === 'scanning'">{{ data.scanner.live.stage }} · {{ data.scanner.live.progress }}%</span>
            <span>Last scan: {{ ago(data.scanner.last_scan?.started_at) }}</span>
            <span v-if="data.scanner.last_error" style="color: var(--coral)">Last error: {{ data.scanner.last_error }}</span>
          </span>
        </RouterLink>
        <RouterLink to="/admin/cache/" class="card" style="text-decoration: none; color: inherit">
          <span class="card__label">Cache</span>
          <span class="card__value" style="text-transform: capitalize">{{ data.cache.state }}</span>
          <span class="card__lines">
            <span>{{ num(data.cache.count) }} in the top · {{ data.cache.candidates }} candidates</span>
            <span>Built {{ ago(data.cache.created_at) }}</span>
          </span>
        </RouterLink>
        <RouterLink to="/admin/logs/?severity=warning" class="card" style="text-decoration: none; color: inherit">
          <span class="card__label">Errors today</span>
          <span class="card__value" :style="data.errors_today ? 'color: var(--coral)' : ''">{{ data.errors_today }}</span>
          <span class="card__lines"><span>{{ data.warnings_today }} warnings</span></span>
        </RouterLink>
      </div>

      <section class="section">
        <h2>Recent events</h2>
        <StateBlock :empty="!data.recent_events.length" empty-text="No events yet.">
          <div class="table-wrap">
            <table class="table">
              <tbody>
                <tr v-for="e in data.recent_events" :key="e.id">
                  <td class="mono muted">{{ hhmm(e.ts) }}</td>
                  <td><Badge :tone="SEVERITY[e.severity]">{{ e.component }}</Badge></td>
                  <td class="wrap">
                    {{ e.message }}
                    <RouterLink v-if="e.probe_id" :to="`/admin/probes/${e.probe_id}`"> → probe</RouterLink>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </StateBlock>
      </section>
    </template>
  </StateBlock>
</template>
