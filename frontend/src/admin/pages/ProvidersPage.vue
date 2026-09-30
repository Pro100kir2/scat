<script setup lang="ts">
import { ref } from 'vue'
import { adminRequest, errorText } from '../api'
import Badge from '../components/Badge.vue'
import StateBlock from '../components/StateBlock.vue'
import { ago, num } from '../format'
import { usePoll } from '../state'
import type { ProviderRow } from '../types'

const items = ref<ProviderRow[] | null>(null)
const error = ref<string | null>(null)

const { refresh } = usePoll(async () => {
  try {
    items.value = (await adminRequest<{ items: ProviderRow[] }>('/providers')).items
    error.value = null
  } catch (err) {
    error.value = errorText(err)
  }
}, 30_000)
</script>

<template>
  <div class="admin__top"><h1>Providers</h1></div>
  <p class="muted" style="margin: -8px 0 16px; font-size: 14px">
    A provider is an ISP; its probes run on devices inside that ISP's network. Each provider gives a config at most one
    point of probe confirmation, however many probes it has.
  </p>
  <StateBlock
    :loading="!items && !error" :error="!items ? error : null" :empty="items?.length === 0"
    empty-text="No providers yet." @retry="refresh"
  >
    <div v-if="items" class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>Provider</th><th>Probes</th><th>Approved / pending / disabled</th><th>Online</th>
            <th>Tests ok / failed</th><th>Errors</th><th>Last heartbeat</th><th>Last activity</th><th>Score contribution</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in items" :key="p.id">
            <td>
              <RouterLink :to="{ path: '/admin/probes/', query: { provider_id: p.id } }">{{ p.name }}</RouterLink>
              <span class="muted"> {{ p.country }}{{ p.region ? `, ${p.region}` : '' }}{{ p.asn ? ` · AS${p.asn}` : '' }}</span>
              <Badge v-if="!p.public" tone="muted">private</Badge>
            </td>
            <td class="mono">{{ p.probes_total }}</td>
            <td class="mono">{{ p.probes_approved }} / {{ p.probes_pending }} / {{ p.probes_disabled }}</td>
            <td><Badge :tone="p.probes_online ? 'ok' : 'muted'">{{ p.probes_online }} online</Badge></td>
            <td class="mono">{{ num(p.jobs_success) }} / {{ num(p.jobs_failed) }}</td>
            <td class="mono">{{ num(p.errors) }}</td>
            <td>{{ ago(p.last_heartbeat) }}</td>
            <td>{{ ago(p.last_activity) }}</td>
            <td class="mono">{{ p.score_contribution }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </StateBlock>
</template>
