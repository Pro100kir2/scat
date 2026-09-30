<script setup lang="ts">
import { reactive, ref } from 'vue'
import { adminRequest, errorText } from '../api'
import Badge from '../components/Badge.vue'
import StateBlock from '../components/StateBlock.vue'
import { confirmAction } from '../confirm'
import { ago, bytes, duration, num, when } from '../format'
import { toast, toastError, usePoll } from '../state'
import type { SourceRow, SourceTest } from '../types'

const sources = ref<SourceRow[] | null>(null)
const error = ref<string | null>(null)
const busy = ref<number | null>(null)
const results = ref<Record<number, SourceTest & { stored?: boolean }>>({})
const expanded = ref<number | null>(null)

const blank = () => ({ id: 0, name: '', url: '', enabled: true, priority: 100, refresh_seconds: 0, timeout_seconds: '' as number | '' })
const form = reactive(blank())
const formOpen = ref(false)
const formError = ref('')
const saving = ref(false)

async function load() {
  try {
    sources.value = (await adminRequest<{ items: SourceRow[] }>('/sources')).items
    error.value = null
  } catch (err) {
    error.value = errorText(err)
  }
}

const { refresh } = usePoll(load, 30_000)

function edit(source?: SourceRow) {
  Object.assign(form, source
    ? { id: source.id, name: source.name, url: source.url, enabled: source.enabled, priority: source.priority,
        refresh_seconds: source.refresh_seconds, timeout_seconds: source.timeout_seconds ?? '' }
    : blank())
  formError.value = ''
  formOpen.value = true
}

async function save() {
  formError.value = ''
  saving.value = true
  const body = {
    name: form.name.trim(), url: form.url.trim(), enabled: form.enabled, priority: Number(form.priority),
    refresh_seconds: Number(form.refresh_seconds), timeout_seconds: form.timeout_seconds === '' ? null : Number(form.timeout_seconds),
  }
  try {
    if (form.id) await adminRequest(`/sources/${form.id}`, { method: 'PATCH', body })
    else await adminRequest('/sources', { method: 'POST', body })
    toast(form.id ? 'Source updated' : 'Source added', 'ok')
    formOpen.value = false
    await load()
  } catch (err) {
    formError.value = errorText(err)
  } finally {
    saving.value = false
  }
}

async function toggle(source: SourceRow) {
  busy.value = source.id
  try {
    await adminRequest(`/sources/${source.id}`, { method: 'PATCH', body: { enabled: !source.enabled } })
    toast(`${source.name} ${source.enabled ? 'disabled' : 'enabled'}`, 'ok')
    await load()
  } catch (err) {
    toastError('Update', err)
  } finally {
    busy.value = null
  }
}

async function run(source: SourceRow, action: 'test' | 'fetch') {
  busy.value = source.id
  try {
    const result = await adminRequest<SourceTest>(`/sources/${source.id}/${action}`, { method: 'POST', timeoutMs: 330_000 })
    results.value[source.id] = { ...result, stored: action === 'fetch' }
    expanded.value = source.id
    toast(result.ok ? `${source.name}: ${result.valid} valid configs` : `${source.name}: ${result.error}`, result.ok ? 'ok' : 'bad')
    if (action === 'fetch') await load()
  } catch (err) {
    toastError(action === 'test' ? 'Test' : 'Fetch', err)
  } finally {
    busy.value = null
  }
}

async function remove(source: SourceRow) {
  const ok = await confirmAction({ title: 'Delete source?', body: `${source.name}\n${source.url}\n\nScans will no longer use it.`,
    confirm: 'Delete', danger: true })
  if (!ok) return
  try {
    await adminRequest(`/sources/${source.id}`, { method: 'DELETE' })
    toast('Source deleted', 'ok')
    await load()
  } catch (err) {
    toastError('Delete', err)
  }
}

const statusTone = (s: SourceRow) => (!s.enabled ? 'muted' : s.last_status === 'ok' ? 'ok' : s.last_status === 'error' ? 'bad' : 'info')
</script>

<template>
  <div class="admin__top">
    <h1>Sources</h1>
    <button class="btn btn--sm btn--primary" @click="edit()">Add source</button>
  </div>

  <form v-if="formOpen" class="box section" style="margin-top: 0; margin-bottom: 20px" @submit.prevent="save">
    <h2>{{ form.id ? 'Edit source' : 'New source' }}</h2>
    <div class="form-grid">
      <label class="wide">URL (https list, or a single vless:// link)
        <input v-model="form.url" class="input mono" required placeholder="https://raw.githubusercontent.com/…/vless.txt" />
      </label>
      <label>Name <input v-model="form.name" class="input" maxlength="80" placeholder="from the URL" /></label>
      <label>Priority (lower first) <input v-model.number="form.priority" class="input mono" type="number" min="0" max="100000" /></label>
      <label>Refresh interval, s (0 = every scan)
        <input v-model.number="form.refresh_seconds" class="input mono" type="number" min="0" max="604800" />
      </label>
      <label>Timeout, s (empty = default)
        <input v-model="form.timeout_seconds" class="input mono" type="number" min="1" max="300" step="1" />
      </label>
      <label style="align-self: end; display: flex; gap: 8px; align-items: center">
        <input v-model="form.enabled" type="checkbox" /> Enabled
      </label>
    </div>
    <p class="muted" style="font-size: 13px; margin-top: 10px">
      The server refuses addresses that resolve to private, loopback, link-local or metadata networks, and re-checks
      every redirect.
    </p>
    <p v-if="formError" class="error-text" role="alert">{{ formError }}</p>
    <div class="actions" style="margin-top: 12px">
      <button class="btn btn--sm btn--primary" type="submit" :disabled="saving">{{ saving ? 'Saving…' : 'Save' }}</button>
      <button class="btn btn--sm btn--quiet" type="button" @click="formOpen = false">Cancel</button>
    </div>
  </form>

  <StateBlock
    :loading="!sources && !error" :error="!sources ? error : null" :empty="sources?.length === 0"
    empty-text="No sources: scans have nothing to test. Add one above." @retry="refresh"
  >
    <div v-if="sources" class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>Source</th><th>Status</th><th>Priority</th><th>Refresh / timeout</th><th>Last fetch</th>
            <th>Configs (fetched / valid / unique)</th><th>Actions</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="s in sources" :key="s.id">
            <tr>
              <td class="wrap">
                <strong>{{ s.name }}</strong> <Badge tone="info">{{ s.kind }}</Badge><br />
                <span class="mono muted" style="font-size: 12px; word-break: break-all">{{ s.url }}</span>
              </td>
              <td class="wrap">
                <Badge :tone="statusTone(s)">{{ s.enabled ? s.last_status : 'disabled' }}</Badge>
                <div v-if="s.last_error" class="muted" style="font-size: 12px">{{ s.last_error }}</div>
              </td>
              <td class="mono">{{ s.priority }}</td>
              <td class="mono">{{ s.refresh_seconds ? duration(s.refresh_seconds) : 'every scan' }} · {{ s.timeout_seconds ?? 'default' }}{{ s.timeout_seconds ? 's' : '' }}</td>
              <td>{{ ago(s.last_fetch_at) }}<br /><span class="muted" style="font-size: 12px">ok {{ ago(s.last_success_at) }}</span></td>
              <td class="mono">{{ num(s.fetched_count) }} / {{ num(s.valid_count) }} / {{ num(s.unique_count) }}</td>
              <td>
                <div class="actions">
                  <button class="btn btn--sm btn--quiet" :disabled="busy === s.id" @click="run(s, 'test')">Test</button>
                  <button class="btn btn--sm btn--quiet" :disabled="busy === s.id" @click="run(s, 'fetch')">Fetch now</button>
                  <button class="btn btn--sm btn--quiet" @click="expanded = expanded === s.id ? null : s.id">Last result</button>
                  <button class="btn btn--sm btn--quiet" @click="edit(s)">Edit</button>
                  <button class="btn btn--sm btn--quiet" :disabled="busy === s.id" @click="toggle(s)">{{ s.enabled ? 'Disable' : 'Enable' }}</button>
                  <button class="btn btn--sm btn--danger" @click="remove(s)">Delete</button>
                </div>
              </td>
            </tr>
            <tr v-if="expanded === s.id">
              <td colspan="7" class="wrap">
                <template v-if="results[s.id]">
                  <strong>{{ results[s.id]!.stored ? 'Fetched now' : 'Test (not stored)' }}:</strong>
                  {{ results[s.id]!.ok ? 'OK' : results[s.id]!.error }} ·
                  {{ results[s.id]!.fetched }} links, {{ results[s.id]!.valid }} valid, {{ results[s.id]!.unique }} unique ·
                  {{ bytes(results[s.id]!.bytes) }} in {{ results[s.id]!.duration_ms }} ms
                  <span v-if="results[s.id]!.redirects.length"> · redirects: {{ results[s.id]!.redirects.join(' → ') }}</span>
                  <div v-for="line in results[s.id]!.sample" :key="line" class="mono muted" style="font-size: 12px">{{ line }}</div>
                </template>
                <template v-else-if="s.last_result">
                  <strong>Last recorded:</strong> {{ when(s.last_result.at) }}{{ s.last_result.cached ? ' (reused cached copy)' : '' }}
                  <span v-if="s.last_result.bytes"> · {{ bytes(s.last_result.bytes) }}</span>
                  <span v-if="s.last_result.duration_ms"> · {{ s.last_result.duration_ms }} ms</span>
                  <div v-for="line in s.last_result.sample ?? []" :key="line" class="mono muted" style="font-size: 12px">{{ line }}</div>
                </template>
                <span v-else class="muted">Not fetched yet.</span>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </StateBlock>
</template>
