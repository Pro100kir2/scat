import { onBeforeUnmount, onMounted, reactive } from 'vue'
import { adminRequest, errorText, login, logout, onUnauthorized, session } from './api'
import { decide, isMuted, readCursor, setMuted, writeCursor, type NewProbeEvent, type NotificationsResponse, type UnreadProbe } from './notify'
import { armAudio, audioReady, chime } from './sound'

export type Tone = 'info' | 'ok' | 'warn' | 'bad'

export interface Toast {
  id: number
  tone: Tone
  title: string
  lines: string[]
  to?: string
}

/** Panel-wide state: one instance, shared by the layout and every page. */
export const admin = reactive({
  auth: 'unknown' as 'unknown' | 'in' | 'out',
  unread: 0,
  unreadProbes: [] as UnreadProbe[],
  toasts: [] as Toast[],
  muted: isMuted(),
  /** A notification arrived but the browser has not allowed sound yet. */
  soundBlocked: false,
})

let toastId = 0

export function toast(title: string, tone: Tone = 'info', lines: string[] = [], opts: { to?: string; ms?: number } = {}): void {
  const id = ++toastId
  admin.toasts.push({ id, tone, title, lines, to: opts.to })
  const ms = opts.ms ?? (tone === 'bad' ? 9000 : 5000)
  if (ms > 0) setTimeout(() => dismiss(id), ms)
}

export function dismiss(id: number): void {
  const i = admin.toasts.findIndex((t) => t.id === id)
  if (i >= 0) admin.toasts.splice(i, 1)
}

export function toastError(action: string, err: unknown): void {
  toast(`${action} failed`, 'bad', [errorText(err)])
}

onUnauthorized(() => {
  if (admin.auth === 'in') toast('Session expired', 'warn', ['Sign in again to continue.'])
  admin.auth = 'out'
})

export async function checkSession(): Promise<void> {
  try {
    await session()
    admin.auth = 'in'
  } catch {
    admin.auth = 'out'
  }
}

export async function signIn(token: string): Promise<void> {
  await login(token)
  admin.auth = 'in'
}

export async function signOut(): Promise<void> {
  try {
    await logout()
  } finally {
    admin.auth = 'out'
  }
}

export function toggleSound(): void {
  admin.muted = !admin.muted
  setMuted(admin.muted)
  if (!admin.muted) admin.soundBlocked = !audioReady() && admin.soundBlocked
}

function announce(events: NewProbeEvent[]): void {
  for (const e of events.slice(-5)) {
    toast('🔔 New probe registered', 'info', [
      `Probe: ${e.probe_id}`,
      `Status: ${e.data.status === 'approved' ? 'Approved' : 'Pending verification'}`,
      ...(e.data.profile ? [`Profile: ${e.data.profile}`] : []),
      ...(e.data.provider ? [`Provider: ${e.data.provider}${e.data.country ? `, ${e.data.country}` : ''}`] : []),
    ], { to: `/admin/probes/${e.probe_id}`, ms: 15_000 })
  }
  if (events.length > 5) toast(`…and ${events.length - 5} more new probes`, 'info', [], { to: '/admin/probes' })
}

export async function pollNotifications(): Promise<void> {
  const cursor = readCursor()
  const reply = await adminRequest<NotificationsResponse>('/notifications', { query: { after: cursor ?? undefined } })
  const decision = decide(cursor, reply)
  writeCursor(decision.cursor)
  admin.unread = reply.unread_count
  admin.unreadProbes = reply.unread
  if (decision.announce.length) {
    announce(decision.announce)
    if (decision.sound && !admin.muted) admin.soundBlocked = !chime()
  }
}

export async function markRead(probeIds?: string[]): Promise<void> {
  await adminRequest('/notifications/read', { method: 'POST', body: probeIds ? { probe_ids: probeIds } : { all: true } })
  await pollNotifications().catch(() => undefined)
}

/**
 * Run `fn` now and then every `ms`, only while the tab is visible (a hidden tab costs the
 * server nothing), and once more as soon as it becomes visible again.
 */
export function usePoll(fn: () => Promise<unknown> | unknown, ms: number | (() => number)) {
  let timer: ReturnType<typeof setTimeout> | undefined
  let stopped = false
  const interval = () => (typeof ms === 'function' ? ms() : ms)

  async function tick() {
    clearTimeout(timer)
    if (stopped) return
    if (typeof document === 'undefined' || !document.hidden) {
      try {
        await fn()
      } catch {
        /* the page shows its own error state */
      }
    }
    if (!stopped) timer = setTimeout(tick, interval())
  }

  const onVisible = () => {
    if (!document.hidden) void tick()
  }

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisible)
    void tick()
  })
  onBeforeUnmount(() => {
    stopped = true
    clearTimeout(timer)
    document.removeEventListener('visibilitychange', onVisible)
  })
  return { refresh: tick }
}

export function setupAudio(): void {
  armAudio()
}
