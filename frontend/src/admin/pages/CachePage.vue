<script setup lang="ts">
import { ref } from 'vue'
import { adminRequest, errorText } from '../api'
import Badge from '../components/Badge.vue'
import StateBlock from '../components/StateBlock.vue'
import { confirmAction } from '../confirm'
import { ago, bytes, duration, when } from '../format'
import { toast, toastError, usePoll } from '../state'
import type { CacheView } from '../types'

const view = ref<CacheView | null>(null)
const error = ref<string | null>(null)
const busy = ref(false)

async function load() {
  try {
    view.value = await adminRequest<CacheView>('/cache')
    error.value = null
  } catch (err) {
    error.value = errorText(err)
  }
}

const { refresh } = usePoll(load, 20_000)

async function act(action: 'clear' | 'rebuild' | 'refresh') {
  const prompts = {
    clear: { title: 'Clear the cache?', body: 'Every cached result is deleted. Visitors see no configs until the next scan finishes.', confirm: 'Clear', danger: true },
    rebuild: { title: 'Rebuild the cache?', body: 'The cache is cleared and a full scan starts now. Visitors see no configs until it finishes.', confirm: 'Rebuild', danger: true },
    refresh: null,
  }
  const prompt = prompts[action]
  if (prompt && !(await confirmAction(prompt))) return
  busy.value = true
  try {
    await adminRequest(`/cache/${action}`, { method: 'POST' })
    toast({ clear: 'Cache cleared', rebuild: 'Rebuild started', refresh: 'Reloaded from disk' }[action], 'ok')
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
    <h1>Cache</h1>
    <div class="actions">
      <button class="btn btn--sm" :disabled="busy" @click="act('refresh')">Refresh</button>
      <button class="btn btn--sm btn--danger" :disabled="busy" @click="act('rebuild')">Rebuild</button>
      <button class="btn btn--sm btn--danger" :disabled="busy" @click="act('clear')">Clear</button>
    </div>
  </div>
  <StateBlock :loading="!view && !error" :error="!view ? error : null" @retry="refresh">
    <template v-if="view">
      <div class="cards">
        <div class="card"><span class="card__label">TTL</span><span class="card__value">{{ duration(view.ttl_seconds) }}</span></div>
        <div class="card">
          <span class="card__label">Last scan</span>
          <span class="card__value">{{ ago(view.last_scan?.finished_at) }}</span>
          <span v-if="view.last_scan" class="card__lines"><RouterLink :to="`/admin/scans/${view.last_scan.id}`">#{{ view.last_scan.id }}</RouterLink></span>
        </div>
        <div class="card">
          <span class="card__label">Ranking</span>
          <span class="card__value">{{ view.use_probe_score ? 'Probe-aware' : 'Backend only' }}</span>
          <span class="card__lines"><span>top {{ view.top_count }} of {{ view.ranking.length }} candidates</span></span>
        </div>
      </div>

      <section class="section">
        <h2>Cache files</h2>
        <StateBlock :empty="!view.files.length" empty-text="No cache files: the next scan creates them.">
          <div class="table-wrap">
            <table class="table">
              <thead><tr><th>Query</th><th>State</th><th>Configs</th><th>Size</th><th>Created</th><th>Expires</th><th>Scan</th></tr></thead>
              <tbody>
                <tr v-for="f in view.files" :key="f.query">
                  <td class="mono">{{ f.query }}</td>
                  <td>
                    <Badge :tone="f.state === 'fresh' ? 'ok' : f.state === 'stale' ? 'warn' : 'muted'">{{ f.valid ? f.state : 'unreadable' }}</Badge>
                    <Badge v-if="f.scanning" tone="warn">scanning</Badge>
                  </td>
                  <td class="mono">{{ f.count ?? '—' }}</td>
                  <td class="mono">{{ bytes(f.size_bytes) }}</td>
                  <td>{{ when(f.created_ts) }}</td>
                  <td>{{ when(f.expires_ts) }}</td>
                  <td class="mono"><RouterLink v-if="f.scan_id" :to="`/admin/scans/${f.scan_id}`">#{{ f.scan_id }}</RouterLink></td>
                </tr>
              </tbody>
            </table>
          </div>
        </StateBlock>
      </section>

      <section class="section">
        <h2>Live ranking of the main cache</h2>
        <p class="muted" style="font-size: 14px; margin-bottom: 10px">
          Computed on every request from the cached scan and the current probe results: first by probe confirmations
          (providers that reached the endpoint), then by the backend's own order. The rows marked “top” are exactly what
          the public API serves.
        </p>
        <StateBlock :empty="!view.ranking.length" empty-text="No usable candidates in the cache.">
          <div class="table-wrap">
            <table class="table">
              <thead><tr><th>#</th><th></th><th>Endpoint</th><th>Label</th><th>Probe score</th><th>Confirmed by</th><th>Backend #</th><th>Latency</th><th>Speed</th></tr></thead>
              <tbody>
                <tr v-for="r in view.ranking" :key="r.fingerprint">
                  <td class="mono">{{ r.rank }}</td>
                  <td><Badge v-if="r.in_top" tone="ok">top</Badge></td>
                  <td class="mono">{{ r.address }}:{{ r.port }}</td>
                  <td>{{ r.label }}</td>
                  <td class="mono"><strong>{{ r.probe_score }}</strong>/{{ r.probe_total }}</td>
                  <td class="wrap">{{ r.probe_providers.join(', ') || '—' }}</td>
                  <td class="mono">{{ r.backend_rank }}</td>
                  <td class="mono">{{ r.latency_ms ? `${Math.round(r.latency_ms)} ms` : '—' }}</td>
                  <td class="mono">{{ r.speed_mbps ? `${r.speed_mbps.toFixed(1)}` : '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </StateBlock>
      </section>
    </template>
  </StateBlock>
</template>
