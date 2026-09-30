<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { fetchEnrollment } from '@/services/api'
import type { EnrollmentState, EnrollmentStatus } from '@/types/api'
import { checkLabel, issueText, verificationView } from '@/utils/probe'

/**
 * Live progress of an installation, from the website's side: the installer reports to the
 * server, the page polls the server. Drawn as a descent line with five stations; once the
 * probe exists, a banner says in plain words where automatic verification stands.
 */
const props = withDefaults(defineProps<{ enrollmentId: string; token: string; pollMs?: number }>(), { pollMs: 3000 })
const emit = defineEmits<{ state: [state: EnrollmentState] }>()

const status = shallowRef<EnrollmentStatus | null>(null)
const lost = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let stopped = false
let polls = 0
// Installs take a few minutes; an abandoned tab should not poll every 3 s forever.
const nextDelay = () => (polls > 200 ? Math.max(props.pollMs, 15_000) : props.pollMs)

async function poll() {
  if (typeof document !== 'undefined' && document.hidden) {
    timer = setTimeout(poll, props.pollMs)
    return
  }
  polls += 1
  try {
    status.value = await fetchEnrollment(props.enrollmentId, props.token)
    lost.value = false
    emit('state', status.value.state)
  } catch (err) {
    // 404: the enrollment expired or was never ours; anything else is a blip.
    lost.value = (err as { status?: number }).status === 404
  }
  const settled = view.value?.final ?? false
  if (!stopped && !settled && !lost.value) timer = setTimeout(poll, nextDelay())
}

onMounted(poll)
onBeforeUnmount(() => {
  stopped = true
  clearTimeout(timer)
})

type Station = 'done' | 'active' | 'failed' | 'pending'
const state = computed<EnrollmentState>(() => status.value?.state ?? 'waiting')
const diag = computed(() => status.value?.diagnostics ?? null)

const view = computed(() => (status.value ? verificationView(status.value) : null))

const stations = computed<{ title: string; text: string; mark: Station }[]>(() => {
  const s = state.value
  const v = status.value?.verification
  const order: EnrollmentState[] = ['waiting', 'installing', 'registered', 'verifying', 'verified']
  const reached = s === 'unsupported' ? 1 : s === 'verification_failed' || s === 'disabled' ? 3 : order.indexOf(s)
  const mark = (i: number): Station => (i < reached ? 'done' : i === reached ? 'active' : 'pending')
  return [
    {
      title: 'Диагностика',
      text: reached > 0 ? 'Устройство проверено' : 'Ждём, когда команда будет запущена на устройстве',
      mark: mark(0),
    },
    {
      title: 'Совместимость',
      text: s === 'unsupported'
        ? 'Устройство не подходит. Ничего не установлено.'
        : reached > 1 ? `Подходит: ${diag.value?.profile_name ?? diag.value?.profile ?? ''}` : 'Сервер сравнит устройство с требованиями',
      mark: s === 'unsupported' ? 'failed' : mark(1),
    },
    {
      title: 'Установка и регистрация',
      text: reached > 2 ? 'Зонд установлен и получил свой ID' : 'Загрузка, проверка SHA-256, установка, служба автозапуска',
      mark: s === 'unsupported' ? 'pending' : mark(2),
    },
    {
      title: 'Автоматическая проверка',
      text: s === 'verification_failed' ? 'Проверка не пройдена'
        : s === 'disabled' ? 'Зонд отключён'
          : reached > 3 ? 'Зонд выполнил проверочное задание'
            : s === 'verifying' ? (v?.online ? 'Зонд на связи, выполняет проверочное задание' : 'Ждём, когда зонд выйдет на связь')
              : 'Зонд получит проверочное задание',
      mark: s === 'verification_failed' || s === 'disabled' ? 'failed' : s === 'unsupported' ? 'pending' : mark(3),
    },
    {
      title: 'В работе',
      text: s === 'verified' ? (v?.online ? 'Участвует в проверке VLESS-конфигураций' : 'Проверен, но сейчас не на связи')
        : 'Начнёт получать задания сразу после проверки',
      mark: s === 'verified' ? (v?.online ? 'done' : 'failed') : 'pending',
    },
  ]
})

const summary = computed(() => {
  const s = diag.value?.summary
  if (!s) return []
  const mb = (kb: unknown) => (typeof kb === 'number' ? `${Math.round(kb / 1024)} МБ` : '—')
  return [
    ['Система', [s.distro || s.os, s.openwrt_version && !String(s.distro).includes(String(s.openwrt_version)) ? `OpenWrt ${s.openwrt_version}` : null].filter(Boolean).join(', ')],
    ['Процессор', [s.machine, s.cpu_model, s.cpu_cores ? `${s.cpu_cores} ядра` : null].filter(Boolean).join(', ')],
    ['Память', `${mb(s.mem_total_kb)} всего, ${mb(s.mem_available_kb)} свободно`],
    ['Место', `${mb(s.storage_free_kb)} свободно`],
    ['Автозапуск', s.init || '—'],
  ].filter(([, v]) => v) as [string, string][]
})
</script>

<template>
  <section class="status" aria-live="polite" aria-label="Ход установки">
    <ol class="line">
      <li v-for="st in stations" :key="st.title" class="station" :class="`is-${st.mark}`">
        <span class="station__mark" aria-hidden="true"></span>
        <div>
          <p class="station__title">{{ st.title }}</p>
          <p class="station__text">{{ st.text }}</p>
        </div>
      </li>
    </ol>

    <p v-if="lost" class="status__lost">
      Эта заявка истекла или не найдена. Создайте новую: токен действует 24 часа и подходит для одной установки.
    </p>

    <div v-if="view" class="banner" :class="`banner--${view.tone}`" role="status">
      <p class="banner__title"><span aria-hidden="true">{{ view.icon }}</span> {{ view.title }}</p>
      <p v-for="line in view.lines" :key="line">{{ line }}</p>
      <template v-if="view.reason">
        <p class="banner__label">Причина</p>
        <p>{{ view.reason }}</p>
      </template>
      <template v-if="view.todo?.length">
        <p class="banner__label">Что делать</p>
        <ul>
          <li v-for="item in view.todo" :key="item">{{ item }}</li>
        </ul>
      </template>
      <RouterLink v-if="view.tone === 'ok'" class="btn btn--sm" to="/providers/">К списку провайдеров</RouterLink>
    </div>

    <details v-if="status?.probe" class="checks">
      <summary>Технические подробности</summary>
      <dl class="checks__summary">
        <div><dt>ID зонда</dt><dd class="mono">{{ status.probe.id }}</dd></div>
        <div><dt>Состояние</dt><dd class="mono">{{ state }}</dd></div>
        <div><dt>Статус</dt><dd class="mono">{{ status.probe.status }}</dd></div>
        <div><dt>Проверка</dt><dd class="mono">{{ status.verification?.state ?? '—' }}</dd></div>
        <div v-if="status.verification?.reason"><dt>Код причины</dt><dd class="mono">{{ status.verification.reason }}</dd></div>
        <div><dt>Попыток проверки</dt><dd class="mono">{{ status.verification?.attempts ?? 0 }}</dd></div>
        <div><dt>На связи</dt><dd>{{ status.verification?.online ? 'да' : 'нет' }}</dd></div>
        <div v-if="status.probe.version"><dt>Версия зонда</dt><dd class="mono">{{ status.probe.version }}</dd></div>
      </dl>
    </details>

    <div v-if="state === 'unsupported' && diag" class="status__fail">
      <p class="status__fail-title">Почему не подходит</p>
      <ul>
        <li v-for="r in diag.reasons" :key="r.code">{{ issueText(r) }}</li>
      </ul>
      <template v-if="diag.alternatives.length">
        <p class="status__fail-title">Что можно сделать</p>
        <ul>
          <li v-for="a in diag.alternatives" :key="a.code">{{ issueText(a, 'alternative') }}</li>
        </ul>
      </template>
      <p class="status__hint">
        Доступность зависит от сети, а не от устройства: подойдёт любое устройство с Linux за тем же подключением.
      </p>
    </div>

    <details v-if="diag" class="checks" :open="state === 'unsupported'">
      <summary>Результаты диагностики</summary>
      <dl v-if="summary.length" class="checks__summary">
        <div v-for="[label, value] in summary" :key="label">
          <dt>{{ label }}</dt>
          <dd>{{ value }}</dd>
        </div>
      </dl>
      <ul class="checks__list">
        <li v-for="c in diag.checks" :key="c.name" :class="c.ok ? 'is-ok' : 'is-bad'">
          <span class="checks__mark" aria-hidden="true">{{ c.ok ? '✓' : '✕' }}</span>
          <span>{{ checkLabel(c) }}</span>
          <span class="mono checks__value">{{ c.value }}</span>
          <span class="checks__need">нужно: {{ c.required }}</span>
        </li>
      </ul>
      <ul v-if="diag.warnings.length" class="checks__warn">
        <li v-for="w in diag.warnings" :key="w.code">{{ issueText(w, 'warning') }}</li>
      </ul>
    </details>
  </section>
</template>

<style scoped>
.status {
  margin-top: 28px;
}

/* The descent line: a thin rope with a marker per station. */
.line {
  position: relative;
  display: grid;
  gap: 18px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.line::before {
  content: '';
  position: absolute;
  top: 8px;
  bottom: 8px;
  left: 6px;
  width: 1px;
  background: linear-gradient(var(--line-strong), var(--line));
}

.station {
  position: relative;
  display: grid;
  grid-template-columns: 14px minmax(0, 1fr);
  gap: 16px;
}

.station__mark {
  position: relative;
  width: 13px;
  height: 13px;
  margin-top: 5px;
  border: 1.5px solid var(--text-3);
  border-radius: 50%;
  background: var(--abyss);
}

.is-done .station__mark {
  border: 0;
  background: radial-gradient(circle at 35% 30%, #fffaf1, #eadcc6 50%, #9fb0bf 100%);
  box-shadow: 0 0 12px rgba(243, 233, 216, 0.4);
}

.is-active .station__mark {
  border-color: var(--sand);
  background: var(--sand);
  animation: breathe 1.6s ease-in-out infinite;
}

.is-failed .station__mark {
  border: 0;
  background: var(--coral);
}

.station__title {
  font-weight: 600;
}

.is-pending .station__title,
.is-pending .station__text {
  color: var(--text-3);
}

.station__text {
  font-size: 14px;
  color: var(--text-2);
}

.is-failed .station__text {
  color: var(--coral);
}

@keyframes breathe {
  50% { opacity: 0.4; transform: scale(0.85); }
}

.status__lost,
.status__fail,
.banner {
  margin-top: 24px;
  padding: 18px 20px;
  border-radius: var(--radius-sm);
}

.status__lost {
  border: 1px solid rgba(236, 154, 134, 0.35);
  color: var(--coral);
}

.banner {
  display: grid;
  justify-items: start;
  gap: 8px;
  color: var(--text-2);
}

.banner__title {
  font-family: var(--font-display);
  font-size: 26px;
  line-height: 1.15;
  color: var(--text);
}

.banner__label {
  margin-top: 6px;
  font-weight: 600;
  color: var(--text);
}

.banner ul {
  margin: 0;
  padding-left: 20px;
}

.banner--wait {
  border: 1px solid rgba(223, 193, 137, 0.35);
  background: rgba(223, 193, 137, 0.08);
}

.banner--ok {
  border: 1px solid rgba(143, 214, 207, 0.35);
  background: var(--aqua-soft);
}

.banner--ok .banner__title {
  color: var(--aqua);
}

.banner--fail {
  border: 1px solid rgba(236, 154, 134, 0.3);
  background: var(--coral-soft);
}

.status__fail {
  border: 1px solid rgba(236, 154, 134, 0.3);
  background: var(--coral-soft);
}

.status__fail-title {
  font-weight: 600;
}

.status__fail ul {
  margin: 6px 0 12px;
  padding-left: 20px;
  color: var(--text-2);
}

.status__hint {
  font-size: 14px;
  color: var(--text-2);
}

.checks {
  margin-top: 20px;
  border-top: 1px solid var(--line);
  padding-top: 14px;
}

.checks summary {
  cursor: pointer;
  color: var(--text-2);
  font-size: 14px;
}

.checks__summary {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px 20px;
  margin: 14px 0;
}

.checks__summary dt {
  font-size: 12px;
  color: var(--text-3);
}

.checks__summary dd {
  margin: 2px 0 0;
  font-size: 14px;
  overflow-wrap: anywhere;
}

.checks__list {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 14px;
}

.checks__list li {
  display: grid;
  grid-template-columns: 18px minmax(120px, 0.8fr) minmax(0, 1fr) minmax(0, 1fr);
  gap: 10px;
  align-items: baseline;
}

.checks__mark {
  color: var(--aqua);
}

.is-bad .checks__mark,
.is-bad .checks__value {
  color: var(--coral);
}

.checks__value {
  font-size: 12.5px;
}

.checks__need {
  font-size: 12.5px;
  color: var(--text-3);
}

.checks__warn {
  margin: 12px 0 0;
  padding-left: 20px;
  font-size: 14px;
  color: var(--sand);
}

@media (max-width: 640px) {
  .checks__list li {
    grid-template-columns: 18px minmax(0, 1fr);
  }

  .checks__value,
  .checks__need {
    grid-column: 2;
  }
}

@media (prefers-reduced-motion: reduce) {
  .is-active .station__mark {
    animation: none;
  }
}
</style>
