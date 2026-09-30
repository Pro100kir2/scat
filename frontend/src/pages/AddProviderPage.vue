<script setup lang="ts">
import { computed, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import CommandBlock from '@/components/providers/CommandBlock.vue'
import InstallStatus from '@/components/providers/InstallStatus.vue'
import { ApiError, addProvider, fetchReleases } from '@/services/api'
import type { EnrollmentCreated, ProbeRelease, ProviderForm } from '@/types/api'
import { countryName } from '@/utils/format'

type Step = 'form' | 'method' | 'interactive' | 'manual'

// The enrollment survives a reload of this tab (not other tabs or devices): the token is
// single-use and short-lived, but it is still a credential, so not in localStorage.
const SESSION_KEY = 'skat:enrollment'
const COUNTRIES = ['RU', 'BY', 'KZ', 'UA', 'UZ', 'KG', 'AM', 'GE', 'AZ', 'TJ', 'MD', 'DE', 'NL', 'FI', 'TR', 'RS']

const step = ref<Step>('form')
const form = reactive<ProviderForm>({ name: '', country: 'RU', region: '', info: '', contact_phone: '' })
const errors = reactive<Partial<Record<keyof ProviderForm, string>>>({})
const formError = ref('')
const sending = ref(false)
const enrollment = shallowRef<EnrollmentCreated | null>(null)
const releases = shallowRef<ProbeRelease[]>([])
const chosen = ref('')

function readSession(): { enrollment: EnrollmentCreated; step: Step } | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY)
    const data = raw ? JSON.parse(raw) : null
    if (data?.enrollment?.token && data.enrollment.expires_at * 1000 > Date.now()) return data
  } catch {
    /* no storage: start over */
  }
  return null
}

function saveSession() {
  try {
    if (enrollment.value) sessionStorage.setItem(SESSION_KEY, JSON.stringify({ enrollment: enrollment.value, step: step.value }))
    else sessionStorage.removeItem(SESSION_KEY)
  } catch {
    /* not persisted; the page still works */
  }
}

onMounted(() => {
  const saved = readSession()
  if (saved) {
    enrollment.value = saved.enrollment
    step.value = saved.step === 'form' ? 'method' : saved.step
  }
})
watch(step, saveSession)

const FIELD_ERRORS: Record<string, string> = {
  name: 'Название: 2–48 символов, без ссылок',
  country: 'Страна: двухбуквенный код, например RU',
  region: 'Город или регион: до 64 символов, без ссылок',
  info: 'Дополнительно: до 500 символов, без ссылок',
  contact_phone: 'Телефон: в международном формате, например +7 912 345-67-89',
}

function validate(): boolean {
  for (const key of Object.keys(errors) as (keyof ProviderForm)[]) delete errors[key]
  const name = form.name.trim()
  if (name.length < 2 || name.length > 48) errors.name = FIELD_ERRORS.name
  if (!/^[A-Za-z]{2}$/.test(form.country.trim())) errors.country = FIELD_ERRORS.country
  const phone = form.contact_phone.trim()
  const digits = phone.replace(/\D/g, '')
  if (phone && (!/^[+\d\s().-]+$/.test(phone) || digits.length < 8 || digits.length > 15)) {
    errors.contact_phone = FIELD_ERRORS.contact_phone
  }
  return Object.keys(errors).length === 0
}

async function submit() {
  formError.value = ''
  if (!validate()) return
  sending.value = true
  try {
    enrollment.value = await addProvider({ ...form, name: form.name.trim(), region: form.region.trim(), info: form.info.trim() })
    step.value = 'method'
    saveSession()
  } catch (err) {
    const e = err as ApiError
    if (e.status === 422 && Array.isArray(e.detail)) {
      for (const item of e.detail as { loc?: string[] }[]) {
        const field = item.loc?.[item.loc.length - 1] as keyof ProviderForm | undefined
        if (field && field in FIELD_ERRORS) errors[field] = FIELD_ERRORS[field]
      }
      formError.value = 'Проверьте отмеченные поля.'
    } else if (e.kind === 'rate_limit') {
      formError.value = 'Слишком много заявок с вашего адреса. Попробуйте через час.'
    } else {
      formError.value = 'Не удалось связаться со Скатом. Попробуйте ещё раз чуть позже.'
    }
  } finally {
    sending.value = false
  }
}

async function choose(next: 'interactive' | 'manual') {
  step.value = next
  if (next === 'manual' && !releases.value.length) {
    try {
      releases.value = (await fetchReleases()).releases
      chosen.value ||= releases.value.find((r) => r.profile === 'linux-amd64')?.profile ?? releases.value[0]?.profile ?? ''
    } catch {
      releases.value = []
    }
  }
}

function restart() {
  enrollment.value = null
  step.value = 'form'
  saveSession()
}

const release = computed(() => releases.value.find((r) => r.profile === chosen.value) ?? null)
const apiOrigin = computed(() => enrollment.value?.install.script_url.replace(/\/api\/probes\/install\.sh$/, '') ?? '')
const manual = computed(() => {
  const r = release.value
  const e = enrollment.value
  if (!r || !e) return null
  // MIPS builds are for routers: OpenWrt keeps programs in /usr/bin and usually has wget, not curl.
  const router = r.profile.startsWith('linux-mips')
  const bin = router ? '/usr/bin/scat-probe' : '/usr/local/bin/scat-probe'
  return {
    bin,
    download: router ? `wget -O ${bin} ${r.url}` : `curl -fLo ${bin} ${r.url}`,
    verify: `echo "${r.sha256}  ${bin}" | sha256sum -c - && chmod 755 ${bin}`,
    enroll: `${bin} enroll --api ${apiOrigin.value} --token ${e.token}`,
    run: `${bin} run`,
  }
})
</script>

<template>
  <main id="top" class="page container">
    <header>
      <RouterLink class="back" to="/providers/"><AppIcon name="back" /> Провайдеры</RouterLink>
      <p class="eyebrow">Новый зонд</p>
      <h1 class="page-title">Добавить <em>своего провайдера</em></h1>
      <p class="page-lead">
        Зонд работает на любом устройстве с Linux в вашей сети: роутере с OpenWrt, Raspberry Pi, компьютере.
        Он только проверяет, открываются ли найденные Скатом серверы из сети вашего провайдера.
        <RouterLink to="/providers/install/">Подробнее о зонде</RouterLink>
      </p>
    </header>

    <ol class="steps" aria-label="Шаги">
      <li :class="{ 'is-current': step === 'form', 'is-done': step !== 'form' }">Провайдер</li>
      <li :class="{ 'is-current': step === 'method', 'is-done': step === 'interactive' || step === 'manual' }">Способ установки</li>
      <li :class="{ 'is-current': step === 'interactive' || step === 'manual' }">Установка</li>
    </ol>

    <!-- 1. Provider information -->
    <form v-if="step === 'form'" class="panel form" novalidate @submit.prevent="submit">
      <div class="form__grid">
        <label class="form__field form__field--wide">
          <span>Провайдер <em>*</em></span>
          <input
            v-model="form.name" class="input" name="name" maxlength="48" placeholder="Например: МТС, Билайн, Ростелеком"
            autocomplete="off" required :aria-invalid="!!errors.name"
          />
          <small v-if="errors.name" class="form__error">{{ errors.name }}</small>
        </label>
        <label class="form__field">
          <span>Страна <em>*</em></span>
          <input
            v-model="form.country" class="input mono" name="country" maxlength="2" list="countries"
            autocomplete="off" required :aria-invalid="!!errors.country"
          />
          <datalist id="countries">
            <option v-for="c in COUNTRIES" :key="c" :value="c">{{ countryName(c) }}</option>
          </datalist>
          <small v-if="errors.country" class="form__error">{{ errors.country }}</small>
          <small v-else class="form__hint">{{ countryName(form.country.toUpperCase()) }}</small>
        </label>
        <label class="form__field">
          <span>Город или регион</span>
          <input
            v-model="form.region" class="input" name="region" maxlength="64" placeholder="Москва" autocomplete="off"
            :aria-invalid="!!errors.region"
          />
          <small v-if="errors.region" class="form__error">{{ errors.region }}</small>
        </label>
        <label class="form__field form__field--wide">
          <span>Телефон для связи <small>(необязательно)</small></span>
          <input
            v-model="form.contact_phone" class="input mono" name="contact_phone" type="tel" maxlength="32"
            placeholder="+7 912 345-67-89" autocomplete="tel" inputmode="tel" :aria-invalid="!!errors.contact_phone"
            aria-describedby="phone-hint"
          />
          <small v-if="errors.contact_phone" class="form__error">{{ errors.contact_phone }}</small>
          <small v-else id="phone-hint" class="form__hint">
            Только для администратора Ската, если понадобится уточнить что-то о зонде. Нигде не публикуется.
          </small>
        </label>
        <label class="form__field form__field--wide">
          <span>Дополнительно</span>
          <textarea
            v-model="form.info" class="input" name="info" maxlength="500"
            placeholder="Тип подключения (домашний интернет, мобильный), тариф: всё, что поможет понять результаты"
            :aria-invalid="!!errors.info"
          ></textarea>
          <small v-if="errors.info" class="form__error">{{ errors.info }}</small>
        </label>
      </div>
      <p v-if="formError" class="form__error form__error--block" role="alert">{{ formError }}</p>
      <div class="form__actions">
        <button class="btn btn--primary" type="submit" :disabled="sending">
          {{ sending ? 'Отправляем…' : 'Продолжить' }}
        </button>
        <p class="form__note">Мы не просим ни почту, ни пароли. Телефон — по желанию. Токен установки действует 24 часа.</p>
      </div>
    </form>

    <!-- 2. Installation method -->
    <section v-else-if="step === 'method' && enrollment" class="methods" aria-label="Способ установки">
      <p class="methods__for">
        <span>Провайдер: <strong>{{ enrollment.provider.name }}</strong>, {{ countryName(enrollment.provider.country) }}</span>
        <button class="btn btn--sm btn--quiet" type="button" @click="restart">Изменить</button>
      </p>
      <div class="methods__grid">
        <button class="method" type="button" @click="choose('interactive')">
          <span class="method__badge">Рекомендуем</span>
          <AppIcon name="terminal" class="method__icon" />
          <span class="method__title">Интерактивная установка</span>
          <span class="method__text">
            Одна команда. Скрипт сначала проверит устройство и установит зонд, только если оно подходит.
            Ход установки будет виден на этой странице.
          </span>
        </button>
        <button class="method" type="button" @click="choose('manual')">
          <AppIcon name="steps" class="method__icon" />
          <span class="method__title">Ручная установка</span>
          <span class="method__text">
            По шагам: скачать сборку для своей платформы, проверить контрольную сумму, зарегистрировать и запустить.
          </span>
        </button>
      </div>
    </section>

    <!-- 3a. Interactive installation -->
    <section v-else-if="step === 'interactive' && enrollment" class="panel install" aria-labelledby="interactive-title">
      <button class="btn btn--sm btn--quiet install__back" type="button" @click="step = 'method'">
        <AppIcon name="back" /> Другой способ
      </button>
      <h2 id="interactive-title" class="install__title">Интерактивная установка</h2>
      <p class="install__lead">
        Выполните команду на устройстве от имени root (на обычном Linux добавьте <code class="mono">sudo</code> перед
        <code class="mono">sh</code>). Сначала скрипт только проверит устройство и спросит сервер, подходит ли оно.
        Если нет, он ничего не установит и объяснит почему.
      </p>
      <CommandBlock :command="enrollment.install.curl" label="Если есть curl" />
      <CommandBlock :command="enrollment.install.wget" label="Если есть только wget (OpenWrt)" />
      <InstallStatus :enrollment-id="enrollment.enrollment_id" :token="enrollment.token" />
    </section>

    <!-- 3b. Manual installation -->
    <section v-else-if="step === 'manual' && enrollment" class="panel install" aria-labelledby="manual-title">
      <button class="btn btn--sm btn--quiet install__back" type="button" @click="step = 'method'">
        <AppIcon name="back" /> Другой способ
      </button>
      <h2 id="manual-title" class="install__title">Ручная установка</h2>
      <ol class="manual">
        <li>
          <p><strong>Узнайте свою платформу.</strong> Эта команда только проверит устройство и ничего не установит:</p>
          <CommandBlock :command="enrollment.install.diagnose_only" />
        </li>
        <li>
          <p><strong>Выберите сборку</strong>, которую назвала проверка:</p>
          <label class="manual__select">
            <span class="visually-hidden">Платформа</span>
            <select v-model="chosen" class="input">
              <option v-for="r in releases" :key="r.profile" :value="r.profile">{{ r.name }} ({{ (r.size / 1048576).toFixed(1) }} МБ)</option>
            </select>
          </label>
          <p v-if="!releases.length" class="manual__warn">Список сборок недоступен. Попробуйте интерактивную установку.</p>
          <p v-if="release" class="manual__sha mono">SHA-256: {{ release.sha256 }}</p>
        </li>
        <li v-if="manual">
          <p><strong>Скачайте и проверьте контрольную сумму.</strong> Если сумма не совпадёт, не запускайте файл.</p>
          <CommandBlock :command="manual.download" />
          <CommandBlock :command="manual.verify" />
        </li>
        <li v-if="manual">
          <p><strong>Зарегистрируйте зонд</strong> (токен одноразовый):</p>
          <CommandBlock :command="manual.enroll" />
        </li>
        <li v-if="manual">
          <p>
            <strong>Запустите</strong> и добавьте в автозапуск: готовые файлы служб для systemd, OpenWrt (procd) и
            OpenRC есть в <RouterLink to="/providers/install/#manual">инструкции</RouterLink>.
          </p>
          <CommandBlock :command="manual.run" />
        </li>
      </ol>
      <InstallStatus :enrollment-id="enrollment.enrollment_id" :token="enrollment.token" />
    </section>
  </main>
</template>

<style scoped>
.back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 18px;
  color: var(--text-3);
  font-size: 14px;
  text-decoration: none;
}

.back:hover {
  color: var(--text);
}

.page-lead a,
.install a,
.manual a {
  color: var(--aqua);
}

.steps {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 28px;
  margin: 40px 0 24px;
  padding: 0;
  list-style: none;
  counter-reset: step;
  font-size: 14px;
  color: var(--text-3);
}

.steps li {
  counter-increment: step;
}

.steps li::before {
  content: '0' counter(step) ' ';
  font-family: var(--font-mono);
  font-size: 12px;
}

.steps .is-current {
  color: var(--pearl);
}

.steps .is-done {
  color: var(--text-2);
}

.form__grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px 20px;
}

.form__field {
  display: grid;
  gap: 6px;
  align-content: start;
}

.form__field > span {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-2);
}

.form__field em {
  color: var(--coral);
  font-style: normal;
}

.form__field small small,
.form__field > span small {
  color: var(--text-3);
  font-weight: 400;
}

.form__field--wide {
  grid-column: 1 / -1;
}

.form__hint {
  font-size: 12px;
  color: var(--text-3);
}

.form__error {
  font-size: 12.5px;
  color: var(--coral);
}

.form__error--block {
  margin-top: 16px;
}

.form__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 20px;
  margin-top: 24px;
}

.form__note {
  font-size: 13px;
  color: var(--text-3);
}

.methods__for {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  margin-bottom: 18px;
  color: var(--text-2);
}

.methods__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.method {
  position: relative;
  display: grid;
  justify-items: start;
  gap: 10px;
  padding: 26px 24px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: linear-gradient(180deg, rgba(16, 38, 52, 0.55), rgba(10, 26, 37, 0.55));
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.25s var(--ease-out), transform 0.25s var(--ease-out);
}

.method:hover {
  border-color: rgba(243, 233, 216, 0.35);
  transform: translateY(-2px);
}

.method__badge {
  position: absolute;
  top: 18px;
  right: 18px;
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--aqua-soft);
  color: var(--aqua);
  font-size: 12px;
}

.method__icon {
  width: 28px;
  height: 28px;
  color: var(--pearl);
}

.method__title {
  font-family: var(--font-display);
  font-size: 26px;
  line-height: 1.1;
}

.method__text {
  color: var(--text-2);
  font-size: 14.5px;
}

.install__back {
  margin: -8px 0 8px -10px;
}

.install__title {
  font-family: var(--font-display);
  font-size: 30px;
  font-weight: 500;
}

.install__lead {
  margin: 8px 0 20px;
  color: var(--text-2);
}

.install code,
.install__lead code {
  font-size: 13px;
}

.manual {
  display: grid;
  gap: 22px;
  margin: 16px 0 0;
  padding-left: 22px;
  color: var(--text-2);
}

.manual li p {
  margin-bottom: 10px;
}

.manual strong {
  color: var(--text);
}

.manual__select {
  display: block;
  max-width: 420px;
}

.manual__sha {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-3);
  overflow-wrap: anywhere;
}

.manual__warn {
  color: var(--sand);
}

@media (max-width: 760px) {
  .form__grid,
  .methods__grid {
    grid-template-columns: 1fr;
  }
}
</style>
