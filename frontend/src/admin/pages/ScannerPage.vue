<script setup lang="ts">
import { computed, ref } from 'vue'
import { adminRequest, errorText } from '../api'
import Badge from '../components/Badge.vue'
import StateBlock from '../components/StateBlock.vue'
import { confirmAction } from '../confirm'
import { ago, duration, num, when } from '../format'
import { toast, toastError, usePoll } from '../state'
import type { ScannerView } from '../types'

const view = ref<ScannerView | null>(null)
const error = ref<string | null>(null)
const busy = ref(false)

async function load() {
  try {
    view.value = await adminRequest<ScannerView>('/scanner')
    error.value = null
  } catch (err) {
    error.value = errorText(err)
  }
}

const scanning = computed(() => view.value?.state === 'scanning' || !!view.value?.queue.length)
const { refresh } = usePoll(load, () => (scanning.value ? 2_000 : 15_000))

const OUTCOMES: Record<string, string> = {
  started: 'Scan started', joined: 'A scan is already running; joined it', cached: 'The cache is fresh: nothing to do',
  cooldown: 'The last scan failed recently; waiting for the cooldown', busy: 'Too many scans queued',
}

async function act(action: 'start' | 'run' | 'cancel' | 'restart') {
  const prompts = {
    start: null,
    run: { title: 'Run a scan now?', body: 'Ignores the cache and rescans every source. Takes several minutes and a lot of bandwidth.', confirm: 'Run now' },
    cancel: { title: 'Cancel the scan?', body: 'Xray is stopped and the current results stay as they are.', confirm: 'Cancel scan', danger: true },
    restart: { title: 'Restart the scan?', body: 'Cancels the running scan, then starts a fresh one immediately.', confirm: 'Restart', danger: true },
  }
  const prompt = prompts[action]
  if (prompt && !(await confirmAction(prompt))) return
  busy.value = true
  try {
    const reply = await adminRequest<{ outcome?: string; cancelled?: string[] }>(`/scanner/${action}`, { method: 'POST' })
    toast(reply.outcome ? OUTCOMES[reply.outcome] ?? reply.outcome : `Cancelled: ${reply.cancelled?.join(', ')}`, 'ok')
    await load()
  } catch (err) {
    toastError(action, err)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="admin__top">
    <h1>Scanner</h1>
    <div class="actions">
      <button class="btn btn--sm" :disabled="busy || scanning" title="Scans only if the cache is stale" @click="act('start')">Start scan</button>
      <button class="btn btn--sm btn--primary" :disabled="busy || scanning" @click="act('run')">Run scan now</button>
      <button class="btn btn--sm btn--danger" :disabled="busy || !scanning" @click="act('cancel')">Cancel scan</button>
      <button class="btn btn--sm btn--danger" :disabled="busy" @click="act('restart')">Restart scanner</button>
    </div>
  </div>
  <p class="muted" style="margin-top: -8px; margin-bottom: 16px; font-size: 14px">
    One scan runs at a time. Cache actions (clear / rebuild) are on the <RouterLink to="/admin/cache/">Cache</RouterLink> page.
  </p>

  <StateBlock :loading="!view && !error" :error="!view ? error : null" @retry="refresh">
    <template v-if="view">
      <div class="cards">
        <div class="card">
          <span class="card__label">Status</span>
          <span class="card__value" style="text-transform: capitalize">{{ view.state }}</span>
          <span class="card__lines">
            <span v-if="view.live.query">query: {{ view.live.query }} · stage: {{ view.live.stage }}</span>
            <span v-if="view.last_error" style="color: var(--coral)">last error: {{ view.last_error }}</span>
          </span>
        </div>
        <div class="card">
          <span class="card__label">Queue</span>
          <span class="card__value">{{ view.queue.length }}</span>
          <span class="card__lines">
            <span v-for="q in view.queue" :key="q.query">{{ q.query }} — {{ q.state }}</span>
            <span v-if="!view.queue.length">empty</span>
          </span>
        </div>
        <div class="card">
          <span class="card__label">Cache</span>
          <span class="card__value" style="text-transform: capitalize">{{ view.cache.cache_state }}</span>
          <span v-if="view.cache.cache" class="card__lines">
            <span>{{ view.cache.cache.count }} configs · built {{ view.cache.cache.created_at }}</span>
            <span>expires {{ view.cache.cache.expires_at }}</span>
          </span>
        </div>
      </div>

      <section v-if="scanning" class="section box">
        <h2>Current scan <span v-if="view.current_scan" class="muted mono">#{{ view.current_scan.id }}</span></h2>
        <div class="bar" role="progressbar" :aria-valuenow="view.live.progress" aria-valuemin="0" aria-valuemax="100">
          <span :style="{ width: `${view.live.progress}%` }"></span>
        </div>
        <dl class="facts" style="margin-top: 14px">
          <div><dt>Stage</dt><dd>{{ view.live.stage }} · {{ view.live.progress }}%</dd></div>
          <div><dt>Candidates</dt><dd class="mono">{{ num(view.live.total_candidates) }}</dd></div>
          <div><dt>Tested</dt><dd class="mono">{{ num(view.live.processed_candidates) }}</dd></div>
          <div><dt>Working</dt><dd class="mono">{{ view.live.working_candidates }} / {{ view.live.target_candidates }}</dd></div>
          <div><dt>Elapsed</dt><dd>{{ duration(view.live.elapsed_seconds) }}</dd></div>
          <div v-if="view.current_scan"><dt>Started</dt><dd>{{ when(view.current_scan.started_at) }} ({{ view.current_scan.trigger }})</dd></div>
        </dl>
      </section>

      <section v-if="view.last_scan" class="section box">
        <h2>
          Last scan <RouterLink :to="`/admin/scans/${view.last_scan.id}`" class="mono">#{{ view.last_scan.id }}</RouterLink>
          <Badge :tone="view.last_scan.status === 'completed' ? 'ok' : view.last_scan.status === 'running' ? 'warn' : 'bad'">{{ view.last_scan.status }}</Badge>
        </h2>
        <dl class="facts">
          <div><dt>Start / end</dt><dd>{{ when(view.last_scan.started_at) }} → {{ when(view.last_scan.finished_at) }}</dd></div>
          <div><dt>Duration</dt><dd>{{ duration(view.last_scan.duration_seconds) }} ({{ ago(view.last_scan.finished_at) }})</dd></div>
          <div><dt>Candidates / unique</dt><dd class="mono">{{ num(view.last_scan.candidate_count) }} / {{ num(view.last_scan.unique_count) }}</dd></div>
          <div><dt>Tested / passed / failed</dt><dd class="mono">{{ num(view.last_scan.tested_count) }} / {{ num(view.last_scan.passed_count) }} / {{ num(view.last_scan.failed_count) }}</dd></div>
          <div><dt>Source errors</dt><dd class="mono">{{ view.last_scan.error_count ?? '—' }}</dd></div>
          <div><dt>Probes used</dt><dd class="mono">{{ view.last_scan.probe_count ?? '—' }} ({{ view.last_scan.probe_jobs ?? 0 }} jobs)</dd></div>
          <div v-if="view.last_scan.error" style="grid-column: 1 / -1"><dt>Error</dt><dd style="color: var(--coral)">{{ view.last_scan.error }}</dd></div>
        </dl>
      </section>

      <section class="section">
        <h2>Counters since start</h2>
        <dl class="facts box">
          <div v-for="(value, key) in view.stats" :key="key"><dt>{{ key }}</dt><dd class="mono">{{ value }}</dd></div>
          <div v-for="(value, key) in view.settings" :key="key"><dt>{{ key }}</dt><dd class="mono">{{ value }}</dd></div>
        </dl>
      </section>
    </template>
  </StateBlock>
</template>
