<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { adminRequest, errorText } from '../api'
import Badge from '../components/Badge.vue'
import Pager from '../components/Pager.vue'
import StateBlock from '../components/StateBlock.vue'
import { when } from '../format'
import { usePoll } from '../state'
import type { EventRow, Paged } from '../types'

const route = useRoute()
const q = (key: string) => (typeof route.query[key] === 'string' ? (route.query[key] as string) : '')
const filters = reactive({
  component: q('component'), severity: q('severity'), probe_id: q('probe_id'), source_id: q('source_id'),
  since: '', until: '', q: '', page: 1, per_page: 50,
})
const data = ref<Paged<EventRow> | null>(null)
const error = ref<string | null>(null)
const open = ref<number | null>(null)

const toTs = (local: string) => (local ? Math.floor(new Date(local).getTime() / 1000) : undefined)

const { refresh } = usePoll(async () => {
  try {
    data.value = await adminRequest<Paged<EventRow>>('/events', {
      query: { ...filters, since: toTs(filters.since), until: toTs(filters.until) },
    })
    error.value = null
  } catch (err) {
    error.value = errorText(err)
  }
}, 30_000)

let timer: ReturnType<typeof setTimeout> | undefined
watch(() => ({ ...filters }), (now, before) => {
  if (now.page === before.page) filters.page = 1
  clearTimeout(timer)
  timer = setTimeout(() => void refresh(), 250)
})

const COMPONENTS = ['probe', 'verification', 'source', 'scanner', 'cache', 'auth', 'admin', 'system']
const TONE = { info: 'info', warning: 'warn', error: 'bad' } as const
</script>

<template>
  <div class="admin__top"><h1>Logs</h1></div>
  <p class="muted" style="margin: -8px 0 16px; font-size: 14px">
    Application events, not raw server logs. Tokens, secrets and phone numbers are removed before an event is stored.
  </p>
  <div class="toolbar" role="search">
    <label>Component
      <select v-model="filters.component" class="input">
        <option value="">Any</option><option v-for="c in COMPONENTS" :key="c" :value="c">{{ c }}</option>
      </select>
    </label>
    <label>Severity
      <select v-model="filters.severity" class="input">
        <option value="">Any</option><option value="warning">Warnings and errors</option><option value="error">Errors</option>
      </select>
    </label>
    <label>Probe <input v-model.trim="filters.probe_id" class="input mono" placeholder="prb_…" /></label>
    <label>Source ID <input v-model.trim="filters.source_id" class="input mono" inputmode="numeric" style="min-width: 80px" /></label>
    <label>From <input v-model="filters.since" class="input" type="datetime-local" /></label>
    <label>To <input v-model="filters.until" class="input" type="datetime-local" /></label>
    <label>Text <input v-model="filters.q" class="input" type="search" /></label>
  </div>
  <StateBlock
    :loading="!data && !error" :error="!data ? error : null" :empty="data?.total === 0"
    empty-text="No events match." @retry="refresh"
  >
    <div v-if="data" class="table-wrap">
      <table class="table">
        <thead><tr><th>Time</th><th>Severity</th><th>Component</th><th>Event</th><th>Message</th></tr></thead>
        <tbody>
          <template v-for="e in data.items" :key="e.id">
            <tr style="cursor: pointer" @click="open = open === e.id ? null : e.id">
              <td class="mono">{{ when(e.ts) }}</td>
              <td><Badge :tone="TONE[e.severity]">{{ e.severity }}</Badge></td>
              <td>{{ e.component }}</td>
              <td class="mono">{{ e.kind }}</td>
              <td class="wrap">
                {{ e.message }}
                <RouterLink v-if="e.probe_id" :to="`/admin/probes/${e.probe_id}`" @click.stop> → {{ e.probe_id }}</RouterLink>
                <RouterLink v-if="e.scan_id" :to="`/admin/scans/${e.scan_id}`" @click.stop> → scan #{{ e.scan_id }}</RouterLink>
              </td>
            </tr>
            <tr v-if="open === e.id && e.data">
              <td colspan="5"><pre class="mono" style="margin: 0; white-space: pre-wrap; font-size: 12px">{{ JSON.stringify(e.data, null, 2) }}</pre></td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
    <Pager v-if="data" v-model:page="filters.page" :per-page="filters.per_page" :total="data.total" />
  </StateBlock>
</template>
