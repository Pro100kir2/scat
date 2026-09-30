<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { adminRequest, errorText } from '../api'
import Badge from '../components/Badge.vue'
import Pager from '../components/Pager.vue'
import StateBlock from '../components/StateBlock.vue'
import { ago, kb, LIFECYCLE, reasonText } from '../format'
import { admin, markRead, toastError, usePoll } from '../state'
import type { ProbesPage } from '../types'

const route = useRoute()
const router = useRouter()
const q = (key: string) => (typeof route.query[key] === 'string' ? (route.query[key] as string) : '')

// Filters live in the URL, so a filtered view can be reloaded or shared with another admin.
const filters = reactive({
  status: q('status'), online: q('online'), verification: q('verification'), profile: q('profile'),
  version: q('version'), provider_id: q('provider_id'), q: q('q'), sort: q('sort') || 'created_at', order: q('order') || 'desc',
  page: Number(q('page')) || 1, per_page: 25,
})
const data = ref<ProbesPage | null>(null)
const error = ref<string | null>(null)
const search = ref(filters.q)

async function load() {
  try {
    data.value = await adminRequest<ProbesPage>('/probes', { query: { ...filters } })
    error.value = null
  } catch (err) {
    error.value = errorText(err)
  }
}

const { refresh } = usePoll(load, 20_000)

watch(() => ({ ...filters }), (f) => {
  void router.replace({ query: Object.fromEntries(Object.entries(f).filter(([k, v]) => v !== '' && k !== 'per_page'
    && !(k === 'page' && v === 1) && !(k === 'sort' && v === 'created_at') && !(k === 'order' && v === 'desc'))) })
  void refresh()
})

let searchTimer: ReturnType<typeof setTimeout> | undefined
watch(search, (value) => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => Object.assign(filters, { q: value.trim(), page: 1 }), 350)
})

function sortBy(column: string) {
  if (filters.sort === column) filters.order = filters.order === 'desc' ? 'asc' : 'desc'
  else Object.assign(filters, { sort: column, order: 'desc' })
}
const arrow = (column: string) => (filters.sort === column ? (filters.order === 'desc' ? ' ↓' : ' ↑') : '')
const setFilter = (key: keyof typeof filters, value: string) => Object.assign(filters, { [key]: value, page: 1 })

async function readAll() {
  try {
    await markRead()
    await load()
  } catch (err) {
    toastError('Mark as read', err)
  }
}
</script>

<template>
  <div class="admin__top">
    <h1>Probes</h1>
    <button v-if="admin.unread" class="btn btn--sm" @click="readAll">Mark {{ admin.unread }} new as read</button>
  </div>

  <p v-if="filters.provider_id" class="actions" style="margin-bottom: 10px">
    <span class="badge badge--info">provider {{ data?.items[0]?.provider ?? filters.provider_id }}</span>
    <button class="btn btn--sm btn--quiet" @click="setFilter('provider_id', '')">✕ all providers</button>
  </p>
  <div class="toolbar" role="search">
    <label>Search ID or phone
      <input v-model="search" class="input mono" type="search" placeholder="prb_… or +7912…" aria-label="Search by probe ID or phone" />
    </label>
    <label>Status
      <select class="input" :value="filters.status" @change="setFilter('status', ($event.target as HTMLSelectElement).value)">
        <option value="">Any</option><option value="approved">Approved</option>
        <option value="pending">Pending</option><option value="disabled">Disabled</option>
      </select>
    </label>
    <label>Online
      <select class="input" :value="filters.online" @change="setFilter('online', ($event.target as HTMLSelectElement).value)">
        <option value="">Any</option><option value="true">Online</option><option value="false">Offline</option>
      </select>
    </label>
    <label>Verification
      <select class="input" :value="filters.verification" @change="setFilter('verification', ($event.target as HTMLSelectElement).value)">
        <option value="">Any</option><option value="running">Running</option><option value="passed">Passed</option>
        <option value="failed">Failed</option><option value="none">Not started</option><option value="skipped">Skipped</option>
      </select>
    </label>
    <label>Profile
      <select class="input" :value="filters.profile" @change="setFilter('profile', ($event.target as HTMLSelectElement).value)">
        <option value="">Any</option>
        <option v-for="p in data?.facets.profiles ?? []" :key="p" :value="p">{{ p }}</option>
      </select>
    </label>
    <label>Version
      <select class="input" :value="filters.version" @change="setFilter('version', ($event.target as HTMLSelectElement).value)">
        <option value="">Any</option>
        <option v-for="v in data?.facets.versions ?? []" :key="v" :value="v">{{ v }}</option>
      </select>
    </label>
  </div>

  <StateBlock
    :loading="!data && !error" :error="!data ? error : null" :empty="data?.total === 0"
    empty-text="No probes match these filters." @retry="refresh"
  >
    <div v-if="data" class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th scope="col"><button @click="sortBy('id')">Probe{{ arrow('id') }}</button></th>
            <th scope="col">State</th>
            <th scope="col">Provider</th>
            <th scope="col">Profile / version</th>
            <th scope="col">Device</th>
            <th scope="col">Network · IP · contact</th>
            <th scope="col"><button @click="sortBy('created_at')">Registered{{ arrow('created_at') }}</button></th>
            <th scope="col"><button @click="sortBy('last_heartbeat')">Heartbeat{{ arrow('last_heartbeat') }}</button></th>
            <th scope="col"><button @click="sortBy('last_activity')">Activity{{ arrow('last_activity') }}</button></th>
            <th scope="col"><button @click="sortBy('jobs_done')">Jobs{{ arrow('jobs_done') }}</button></th>
            <th scope="col"><button @click="sortBy('error_count')">Errors{{ arrow('error_count') }}</button></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in data.items" :key="p.id" :class="{ 'row--new': p.is_new }">
            <td class="mono">
              <RouterLink :to="`/admin/probes/${p.id}`">{{ p.id }}</RouterLink>
              <Badge v-if="p.is_new" tone="bad">new</Badge>
            </td>
            <td>
              <Badge :tone="LIFECYCLE[p.lifecycle]?.tone">{{ LIFECYCLE[p.lifecycle]?.label ?? p.lifecycle }}</Badge>
              <Badge :tone="p.online ? 'ok' : 'muted'">{{ p.online ? 'online' : 'offline' }}</Badge>
              <div v-if="p.verification.reason" class="muted" style="font-size: 12px; white-space: normal; max-width: 260px">
                {{ reasonText(p.verification.reason) }}
              </div>
            </td>
            <td>{{ p.provider }} <span class="muted">{{ p.country }}</span></td>
            <td class="mono">{{ p.profile }}<br /><span class="muted">{{ p.version }}</span></td>
            <td>
              {{ p.os ?? '—' }} <span class="muted">{{ p.arch }}</span><br />
              <span class="muted" style="font-size: 12px">
                {{ p.cpu ?? '' }}{{ p.cpu_cores ? ` ×${p.cpu_cores}` : '' }} · RAM {{ kb(p.mem_total_kb) }} · free {{ kb(p.storage_free_kb) }}
              </span>
            </td>
            <td class="mono">
              <span :title="p.as_name ?? p.network_error ?? ''">{{ p.asn ? `AS${p.asn}` : '—' }}</span>
              <span class="muted">{{ p.as_name ?? '' }}</span><br />
              {{ p.public_ip ?? '—' }} <span class="muted">{{ p.contact_phone ?? '' }}</span>
            </td>
            <td>{{ ago(p.created_at) }}</td>
            <td>{{ ago(p.last_heartbeat) }}</td>
            <td>{{ ago(p.last_activity) }}</td>
            <td class="mono">{{ p.jobs_done }} <span class="muted">({{ p.jobs_success }} ok · {{ p.jobs_failed }} fail)</span></td>
            <td class="mono" :title="p.last_error ?? ''">
              {{ p.error_count }}<span v-if="p.last_error" class="muted"> · {{ p.last_error }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <Pager v-if="data" v-model:page="filters.page" :per-page="filters.per_page" :total="data.total" />
  </StateBlock>
</template>
