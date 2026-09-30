<script setup lang="ts">
import { ref } from 'vue'
import { adminRequest, errorText } from '../api'
import Badge from '../components/Badge.vue'
import StateBlock from '../components/StateBlock.vue'
import { duration, num, STATUS_TONE, when } from '../format'
import { usePoll } from '../state'
import type { ScanDetail } from '../types'

const props = defineProps<{ id: string }>()
const scan = ref<ScanDetail | null>(null)
const error = ref<string | null>(null)

const { refresh } = usePoll(async () => {
  try {
    scan.value = await adminRequest<ScanDetail>(`/scans/${props.id}`)
    error.value = null
  } catch (err) {
    error.value = (err as { status?: number }).status === 404 ? 'No such scan.' : errorText(err)
  }
}, () => (scan.value?.status === 'running' ? 3_000 : 60_000))
</script>

<template>
  <div class="admin__top">
    <div>
      <RouterLink to="/admin/scans/" class="muted">← Scans</RouterLink>
      <h1>Scan #{{ id }}</h1>
    </div>
  </div>
  <StateBlock :loading="!scan && !error" :error="!scan ? error : null" @retry="refresh">
    <template v-if="scan">
      <div class="box">
        <dl class="facts">
          <div><dt>Status</dt><dd><Badge :tone="STATUS_TONE[scan.status]">{{ scan.status }}</Badge> ({{ scan.trigger }})</dd></div>
          <div><dt>Query</dt><dd class="mono">{{ scan.query }}</dd></div>
          <div><dt>Start</dt><dd>{{ when(scan.started_at) }}</dd></div>
          <div><dt>End</dt><dd>{{ when(scan.finished_at) }}</dd></div>
          <div><dt>Duration</dt><dd>{{ duration(scan.duration_seconds) }}</dd></div>
          <div><dt>Sources (ok / total)</dt><dd class="mono">{{ scan.sources_ok ?? '—' }} / {{ scan.source_count ?? '—' }}</dd></div>
          <div><dt>Candidates</dt><dd class="mono">{{ num(scan.candidate_count) }}</dd></div>
          <div><dt>Unique</dt><dd class="mono">{{ num(scan.unique_count) }}</dd></div>
          <div><dt>Tested</dt><dd class="mono">{{ num(scan.tested_count) }}</dd></div>
          <div><dt>Passed</dt><dd class="mono">{{ num(scan.passed_count) }}</dd></div>
          <div><dt>Failed</dt><dd class="mono">{{ num(scan.failed_count) }}</dd></div>
          <div><dt>In the top</dt><dd class="mono">{{ scan.top_count ?? '—' }}</dd></div>
          <div><dt>Probes used</dt><dd class="mono">{{ scan.probe_count ?? '—' }} ({{ scan.probe_jobs ?? 0 }} jobs)</dd></div>
          <div v-if="scan.error" style="grid-column: 1 / -1"><dt>Error</dt><dd style="color: var(--coral)">{{ scan.error }}</dd></div>
        </dl>
      </div>

      <section v-if="scan.detail" class="section">
        <h2>Sources</h2>
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>Source</th><th>Result</th><th>Links</th><th>Valid</th><th>Unique</th></tr></thead>
            <tbody>
              <tr v-for="s in scan.detail.sources" :key="s.url">
                <td class="wrap">{{ s.name || s.url }}<br /><span class="mono muted" style="font-size: 12px; word-break: break-all">{{ s.url }}</span></td>
                <td class="wrap">
                  <Badge :tone="s.success ? 'ok' : 'bad'">{{ s.success ? (s.cached ? 'cached' : 'ok') : 'failed' }}</Badge>
                  <span class="muted"> {{ s.error }}</span>
                </td>
                <td class="mono">{{ num(s.vless_count) }}</td>
                <td class="mono">{{ num(s.valid_count) }}</td>
                <td class="mono">{{ num(s.unique_count) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <h2 class="section">Top at the end of this scan <span class="muted">(backend order, before probe scores)</span></h2>
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>#</th><th>Endpoint</th><th>Exit</th><th>Latency</th><th>Speed</th></tr></thead>
            <tbody>
              <tr v-for="(n, i) in scan.detail.top" :key="n.fingerprint">
                <td class="mono">{{ i + 1 }}</td>
                <td class="mono">{{ n.address }}:{{ n.port }}</td>
                <td>{{ n.exit_country ?? '—' }}</td>
                <td class="mono">{{ n.latency_ms ? `${Math.round(n.latency_ms)} ms` : '—' }}</td>
                <td class="mono">{{ n.speed_mbps ? `${n.speed_mbps.toFixed(1)} Mbit/s` : '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </StateBlock>
</template>
