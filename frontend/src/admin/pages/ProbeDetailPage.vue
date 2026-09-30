<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { adminRequest, errorText } from '../api'
import Badge from '../components/Badge.vue'
import Pager from '../components/Pager.vue'
import StateBlock from '../components/StateBlock.vue'
import { confirmAction } from '../confirm'
import { ago, kb, LIFECYCLE, reasonText, when } from '../format'
import { markRead, toast, toastError, usePoll } from '../state'
import type { JobRow, Paged, ProbeDetail, Verification } from '../types'

const props = defineProps<{ id: string }>()
const router = useRouter()
const probe = ref<ProbeDetail | null>(null)
const error = ref<string | null>(null)
const verifications = ref<Verification[]>([])
const jobs = ref<Paged<JobRow> | null>(null)
const jobPage = ref(1)
const onlyErrors = ref(false)
const busy = ref(false)
const editingPhone = ref(false)
const phone = ref('')

async function load() {
  try {
    const [p, v, j] = await Promise.all([
      adminRequest<ProbeDetail>(`/probes/${props.id}`),
      adminRequest<{ items: Verification[] }>(`/probes/${props.id}/verifications`),
      adminRequest<Paged<JobRow>>(`/probes/${props.id}/jobs`, { query: { page: jobPage.value, errors: onlyErrors.value } }),
    ])
    probe.value = p
    verifications.value = v.items
    jobs.value = j
    error.value = null
  } catch (err) {
    error.value = (err as { status?: number }).status === 404 ? 'This probe does not exist (deleted?).' : errorText(err)
  }
}

const running = computed(() => probe.value?.verification.state === 'running')
const { refresh } = usePoll(load, () => (running.value ? 3_000 : 15_000))
watch([jobPage, onlyErrors], () => void refresh())
onMounted(() => void markRead([props.id]).catch(() => undefined))

async function act(action: string, opts: { title: string; body: string; confirm: string; danger?: boolean; done: string }) {
  if (!(await confirmAction(opts))) return
  busy.value = true
  try {
    await adminRequest(`/probes/${props.id}/${action}`, { method: 'POST' })
    toast(opts.done, 'ok')
    await load()
  } catch (err) {
    toastError(opts.confirm, err)
  } finally {
    busy.value = false
  }
}

async function remove() {
  const ok = await confirmAction({
    title: 'Delete probe?',
    body: 'This probe will no longer be allowed to participate in scanning. Its credentials stop working at once and its jobs, results and verification history are removed. The device must be re-enrolled to come back.',
    confirm: 'Delete', danger: true,
  })
  if (!ok) return
  busy.value = true
  try {
    await adminRequest(`/probes/${props.id}`, { method: 'DELETE' })
    toast(`Probe ${props.id} deleted`, 'ok')
    await router.push('/admin/probes/')
  } catch (err) {
    toastError('Delete', err)
  } finally {
    busy.value = false
  }
}

async function savePhone() {
  try {
    probe.value = await adminRequest<ProbeDetail>(`/probes/${props.id}`, { method: 'PATCH', body: { contact_phone: phone.value.trim() || null } })
    editingPhone.value = false
    toast('Contact saved', 'ok')
  } catch (err) {
    toastError('Save contact', err)
  }
}

const OUTCOME_TONE: Record<string, 'ok' | 'warn' | 'bad'> = { SUCCESS: 'ok', FAILED: 'bad', TIMEOUT: 'warn', ERROR: 'bad' }
</script>

<template>
  <div class="admin__top">
    <div>
      <RouterLink to="/admin/probes/" class="muted">← Probes</RouterLink>
      <h1 class="mono" style="font-family: var(--font-mono); font-size: 26px">{{ id }}</h1>
    </div>
    <div v-if="probe" class="actions">
      <button
        v-if="probe.status !== 'approved' && probe.status !== 'disabled'" class="btn btn--sm" :disabled="busy"
        @click="act('approve', { title: 'Approve without verification?', body: 'Its results will count immediately, without the automatic proof that it runs jobs. Prefer “Re-run verification”.', confirm: 'Approve', done: 'Probe approved' })"
      >
        Approve
      </button>
      <button
        v-if="probe.status !== 'disabled'" class="btn btn--sm" :disabled="busy || running"
        @click="act('verify', { title: 'Re-run verification?', body: 'The probe gets a new one-time check. If it fails, an approved probe goes back to pending until it passes.', confirm: 'Re-run', done: 'Verification started' })"
      >
        Re-run verification
      </button>
      <button
        v-if="probe.status !== 'disabled'" class="btn btn--sm" :disabled="busy"
        @click="act('test', { title: 'Send a test job?', body: 'The probe will run one control check (HTTP GET to the control URL). The result shows in its jobs below.', confirm: 'Send', done: 'Test job queued' })"
      >
        Send test job
      </button>
      <button
        v-if="probe.status === 'disabled'" class="btn btn--sm" :disabled="busy"
        @click="act('enable', { title: 'Enable probe?', body: 'It becomes pending and is verified again automatically before its results count.', confirm: 'Enable', done: 'Probe enabled' })"
      >
        Enable
      </button>
      <button
        v-else class="btn btn--sm btn--danger" :disabled="busy"
        @click="act('disable', { title: 'Disable probe?', body: 'It stops receiving jobs, its open jobs are withdrawn and its results stop counting. The device gets 403 until enabled again.', confirm: 'Disable', danger: true, done: 'Probe disabled' })"
      >
        Disable
      </button>
      <button class="btn btn--sm btn--danger" :disabled="busy" @click="remove">Delete</button>
    </div>
  </div>

  <StateBlock :loading="!probe && !error" :error="!probe ? error : null" @retry="refresh">
    <template v-if="probe">
      <div class="actions" style="margin-bottom: 16px">
        <Badge :tone="LIFECYCLE[probe.lifecycle]?.tone">{{ LIFECYCLE[probe.lifecycle]?.label }}</Badge>
        <Badge :tone="probe.online ? 'ok' : 'muted'">{{ probe.online ? 'online' : 'offline' }}</Badge>
        <Badge tone="info">status: {{ probe.status }}</Badge>
        <Badge tone="info">verification: {{ probe.verification.state }}</Badge>
      </div>
      <p v-if="probe.verification.reason" class="box" style="border-color: rgba(236,154,134,.4)">
        <strong>Verification failed:</strong> {{ reasonText(probe.verification.reason) }}
        <span class="muted mono"> ({{ probe.verification.reason }}, attempt {{ probe.verification.attempts }})</span>
      </p>

      <div class="cards section">
        <div class="card">
          <span class="card__label">Contact</span>
          <template v-if="!editingPhone">
            <span>Phone: <span class="mono">{{ probe.contact_phone ?? 'not given' }}</span></span>
            <button
              class="btn btn--sm btn--quiet" style="justify-self: start"
              @click="phone = probe.contact_phone ?? ''; editingPhone = true"
            >
              Edit
            </button>
          </template>
          <form v-else class="stack" @submit.prevent="savePhone">
            <input v-model="phone" class="input mono" type="tel" placeholder="+7 912 345-67-89" aria-label="Contact phone" />
            <div class="actions">
              <button class="btn btn--sm btn--primary" type="submit">Save</button>
              <button class="btn btn--sm btn--quiet" type="button" @click="editingPhone = false">Cancel</button>
            </div>
          </form>
        </div>
        <div class="card">
          <span class="card__label">Jobs</span>
          <span class="card__value">{{ probe.jobs_done }}</span>
          <span class="card__lines">
            <span>{{ probe.jobs_success }} success · {{ probe.jobs_failed }} failed</span>
            <span>{{ probe.jobs_error }} errors · {{ probe.jobs_expired }} expired · {{ probe.open_jobs }} open</span>
          </span>
        </div>
        <div class="card">
          <span class="card__label">Score contribution</span>
          <span class="card__value">{{ probe.score_contribution }}</span>
          <span class="card__lines"><span>current targets confirmed by its provider</span></span>
        </div>
      </div>

      <section class="section box">
        <dl class="facts">
          <div><dt>Provider</dt><dd>{{ probe.provider }} ({{ probe.country }}) {{ probe.region ?? '' }}</dd></div>
          <div><dt>ASN</dt><dd class="mono">{{ probe.asn ?? '—' }}</dd></div>
          <div><dt>Public IP</dt><dd class="mono">{{ probe.public_ip ?? '—' }}</dd></div>
          <div><dt>Enrolled from</dt><dd class="mono">{{ probe.enrollment.ip ?? '—' }}</dd></div>
          <div><dt>Profile</dt><dd class="mono">{{ probe.profile }} ({{ probe.platform }})</dd></div>
          <div><dt>Agent version</dt><dd class="mono">{{ probe.version }}</dd></div>
          <div><dt>OS</dt><dd>{{ probe.os ?? '—' }}</dd></div>
          <div><dt>Architecture</dt><dd class="mono">{{ probe.arch ?? '—' }}</dd></div>
          <div><dt>CPU</dt><dd>{{ probe.cpu ?? '—' }}{{ probe.cpu_cores ? ` × ${probe.cpu_cores}` : '' }}</dd></div>
          <div><dt>RAM</dt><dd>{{ kb(probe.mem_total_kb) }} ({{ kb(probe.mem_available_kb) }} free)</dd></div>
          <div><dt>Storage</dt><dd>{{ kb(probe.storage_free_kb) }} free of {{ kb(probe.storage_total_kb) }}</dd></div>
          <div><dt>Control check</dt><dd>{{ probe.control_ok === null ? '—' : probe.control_ok ? 'passing' : 'failing' }}</dd></div>
          <div><dt>Registered</dt><dd>{{ when(probe.created_at) }}</dd></div>
          <div><dt>Last heartbeat</dt><dd>{{ ago(probe.last_heartbeat) }}</dd></div>
          <div><dt>Last job</dt><dd>{{ ago(probe.last_job_at) }}</dd></div>
          <div><dt>Last successful job</dt><dd>{{ ago(probe.last_success_at) }}</dd></div>
          <div><dt>Last error</dt><dd>{{ probe.last_error ?? '—' }} <span class="muted">{{ probe.last_error_at ? ago(probe.last_error_at) : '' }}</span></dd></div>
          <div v-if="probe.enrollment.info" style="grid-column: 1 / -1"><dt>Notes from the form</dt><dd>{{ probe.enrollment.info }}</dd></div>
        </dl>
      </section>

      <section class="section">
        <h2>Verification history</h2>
        <StateBlock :empty="!verifications.length" empty-text="No verification has run yet.">
          <div class="table-wrap">
            <table class="table">
              <thead><tr><th>Started</th><th>Trigger</th><th>Result</th><th>Checks</th></tr></thead>
              <tbody>
                <tr v-for="v in verifications" :key="v.id">
                  <td>{{ when(v.started_at) }}</td>
                  <td>{{ v.trigger }}</td>
                  <td class="wrap">
                    <Badge :tone="v.state === 'passed' ? 'ok' : v.state === 'running' ? 'warn' : v.state === 'failed' ? 'bad' : 'muted'">{{ v.state }}</Badge>
                    <span v-if="v.reason"> {{ v.reason_text }}</span>
                  </td>
                  <td class="wrap">
                    <div v-for="c in v.detail?.checks ?? []" :key="c.purpose + c.type" class="mono" style="font-size: 12px">
                      {{ c.purpose }} {{ c.type }}: {{ c.outcome ?? c.status }}{{ c.http_status ? ` (${c.http_status})` : '' }}{{ c.error ? ` ${c.error}` : '' }}
                      <template v-if="c.request_seen !== null"> · request {{ c.request_seen ? 'seen' : 'NOT seen' }}</template>
                      <template v-if="c.request_ip_matches === false"> · from another IP</template>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </StateBlock>
      </section>

      <section class="section">
        <div class="admin__top" style="margin-bottom: 10px">
          <h2 style="margin: 0">Jobs</h2>
          <label class="actions" style="align-items: center; font-size: 14px">
            <input v-model="onlyErrors" type="checkbox" @change="jobPage = 1" /> Errors only
          </label>
        </div>
        <StateBlock :empty="jobs?.total === 0" empty-text="No jobs in the last 24 hours.">
          <div v-if="jobs" class="table-wrap">
            <table class="table">
              <thead><tr><th>Created</th><th>Purpose</th><th>Check</th><th>Status</th><th>Outcome</th><th>Latency</th></tr></thead>
              <tbody>
                <tr v-for="j in jobs.items" :key="j.id">
                  <td>{{ when(j.created_at) }}</td>
                  <td>{{ j.purpose }}</td>
                  <td class="mono" style="font-size: 12px">{{ j.key.split('|').slice(0, 4).join(' ') }}</td>
                  <td>{{ j.status }}<span v-if="j.attempts > 1" class="muted"> ×{{ j.attempts }}</span></td>
                  <td>
                    <Badge v-if="j.outcome" :tone="OUTCOME_TONE[j.outcome]">{{ j.outcome }}</Badge>
                    <span class="muted"> {{ j.error ?? '' }}{{ j.http_status ? ` HTTP ${j.http_status}` : '' }}</span>
                  </td>
                  <td class="mono">{{ j.latency_ms ? `${Math.round(j.latency_ms)} ms` : '' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <Pager v-if="jobs" v-model:page="jobPage" :per-page="jobs.per_page" :total="jobs.total" />
        </StateBlock>
      </section>
    </template>
  </StateBlock>
</template>
