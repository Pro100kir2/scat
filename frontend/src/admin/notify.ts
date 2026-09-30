/**
 * New-probe notifications: which events are genuinely new, and when to make a sound.
 *
 * The server returns `latest_id` (the newest probe_registered event) and, when asked with
 * `after`, the events since then. The panel keeps the cursor in localStorage, so:
 * - the very first poll ever only sets the cursor (old probes never ring);
 * - a reload does not replay anything already seen;
 * - repeated polls while a probe stays pending never ring again;
 * - one sound per poll, however many probes arrived together.
 */
import { storage } from '@/utils/storage'

export const CURSOR_KEY = 'scat-admin-notify-cursor'
export const MUTE_KEY = 'scat-admin-sound-muted'

export interface NewProbeEvent {
  id: number
  ts: number
  probe_id: string
  data: { profile?: string; status?: string; provider?: string; country?: string; platform?: string }
}

export interface UnreadProbe {
  id: string
  status: string
  verify_state: string
  lifecycle: string
  profile: string | null
  created_at: number
  provider: string
}

export interface NotificationsResponse {
  latest_id: number
  new: NewProbeEvent[]
  unread_count: number
  unread: UnreadProbe[]
}

export interface Decision {
  cursor: number
  announce: NewProbeEvent[]
  sound: boolean
}

export function readCursor(): number | null {
  const raw = storage.get(CURSOR_KEY)
  const value = raw === null ? NaN : Number(raw)
  return Number.isFinite(value) && value >= 0 ? value : null
}

export function writeCursor(cursor: number): void {
  storage.set(CURSOR_KEY, String(cursor))
}

/** Pure: given the stored cursor and a server reply, what to show and whether to ring. */
export function decide(cursor: number | null, reply: NotificationsResponse): Decision {
  if (cursor === null) return { cursor: reply.latest_id, announce: [], sound: false }
  const announce = reply.new.filter((e) => e.id > cursor)
  const newest = Math.max(cursor, reply.latest_id, ...announce.map((e) => e.id))
  return { cursor: newest, announce, sound: announce.length > 0 }
}

export const isMuted = () => storage.get(MUTE_KEY) === '1'
export const setMuted = (muted: boolean) => (muted ? storage.set(MUTE_KEY, '1') : storage.remove(MUTE_KEY))
