/**
 * A short two-note chime, synthesised with Web Audio (no audio file to load or cache).
 *
 * Browsers only let a page make sound after the user has interacted with it. The context
 * is created on the first click or key press anywhere in the panel ("unlocked"); before
 * that, chime() reports that it could not play, so the panel can show a visible hint.
 */
type Ctx = AudioContext

let ctx: Ctx | null = null
let armed = false

function create(): Ctx | null {
  const Ctor = (globalThis as { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext }).AudioContext
    ?? (globalThis as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Ctor) return null
  try {
    return new Ctor()
  } catch {
    return null
  }
}

/** Listen for the first user gesture; safe to call many times. */
export function armAudio(): void {
  if (armed || typeof window === 'undefined') return
  armed = true
  const unlock = () => {
    ctx ??= create()
    void ctx?.resume().catch(() => undefined)
    window.removeEventListener('pointerdown', unlock)
    window.removeEventListener('keydown', unlock)
  }
  window.addEventListener('pointerdown', unlock)
  window.addEventListener('keydown', unlock)
}

export function audioReady(): boolean {
  return ctx !== null && ctx.state === 'running'
}

/** Play the chime. Returns false when the browser does not allow sound yet. */
export function chime(): boolean {
  if (!audioReady() || !ctx) return false
  const now = ctx.currentTime
  for (const [i, freq] of [880, 1318.5].entries()) {
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.type = 'sine'
    osc.frequency.value = freq
    const start = now + i * 0.16
    gain.gain.setValueAtTime(0.0001, start)
    gain.gain.exponentialRampToValueAtTime(0.18, start + 0.02)
    gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.35)
    osc.connect(gain).connect(ctx.destination)
    osc.start(start)
    osc.stop(start + 0.4)
  }
  return true
}
