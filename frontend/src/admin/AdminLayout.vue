<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import './admin.css'
import ConfirmDialog from './components/ConfirmDialog.vue'
import { errorText } from './api'
import { admin, checkSession, dismiss, pollNotifications, setupAudio, signIn, signOut, toggleSound, usePoll } from './state'

const NAV = [
  { to: '/admin/', label: 'Dashboard', exact: true },
  { to: '/admin/probes/', label: 'Probes', badge: true },
  { to: '/admin/sources/', label: 'Sources' },
  { to: '/admin/scanner/', label: 'Scanner' },
  { to: '/admin/scans/', label: 'Scans' },
  { to: '/admin/providers/', label: 'Providers' },
  { to: '/admin/cache/', label: 'Cache' },
  { to: '/admin/system/', label: 'System' },
  { to: '/admin/logs/', label: 'Logs' },
  { to: '/admin/settings/', label: 'Settings' },
]

const route = useRoute()
const menuOpen = ref(false)
const token = ref('')
const loginError = ref('')
const signingIn = ref(false)

// New-probe notifications: one small request every 20 s while the tab is visible.
const notifications = usePoll(async () => {
  if (admin.auth === 'in') await pollNotifications()
}, 20_000)

onMounted(() => {
  setupAudio()
  void checkSession()
})

watch(() => admin.auth, (auth) => {
  if (auth === 'in') void notifications.refresh()
})
watch(() => route.fullPath, () => (menuOpen.value = false))

async function submit() {
  loginError.value = ''
  signingIn.value = true
  try {
    await signIn(token.value.trim())
    token.value = ''  // the token is not kept anywhere once the session cookie exists
  } catch (err) {
    loginError.value = (err as { status?: number }).status === 401 ? 'Wrong admin token.' : errorText(err)
  } finally {
    signingIn.value = false
  }
}

const isActive = (item: { to: string; exact?: boolean }) =>
  item.exact ? route.path === item.to || route.path === '/admin' : route.path.startsWith(item.to)
</script>

<template>
  <div v-if="admin.auth === 'unknown'" class="login" aria-busy="true">Loading…</div>

  <main v-else-if="admin.auth === 'out'" class="login">
    <form @submit.prevent="submit">
      <p class="admin__brand">Скат Admin</p>
      <label class="stack">
        <span>Admin token</span>
        <input
          v-model="token" class="input mono" type="password" autocomplete="current-password" required
          aria-describedby="token-hint"
        />
      </label>
      <small id="token-hint" class="muted">
        The token is exchanged for a secure session cookie and is not stored in the browser.
      </small>
      <p v-if="loginError" class="error-text" role="alert">{{ loginError }}</p>
      <button class="btn btn--primary" type="submit" :disabled="signingIn || !token">
        {{ signingIn ? 'Signing in…' : 'Sign in' }}
      </button>
    </form>
  </main>

  <div v-else class="admin">
    <aside class="admin__side" :class="{ 'is-open': menuOpen }">
      <div class="admin__head">
        <p class="admin__brand">Скат Admin</p>
        <button class="btn btn--sm btn--quiet menu-toggle" :aria-expanded="menuOpen" @click="menuOpen = !menuOpen">
          Menu <span v-if="admin.unread" class="dot">{{ admin.unread }}</span>
        </button>
      </div>
      <nav class="admin__nav" aria-label="Admin sections">
        <RouterLink
          v-for="item in NAV" :key="item.to" :to="item.to" :aria-current="isActive(item) ? 'page' : undefined"
          :class="{ 'is-active': isActive(item) }" active-class="" exact-active-class=""
        >
          <span>{{ item.label }}</span>
          <span v-if="item.badge && admin.unread" class="dot" :aria-label="`${admin.unread} new probes`">{{ admin.unread }}</span>
        </RouterLink>
      </nav>
      <div class="admin__foot">
        <button class="btn btn--sm btn--quiet" :aria-pressed="!admin.muted" @click="toggleSound">
          {{ admin.muted ? '🔕 Sound off' : '🔔 Sound on' }}
        </button>
        <button class="btn btn--sm btn--quiet" @click="signOut">Sign out</button>
      </div>
    </aside>

    <main class="admin__main">
      <p v-if="admin.soundBlocked && !admin.muted" class="box" role="status" style="margin-bottom: 16px">
        🔔 A new probe arrived, but the browser blocked the sound. Click anywhere on the page to allow notification sounds.
      </p>
      <RouterView />
    </main>

    <div class="toasts" aria-live="polite">
      <div v-for="t in admin.toasts" :key="t.id" class="toast" :class="`toast--${t.tone}`" role="status">
        <div class="toast__head">
          <span class="toast__title">{{ t.title }}</span>
          <button class="toast__close" aria-label="Dismiss" @click="dismiss(t.id)">✕</button>
        </div>
        <span v-for="line in t.lines" :key="line" class="muted">{{ line }}</span>
        <RouterLink v-if="t.to" :to="t.to" @click="dismiss(t.id)">Open</RouterLink>
      </div>
    </div>
    <ConfirmDialog />
  </div>
</template>
