<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { adminRequest, errorText } from '../api'
import Badge from '../components/Badge.vue'
import Pager from '../components/Pager.vue'
import StateBlock from '../components/StateBlock.vue'
import { duration, num, STATUS_TONE, when } from '../format'
import { usePoll } from '../state'
import type { Paged, ScanRow } from '../types'

const filters = reactive({ status: '', page: 1, per_page: 25 })
const data = ref<Paged<ScanRow> | null>(null)
const error = ref<string | null>(null)

const { refresh } = usePoll(async () => {
  try {
    data.value = await adminRequest<Paged<ScanRow>>('/scans', { query: { ...filters } })
    error.value = null
  } catch (err) {
    error.value = errorText(err)
  }
}, () => (data.value?.items.some((s) => s.status === 'running') ? 3_000 : 30_000))
watch(() => ({ ...filters }), () => void refresh())
</script>

<template>
  <div class="admin__top"><h1>Scans</h1></div>
  <div class="toolbar">
    <label>Status
      <select v-model="filters.status" class="input" @change="filters.page = 1">
        <option value="">Any</option><option value="running">Running</option><option value="completed">Completed</option>
        <option value="failed">Failed</option><option value="cancelled">Cancelled</option>
      </select>
    </label>
  </div>
  <StateBlock
    :loading="!data && !error" :error="!data ? error : null" :empty="data?.total === 0"
    empty-text="No scans recorded yet." @retry="refresh"
  >
    <div v-if="data" class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>Scan</th><th>Status</th><th>Query</th><th>Start</th><th>End</th><th>Duration</th><th>Sources</th>
            <th>Candidates</th><th>Unique</th><th>Tested</th><th>Passed</th><th>Failed</th><th>Probes</th><th>Errors</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in data.items" :key="s.id">
            <td class="mono"><RouterLink :to="`/admin/scans/${s.id}`">#{{ s.id }}</RouterLink></td>
            <td><Badge :tone="STATUS_TONE[s.status]">{{ s.status }}</Badge> <span class="muted">{{ s.trigger }}</span></td>
            <td class="mono">{{ s.query }}</td>
            <td>{{ when(s.started_at) }}</td>
            <td>{{ when(s.finished_at) }}</td>
            <td>{{ duration(s.duration_seconds) }}</td>
            <td class="mono">{{ s.sources_ok ?? '—' }}/{{ s.source_count ?? '—' }}</td>
            <td class="mono">{{ num(s.candidate_count) }}</td>
            <td class="mono">{{ num(s.unique_count) }}</td>
            <td class="mono">{{ num(s.tested_count) }}</td>
            <td class="mono">{{ num(s.passed_count) }}</td>
            <td class="mono">{{ num(s.failed_count) }}</td>
            <td class="mono">{{ s.probe_count ?? '—' }}</td>
            <td class="wrap" :title="s.error ?? ''">{{ s.error_count ?? '' }} <span class="muted">{{ s.error ?? '' }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
    <Pager v-if="data" v-model:page="filters.page" :per-page="filters.per_page" :total="data.total" />
  </StateBlock>
</template>
