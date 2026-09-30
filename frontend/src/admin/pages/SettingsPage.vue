<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { adminRequest, errorText } from '../api'
import Badge from '../components/Badge.vue'
import StateBlock from '../components/StateBlock.vue'
import { confirmAction } from '../confirm'
import { toast, toastError } from '../state'
import type { SettingItem } from '../types'

const items = ref<SettingItem[] | null>(null)
const error = ref<string | null>(null)
const draft = reactive<Record<string, string | boolean>>({})
const saving = ref(false)
const formError = ref('')

function fill(list: SettingItem[]) {
  items.value = list
  for (const s of list) draft[s.key] = s.type === 'bool' ? Boolean(s.value) : String(s.value)
}

async function load() {
  try {
    fill((await adminRequest<{ items: SettingItem[] }>('/settings')).items)
    error.value = null
  } catch (err) {
    error.value = errorText(err)
  }
}
onMounted(load)

const categories = computed(() => {
  const groups: Record<string, SettingItem[]> = {}
  for (const s of items.value ?? []) (groups[s.category] ??= []).push(s)
  return groups
})

const changed = computed(() => (items.value ?? []).filter((s) =>
  s.type === 'bool' ? draft[s.key] !== s.value : Number(draft[s.key]) !== Number(s.value)))

async function save() {
  formError.value = ''
  const body = Object.fromEntries(changed.value.map((s) => [s.key, s.type === 'bool' ? draft[s.key] : Number(draft[s.key])]))
  saving.value = true
  try {
    fill((await adminRequest<{ items: SettingItem[] }>('/settings', { method: 'PUT', body })).items)
    toast(`Saved ${Object.keys(body).length} setting(s)`, 'ok')
  } catch (err) {
    formError.value = errorText(err)
  } finally {
    saving.value = false
  }
}

async function reset(s: SettingItem) {
  if (!(await confirmAction({ title: `Reset ${s.key}?`, body: `Back to the environment default: ${s.default}.`, confirm: 'Reset' }))) return
  try {
    fill((await adminRequest<{ items: SettingItem[] }>(`/settings/${s.key}`, { method: 'DELETE' })).items)
    toast(`${s.key} reset`, 'ok')
  } catch (err) {
    toastError('Reset', err)
  }
}
</script>

<template>
  <div class="admin__top">
    <h1>Settings</h1>
    <button class="btn btn--sm btn--primary" :disabled="saving || !changed.length" @click="save">
      {{ saving ? 'Saving…' : changed.length ? `Save ${changed.length} change(s)` : 'No changes' }}
    </button>
  </div>
  <p class="muted" style="margin: -8px 0 16px; font-size: 14px">
    Safe runtime settings, validated on the server and applied immediately. Secrets and network/trust settings
    (admin token, origins, proxy trust, public URL, paths) are not here: they stay in <code>.env</code>.
  </p>
  <p v-if="formError" class="error-text" role="alert">{{ formError }}</p>
  <StateBlock :loading="!items && !error" :error="!items ? error : null" @retry="load">
    <section v-for="(list, category) in categories" :key="category" class="section">
      <h2 style="text-transform: capitalize">{{ category }}</h2>
      <div class="table-wrap">
        <table class="table">
          <tbody>
            <tr v-for="s in list" :key="s.key">
              <td class="wrap">
                <label :for="`set-${s.key}`">{{ s.label }}</label><br />
                <span class="mono muted" style="font-size: 12px">{{ s.key }}</span>
                <Badge v-if="s.overridden" tone="info">overridden</Badge>
                <Badge v-if="s.restart_required" tone="warn">applies after restart</Badge>
              </td>
              <td style="width: 200px">
                <input v-if="s.type === 'bool'" :id="`set-${s.key}`" v-model="draft[s.key]" type="checkbox" />
                <input
                  v-else :id="`set-${s.key}`" v-model="draft[s.key]" class="input mono" type="number"
                  :min="s.min ?? undefined" :max="s.max ?? undefined" :step="s.type === 'float' ? 0.1 : 1"
                />
              </td>
              <td class="muted" style="font-size: 12px">
                default {{ s.default }}<template v-if="s.min !== null"> · {{ s.min }}…{{ s.max }}</template>
              </td>
              <td><button v-if="s.overridden" class="btn btn--sm btn--quiet" @click="reset(s)">Reset</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </StateBlock>
</template>
