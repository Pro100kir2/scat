import { Environment, type Size } from './environment'
import { Manta } from './manta'
import { clamp, damp, easeOutCubic, mulberry32, TAU } from './math'
import { PearlBed } from './pearls'

export type SceneMode = 'idle' | 'scanning' | 'replay'

export interface Rect {
  x: number
  y: number
  width: number
  height: number
}

export interface OceanOptions {
  reducedMotion: boolean
  /** Called every time a discovery becomes visible, with the running total. */
  onReveal: (total: number) => void
  /** Called when every queued discovery has been shown. */
  onDrained: () => void
}

type Plan =
  | { kind: 'wander' }
  | { kind: 'investigate'; linger: number }
  | { kind: 'rise'; time: number }
  | { kind: 'seek'; x: number; y: number; time: number }

interface Sonar {
  x: number
  y: number
  age: number
  /** Pearls waiting for this wave to pass over them. */
  pending: { x: number; y: number; far: boolean }[]
}

const SONAR_SPEED = 260 // px/s across the seabed
const SONAR_LIFE = 3.2

export class OceanEngine {
  private readonly ctx: CanvasRenderingContext2D
  private readonly rand = mulberry32((Date.now() % 100000) + 7)
  private readonly env: Environment
  private readonly pearls = new PearlBed(this.rand)
  private manta: Manta | null = null
  private plan: Plan = { kind: 'wander' }
  private mode: SceneMode = 'idle'
  private safe: Rect | null = null

  private queue = 0
  private revealed = 0
  private sonars: Sonar[] = []
  private sonarTimer = 1.5
  private overflowTimer = 0
  private replayWaveSent = false

  private time = 0
  private last = 0
  private raf = 0
  private running = false
  private visible = true
  private pointer = { x: 0, y: 0, px: 0, py: 0, sx: 0, sy: 0, inside: false }
  private hovered: number | null = null
  private unit = 1
  private readonly lite: boolean
  private readonly observers: { disconnect(): void }[] = []

  constructor(private readonly canvas: HTMLCanvasElement, private readonly opts: OceanOptions) {
    this.ctx = canvas.getContext('2d', { alpha: false })!
    this.lite = window.matchMedia('(max-width: 720px), (pointer: coarse)').matches
    this.env = new Environment(this.rand, this.lite)

    const resize = new ResizeObserver(() => this.resize())
    resize.observe(canvas)
    const onScreen = new IntersectionObserver(([entry]) => {
      this.visible = !!entry?.isIntersecting
      this.syncLoop()
    })
    onScreen.observe(canvas)
    this.observers.push(resize, onScreen)
    document.addEventListener('visibilitychange', this.syncLoop)
    this.resize()
  }

  // --- public API -------------------------------------------------------------

  setSafeZone(rect: Rect | null) {
    this.safe = rect
    if (this.opts.reducedMotion && this.manta) {
      this.placeStillManta(this.manta)
      this.redrawIfStatic()
    }
  }

  setMode(mode: SceneMode) {
    if (mode === this.mode) return
    this.mode = mode
    this.replayWaveSent = false
    this.sonarTimer = 0.6
    this.redrawIfStatic()
  }

  /** Queue `count` new discoveries; each becomes a pearl (or a far glint past the cap). */
  discover(count: number) {
    if (count <= 0) return
    this.queue += count
    if (this.opts.reducedMotion) {
      while (this.queue > 0) this.revealAt(...this.pickPearlSpot(), false)
      this.redrawIfStatic()
    }
    this.checkDrained()
  }

  reset() {
    this.queue = 0
    this.revealed = 0
    this.sonars = []
    this.pearls.clear()
    this.plan = { kind: 'wander' }
    this.redrawIfStatic()
  }

  pointerMove(x: number, y: number, inside: boolean) {
    const { width, height } = this.env.size
    Object.assign(this.pointer, { x, y, inside, px: (x / width) * 2 - 1, py: (y / height) * 2 - 1 })
    const hit = inside ? this.pearls.hit(x, y) : null
    const hovered = hit ? hit.rank : null
    if (hovered !== this.hovered) {
      this.hovered = hovered
      this.redrawIfStatic()
    }
    return hovered
  }

  destroy() {
    this.running = false
    cancelAnimationFrame(this.raf)
    this.observers.forEach((o) => o.disconnect())
    document.removeEventListener('visibilitychange', this.syncLoop)
  }

  // --- lifecycle --------------------------------------------------------------

  private resize() {
    const rect = this.canvas.getBoundingClientRect()
    if (rect.width < 2 || rect.height < 2) return
    const dpr = Math.min(window.devicePixelRatio || 1, this.lite ? 1.5 : 2)
    const size: Size = { width: rect.width, height: rect.height, dpr }
    this.canvas.width = Math.round(size.width * dpr)
    this.canvas.height = Math.round(size.height * dpr)
    this.env.resize(size)
    this.unit = clamp(Math.min(size.width, size.height * 1.3) / 900, 0.55, 1.25)

    // Pearls sit on the sand; keep them there after a resize.
    for (const p of this.pearls.pearls) {
      p.x = clamp(p.x, size.width * 0.05, size.width * 0.95)
      p.y = this.env.seabedY(p.x) + 2
    }

    if (!this.manta) this.spawnManta()
    this.manta!.span = (this.lite ? 230 : 330) * this.unit
    if (this.opts.reducedMotion) this.placeStillManta(this.manta!)
    this.syncLoop()
    this.redrawIfStatic()
  }

  private spawnManta() {
    const { width, height } = this.env.size
    if (this.opts.reducedMotion) {
      // A still, composed pose for people who prefer less motion.
      const m = new Manta(0, 0, Math.PI - 0.35, this.rand)
      m.bank = 0.12
      m.pitch = 0.08
      m.flapPhase = 1.1
      m.flapAmp = 0.22
      this.placeStillManta(m)
      this.manta = m
      return
    }
    // Glide in from the right edge.
    const m = new Manta(width + 160 * this.unit, height * (this.lite ? 0.66 : 0.4), Math.PI, this.rand)
    m.speed = 70 * this.unit
    this.manta = m
    this.nextWaypoint()
  }

  /** Rest the still manta in open water: beside the text on wide screens, below it on narrow ones. */
  private placeStillManta(m: Manta) {
    const { width, height } = this.env.size
    const s = this.safe
    const beside = !s || s.x + s.width < width * 0.62
    m.x = beside ? width * 0.74 : width * 0.62
    const seabed = this.env.seabedY(m.x)
    m.y = beside ? height * 0.44 : Math.min(seabed - 70 * this.unit, (s.y + s.height + seabed) / 2)
  }

  private readonly syncLoop = () => {
    const shouldRun = !this.opts.reducedMotion && this.visible && !document.hidden
    if (shouldRun && !this.running) {
      this.running = true
      this.last = performance.now()
      this.raf = requestAnimationFrame(this.frame)
    } else if (!shouldRun && this.running) {
      this.running = false
      cancelAnimationFrame(this.raf)
    }
  }

  private readonly frame = (now: number) => {
    if (!this.running) return
    const dt = Math.min(0.05, (now - this.last) / 1000)
    this.last = now
    this.time += dt
    this.update(dt)
    this.render(dt)
    this.raf = requestAnimationFrame(this.frame)
  }

  private redrawIfStatic() {
    if (this.opts.reducedMotion && this.manta) this.render(0)
  }

  // --- behaviour ----------------------------------------------------------------

  private allowed(x: number, y: number) {
    const { width, height } = this.env.size
    if (x < width * 0.06 || x > width * 0.94 || y < height * 0.12 || y > this.env.seabedY(x) - 40 * this.unit) return false
    const s = this.safe
    if (!s) return true
    const margin = 30 * this.unit
    return !(x > s.x - margin && x < s.x + s.width + margin && y > s.y - margin && y < s.y + s.height + margin)
  }

  private cruiseSpeed() {
    return this.mode === 'idle' ? 46 : 62
  }

  private nextWaypoint() {
    const m = this.manta!
    const { width, height } = this.env.size

    if (this.mode === 'idle' && this.rand() < 0.2) {
      const x = clamp(m.x + Math.cos(m.yaw) * (150 + this.rand() * 200) * this.unit, width * 0.1, width * 0.9)
      const y = this.env.seabedY(x) - 55 * this.unit
      if (this.allowed(x, y)) {
        this.plan = { kind: 'investigate', linger: 1.4 + this.rand() * 1.4 }
        m.target = { x, y, depth: 0.1 + this.rand() * 0.2, speed: 34 }
        return
      }
    }

    // Sample candidates and prefer gentle turns at a comfortable distance.
    let best = { x: width * 0.7, y: height * 0.45, score: -Infinity }
    const heading = Math.cos(m.yaw)
    for (let i = 0; i < 10; i++) {
      const x = width * (0.06 + this.rand() * 0.88)
      const y = height * 0.12 + this.rand() * (this.env.seabedY(x) - height * 0.12 - 40 * this.unit)
      if (!this.allowed(x, y)) continue
      const dx = x - m.x
      const dist = Math.hypot(dx, y - m.y)
      const turn = Math.abs(Math.sign(dx) - Math.sign(heading))
      const score = -turn * 0.45 - Math.abs(dist - 320 * this.unit) / (300 * this.unit) + this.rand() * 0.9
      if (score > best.score) best = { x, y, score }
    }
    this.plan = { kind: 'wander' }
    m.target = { x: best.x, y: best.y, depth: this.rand() * 0.75, speed: this.cruiseSpeed() * (0.85 + this.rand() * 0.3) }
  }

  private pickPearlSpot(): [number, number] {
    const { width } = this.env.size
    const m = this.manta
    const dir = m ? Math.sign(Math.cos(m.yaw)) || 1 : 1
    for (let i = 0; i < 16; i++) {
      const ahead = m ? m.x + dir * (90 + this.rand() * 260) * this.unit : width * this.rand()
      const x = i < 8 ? ahead : width * (0.06 + this.rand() * 0.88)
      if (x > width * 0.05 && x < width * 0.95 && this.isFreeSpot(x)) return [x, this.env.seabedY(x) + 2]
    }
    for (let i = 0; i < 24; i++) {
      const x = width * (0.06 + this.rand() * 0.88)
      if (this.isFreeSpot(x)) return [x, this.env.seabedY(x) + 2]
    }
    const x = width * (0.55 + this.rand() * 0.4)
    return [x, this.env.seabedY(x) + 2]
  }

  /** No pearl, and no pearl still waiting for a sonar wave, too close to `x`. */
  private isFreeSpot(x: number) {
    const gap = 44 * this.unit
    const s = this.safe
    // Keep pearls out from under the text when the copy reaches down to the sand.
    if (s && s.y + s.height > this.env.seabedY(x) - 40 && x > s.x - 20 && x < s.x + s.width + 20) return false
    return this.pearls.isFree(x, gap) && this.sonars.every((w) => w.pending.every((p) => p.far || Math.abs(p.x - x) > gap))
  }

  private maxPearls() {
    return this.lite ? 8 : 12
  }

  /** Make the next queued discovery visible. */
  private revealAt(x: number, y: number, withWave: boolean) {
    if (this.queue <= 0) return
    this.queue -= 1
    this.revealed += 1
    if (this.revealed <= this.maxPearls()) {
      this.pearls.add(x, y, this.revealed, this.opts.reducedMotion)
    } else {
      const { width, height } = this.env.size
      const fx = width * (0.05 + this.rand() * 0.9)
      this.pearls.glint(fx, height * (0.72 + this.rand() * 0.06))
    }
    if (withWave) this.sonars.push({ x, y, age: 0, pending: [] })
    this.opts.onReveal(this.revealed)
    this.checkDrained()
  }

  private checkDrained() {
    if (this.queue === 0 && this.sonars.every((s) => s.pending.length === 0)) {
      queueMicrotask(() => this.opts.onDrained())
    }
  }

  private update(dt: number) {
    const m = this.manta
    if (!m) return
    const p = this.pointer
    p.sx = damp(p.sx, p.inside ? p.px : 0, 2, dt)
    p.sy = damp(p.sy, p.inside ? p.py : 0, 2, dt)

    // A little shy: if the cursor comes close, drift away from it.
    if (p.inside && this.plan.kind === 'wander' && m.distanceTo(p.x, p.y) < 110 * this.unit) {
      const away = Math.sign(m.x - p.x) || 1
      m.target = { ...m.target, x: clamp(m.x + away * 240 * this.unit, 0, this.env.size.width), y: m.y - 60 * this.unit, speed: 84 }
    }

    // Discoveries: swim to the spot, then let the pearl appear under the manta.
    if (this.queue > 0 && this.plan.kind !== 'seek') {
      const outstanding = this.sonars.reduce((n, s) => n + s.pending.length, 0)
      if (this.queue > outstanding) {
        const [x, y] = this.pickPearlSpot()
        this.plan = { kind: 'seek', x, y, time: 0 }
        m.target = { x, y: y - 48 * this.unit, depth: 0.12, speed: 58 }
      }
    }

    const plan = this.plan
    const arrived = m.distanceTo(m.target.x, m.target.y) < 36 * this.unit
    if (plan.kind === 'seek') {
      plan.time += dt
      // Normally the manta gets there in 2–4 s; never let a missed approach stall the story.
      if (m.distanceTo(plan.x, plan.y - 48 * this.unit) < 44 * this.unit || plan.time > 7) {
        this.revealAt(plan.x, plan.y, true)
        m.celebrate()
        this.plan = { kind: 'rise', time: 1.6 }
        m.target = { x: m.x + Math.cos(m.yaw) * 200 * this.unit, y: m.y - 150 * this.unit, depth: m.depth, speed: 90 }
        if (this.mode === 'replay' && !this.replayWaveSent) this.sendReplayWave(plan.x, plan.y)
      }
    } else if (plan.kind === 'investigate') {
      if (arrived) {
        plan.linger -= dt
        m.target = { ...m.target, x: m.x + Math.cos(m.yaw) * 60, speed: 18 }
        if (plan.linger <= 0) {
          this.plan = { kind: 'rise', time: 2 }
          m.target = { x: m.x + Math.cos(m.yaw) * 220 * this.unit, y: m.y - 190 * this.unit, depth: m.depth, speed: 92 }
        }
      }
    } else if (plan.kind === 'rise') {
      plan.time -= dt
      if (plan.time <= 0 || arrived) this.nextWaypoint()
    } else if (arrived || !this.allowed(m.target.x, m.target.y)) {
      this.nextWaypoint()
    }

    // Busy scans produce more discoveries than one manta can visit: show the
    // surplus as quick glints so the counter never lags far behind reality.
    if (this.plan.kind === 'seek' && this.queue > 2) {
      this.overflowTimer -= dt
      if (this.overflowTimer <= 0) {
        this.overflowTimer = 0.28
        this.revealAt(...this.pickPearlSpot(), false)
      }
    }

    // Periodic sonar sweeps while the scanner is working.
    if (this.mode === 'scanning') {
      this.sonarTimer -= dt
      if (this.sonarTimer <= 0) {
        this.sonarTimer = 3.4
        this.sonars.push({ x: m.x, y: this.env.seabedY(m.x), age: 0, pending: [] })
      }
    }

    for (const s of this.sonars) {
      s.age += dt
      const reach = s.age * SONAR_SPEED
      const passed = s.pending.filter((pt) => Math.abs(pt.x - s.x) <= reach)
      if (!passed.length) continue
      s.pending = s.pending.filter((pt) => !passed.includes(pt))
      for (const pt of passed) this.revealAt(pt.x, pt.y, false)
    }
    this.sonars = this.sonars.filter((s) => s.age < SONAR_LIFE || s.pending.length)

    m.update(dt, this.unit)
    this.pearls.update(dt)
  }

  /** Replay of a finished scan: one wave from the first pearl uncovers the rest. */
  private sendReplayWave(x: number, y: number) {
    this.replayWaveSent = true
    const wave: Sonar = { x, y, age: 0, pending: [] }
    const shown = Math.min(this.queue, Math.max(0, this.maxPearls() - this.revealed))
    for (let i = 0; i < shown; i++) {
      const [px, py] = this.pickPearlSpot()
      wave.pending.push({ x: px, y: py, far: false })
    }
    const { width, height } = this.env.size
    for (let i = shown; i < this.queue; i++) {
      wave.pending.push({ x: width * (0.05 + this.rand() * 0.9), y: height * 0.75, far: true })
    }
    this.sonars.push(wave)
  }

  // --- rendering ----------------------------------------------------------------

  private render(dt: number) {
    const ctx = this.ctx
    const { width, height, dpr } = this.env.size
    const m = this.manta
    if (!m) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    const { sx, sy } = this.pointer

    this.env.drawBack(ctx, this.time, dt, sx, sy)

    // Shadow on the sand when the manta swims low.
    const ground = this.env.seabedY(m.x)
    const altitude = ground - m.y
    const shadowAlpha = clamp(1 - altitude / (260 * this.unit), 0, 1) * 0.35 * (1 - m.depth * 0.6)
    this.env.drawSeabed(ctx, this.time)
    if (shadowAlpha > 0.01) {
      const r = m.scale * 0.9
      const g = ctx.createRadialGradient(m.x, ground + 6, 0, m.x, ground + 6, r)
      g.addColorStop(0, `rgba(1, 8, 12, ${shadowAlpha})`)
      g.addColorStop(1, 'rgba(1, 8, 12, 0)')
      ctx.fillStyle = g
      ctx.beginPath()
      ctx.ellipse(m.x, ground + 6, r, r * 0.22, 0, 0, TAU)
      ctx.fill()
    }

    this.drawSonars(ctx)
    this.pearls.draw(ctx, this.time, this.hovered)
    m.draw(ctx)
    this.env.drawFront(ctx, this.time, dt, sx, sy)

    // Soft vignette keeps the eye in the middle and the edges calm.
    const v = ctx.createRadialGradient(width * 0.55, height * 0.45, height * 0.3, width * 0.55, height * 0.45, Math.max(width, height) * 0.85)
    v.addColorStop(0, 'rgba(3, 10, 16, 0)')
    v.addColorStop(1, 'rgba(3, 10, 16, 0.55)')
    ctx.fillStyle = v
    ctx.fillRect(0, 0, width, height)
  }

  private drawSonars(ctx: CanvasRenderingContext2D) {
    ctx.save()
    ctx.lineWidth = 1
    for (const s of this.sonars) {
      const t = clamp(s.age / SONAR_LIFE, 0, 1)
      const reach = s.age * SONAR_SPEED
      const alpha = (1 - easeOutCubic(t)) * 0.4
      if (alpha <= 0.01) continue
      ctx.strokeStyle = `rgba(150, 222, 226, ${alpha})`
      ctx.beginPath()
      ctx.ellipse(s.x, s.y + 2, reach, reach * 0.12, 0, Math.PI, TAU)
      ctx.stroke()
      ctx.strokeStyle = `rgba(150, 222, 226, ${alpha * 0.45})`
      ctx.beginPath()
      ctx.ellipse(s.x, s.y + 2, reach, reach * 0.12, 0, 0, Math.PI)
      ctx.stroke()
    }
    ctx.restore()
  }
}
