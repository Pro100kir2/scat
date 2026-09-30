<script setup lang="ts">
import { ref } from 'vue'
import { adminRequest, errorText } from '../api'
import Badge from '../components/Badge.vue'
import StateBlock from '../components/StateBlock.vue'
import { ago, bytes, duration, when } from '../format'
import { usePoll } from '../state'
import type { HealthView } from '../types'

const FRONTEND_VERSION = __APP_VERSION__

const health = ref<HealthView | null>(null)
const error = ref<string | null>(null)
const { refresh } = usePoll(async () => {
  try {
    health.value = await adminRequest<HealthView>('/health')
    error.value = null
  } catch (err) {
    error.value = errorText(err)
  }
}, 20_000)

const TONE = { ok: 'ok', warning: 'warn', error: 'bad' } as const
const HEADLINE = { ok: '🟢 System healthy', warning: '🟡 Needs attention', error: '🔴 Something is broken' }
</script>

<template>
  <div class="admin__top"><h1>System</h1></div>
  <StateBlock :loading="!health && !error" :error="!health ? error : null" @retry="refresh">
    <template v-if="health">
      <div class="health" :class="`health--${health.status}`" role="status">{{ HEADLINE[health.status] }}</div>

      <div class="cards section">
        <div v-for="(c, name) in health.components" :key="name" class="card">
          <span class="card__label">{{ name }}</span>
          <span><Badge :tone="TONE[c.status]">{{ c.status }}</Badge></span>
          <span class="card__lines"><span>{{ c.summary }}</span></span>
        </div>
      </div>

      <section class="section box">
        <dl class="facts">
          <div><dt>Backend</dt><dd class="mono">{{ health.versions.backend }}</dd></div>
          <div><dt>Frontend</dt><dd class="mono">{{ FRONTEND_VERSION }}</dd></div>
          <div><dt>Scanner logic</dt><dd class="mono">{{ health.versions.scanner }}</dd></div>
          <div><dt>Probe agent release</dt><dd class="mono">{{ health.versions.probe_agent }}</dd></div>
          <div><dt>Python</dt><dd class="mono">{{ health.versions.python }}</dd></div>
          <div><dt>Uptime</dt><dd>{{ duration(health.uptime_seconds) }}</dd></div>
          <div><dt>Memory (RSS)</dt><dd class="mono">{{ bytes(health.memory.rss_bytes ?? health.memory.max_rss_bytes) }}</dd></div>
          <div v-if="health.memory.total_bytes">
            <dt>Host memory available</dt>
            <dd class="mono">{{ bytes(health.memory.available_bytes) }} of {{ bytes(health.memory.total_bytes) }}</dd>
          </div>
          <div v-if="health.components.disk">
            <dt>Disk free</dt>
            <dd class="mono">{{ bytes(health.components.disk.free_bytes as number) }} of {{ bytes(health.components.disk.total_bytes as number) }}</dd>
          </div>
          <div><dt>Last successful scan</dt><dd>{{ when(health.last_successful_scan?.finished_at) }} ({{ ago(health.last_successful_scan?.finished_at) }})</dd></div>
        </dl>
      </section>

      <section class="section">
        <h2>Recent errors <RouterLink to="/admin/logs/?severity=error" class="muted" style="font-size: 14px">all →</RouterLink></h2>
        <StateBlock :empty="!health.recent_errors.length" empty-text="No errors recorded. 🎉">
          <div class="table-wrap">
            <table class="table">
              <tbody>
                <tr v-for="e in health.recent_errors" :key="e.id">
                  <td>{{ when(e.ts) }}</td><td>{{ e.component }}</td><td class="wrap">{{ e.message }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </StateBlock>
      </section>
    </template>
  </StateBlock>
</template>
