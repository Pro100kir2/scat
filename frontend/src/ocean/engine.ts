import { Environment, type Size } from './environment'
import { Manta } from './manta'
import { clamp, damp, easeOutCubic, mulberry32, smoothstep, TAU } from './math'
import { PearlBed, type Pearl } from './pearls'

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
  | { kind: 'dig'; pearl: Pearl; time: number }

interface Sonar {
  x: number
  y: number
  age: number
  /** Pearls waiting for this wave to pass over them. */
  pending: { x: number; y: number; far: boolean }[]
}

const SONAR_SPEED = 260 // px/s across the seabed
const SONAR_LIFE = 3.2
/** How long the manta works the sand before the pearl is fully out. */
const DIG_SECONDS = 2.2
/** Gap kept between the manta's outline and the canvas edge, px. */
const EDGE_PAD = 4

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
    const prev = this.env.size
    this.canvas.width = Math.round(size.width * dpr)
    this.canvas.height = Math.round(size.height * dpr)
    this.env.resize(size)
    this.unit = clamp(Math.min(size.width, size.height * 1.3) / 900, 0.55, 1.25)

    // Pearls sit on the sand; keep them there after a resize.
    for (const p of this.pearls.pearls) {
      p.x = clamp(p.x, size.width * 0.05, size.width * 0.95)
      p.y = this.env.seabedY(p.x) + 2
    }

    const m = this.manta
    if (m && !this.opts.reducedMotion && prev.width > 1) {
      // Carry the manta and its goal along with the scene instead of leaving them off screen.
      const kx = size.width / prev.width
      const ky = size.height / prev.height
      m.x *= kx
      m.y *= ky
      m.target = { ...m.target, x: m.target.x * kx, y: m.target.y * ky }
      if (this.plan.kind === 'seek') {
        this.plan.x = clamp(this.plan.x * kx, ...this.reachableX())
        this.plan.y = this.env.seabedY(this.plan.x) + 2
      }
    }

    if (!this.manta) this.spawnManta()
    this.manta!.span = (this.lite ? 230 : 330) * this.unit
    if (this.opts.reducedMotion) this.placeStillManta(this.manta!)
    else if (m) m.target = { ...m.target, ...this.containPoint(m.target.x, m.target.y) }
    this.containManta(this.manta!)
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
    // Start near the right edge, already fully on screen, and glide left.
    const m = new Manta(width, height * (this.lite ? 0.66 : 0.4), Math.PI, this.rand)
    m.span = (this.lite ? 230 : 330) * this.unit
    m.speed = 70 * this.unit
    this.manta = m
    Object.assign(m, this.containPoint(m.x, m.y))
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
    this.containManta(m)
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

  /**
   * Conservative distance from the manta's centre to its outline, for choosing goals.
   * The exact outline depends on the pose, so containManta() enforces the hard limit.
   */
  private reach() {
    const half = (this.manta?.span ?? 0) * 0.5
    // Horizontal goals also leave room for the turn: the manta banks round in an arc.
    return { x: half * 1.1 + 40 * this.unit + EDGE_PAD, y: half * 0.8 + EDGE_PAD }
  }

  /** Horizontal range for the manta's centre that keeps its wings on screen. */
  private reachableX(): [number, number] {
    const { width } = this.env.size
    const lo = Math.max(width * 0.06, this.reach().x)
    const hi = Math.min(width * 0.94, width - this.reach().x)
    return lo <= hi ? [lo, hi] : [width / 2, width / 2]
  }

  /** Nearest point the manta can aim for without any part of it leaving the canvas. */
  private containPoint(x: number, y: number) {
    const { height } = this.env.size
    const r = this.reach()
    const top = Math.max(height * 0.12, r.y)
    const bottom = height - r.y
    return { x: clamp(x, ...this.reachableX()), y: top <= bottom ? clamp(y, top, bottom) : height / 2 }
  }

  /**
   * Hard guarantee: measure the pose actually drawn this frame and keep it inside the canvas.
   * Steering (containPoint/allowed) keeps this from engaging most of the time; when it does,
   * it only stops outward motion, so the manta slides along the edge rather than jumping.
   */
  /** Ease off the swimming speed when the outline nears a side edge while heading towards it. */
  private brakeNearEdge(m: Manta) {
    const { width } = this.env.size
    const heading = Math.cos(m.yaw)
    const b = m.bounds
    const gap = heading > 0 ? width - EDGE_PAD - (m.x + b.right) : m.x + b.left - EDGE_PAD
    const zone = 90 * this.unit
    if (gap >= zone || Math.abs(heading) < 0.2) return
    const limit = Math.max(8 * this.unit, (m.speed * Math.max(gap, 0)) / zone)
    m.speed = Math.min(m.speed, limit + (m.speed - limit) * (1 - Math.abs(heading)))
  }

  private containManta(m: Manta) {
    const { width, height } = this.env.size
    const b = m.pose()
    const fit = (v: number, lo: number, hi: number) => (lo <= hi ? clamp(v, lo, hi) : (lo + hi) / 2)
    const x = fit(m.x, EDGE_PAD - b.left, width - EDGE_PAD - b.right)
    const y = fit(m.y, EDGE_PAD - b.top, height - EDGE_PAD - b.bottom)
    const touched = x !== m.x || y !== m.y
    m.x = x
    m.y = y
    return touched
  }

  private allowed(x: number, y: number) {
    const { height } = this.env.size
    const [lo, hi] = this.reachableX()
    const r = this.reach()
    if (x < lo || x > hi || y < Math.max(height * 0.12, r.y) || y > Math.min(this.env.seabedY(x) - 40 * this.unit, height - r.y)) return false
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

    const [lo, hi] = this.reachableX()
    if (this.mode === 'idle' && this.rand() < 0.2) {
      const x = clamp(m.x + Math.cos(m.yaw) * (150 + this.rand() * 200) * this.unit, Math.max(lo, width * 0.1), Math.min(hi, width * 0.9))
      const y = this.env.seabedY(x) - 55 * this.unit
      if (this.allowed(x, y)) {
        this.plan = { kind: 'investigate', linger: 1.4 + this.rand() * 1.4 }
        m.target = { x, y, depth: 0.1 + this.rand() * 0.2, speed: 34 }
        return
      }
    }

    // Sample candidates and prefer gentle turns at a comfortable distance.
    let best = { ...this.containPoint(width * 0.7, height * 0.45), score: -Infinity }
    const heading = Math.cos(m.yaw)
    for (let i = 0; i < 10; i++) {
      const x = lo + this.rand() * (hi - lo)
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

  /** A free spot on the sand; `reachable` limits it to where the manta can hover without leaving the screen. */
  private pickPearlSpot(reachable = false): [number, number] {
    const { width } = this.env.size
    const [lo, hi] = reachable ? this.reachableX() : [width * 0.05, width * 0.95]
    const m = this.manta
    const dir = m ? Math.sign(Math.cos(m.yaw)) || 1 : 1
    for (let i = 0; i < 16; i++) {
      const ahead = m ? m.x + dir * (90 + this.rand() * 260) * this.unit : width * this.rand()
      const x = i < 8 ? ahead : lo + this.rand() * (hi - lo)
      if (x >= lo && x <= hi && this.isFreeSpot(x)) return [x, this.env.seabedY(x) + 2]
    }
    for (let i = 0; i < 24; i++) {
      const x = lo + this.rand() * (hi - lo)
      if (this.isFreeSpot(x)) return [x, this.env.seabedY(x) + 2]
    }
    const x = lo + (hi - lo) * (0.55 + this.rand() * 0.4)
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

  /** The manta reached the spot: bury the pearl and start digging it out. */
  private startDig(x: number, y: number) {
    // Past the cap discoveries are only distant glints; nothing to dig for.
    if (this.revealed >= this.maxPearls()) {
      this.revealAt(x, y, true)
      this.riseAfterFind(x, y)
      return
    }
    this.queue -= 1
    this.plan = { kind: 'dig', pearl: this.pearls.bury(x, y, this.revealed + 1), time: 0 }
  }

  /** The pearl is out of the sand: count it and carry on exactly as a plain reveal would. */
  private finishDig(pearl: Pearl) {
    this.revealed += 1
    this.pearls.unearth(pearl, this.revealed)
    this.sonars.push({ x: pearl.x, y: pearl.y, age: 0, pending: [] })
    this.opts.onReveal(this.revealed)
    this.riseAfterFind(pearl.x, pearl.y)
    this.checkDrained()
  }

  private riseAfterFind(x: number, y: number) {
    const m = this.manta!
    m.celebrate()
    this.plan = { kind: 'rise', time: 1.6 }
    m.target = { x: m.x + Math.cos(m.yaw) * 200 * this.unit, y: m.y - 150 * this.unit, depth: m.depth, speed: 90 }
    Object.assign(m.target, this.containPoint(m.target.x, m.target.y))
    if (this.mode === 'replay' && !this.replayWaveSent) this.sendReplayWave(x, y)
  }

  private checkDrained() {
    if (this.queue === 0 && this.plan.kind !== 'dig' && this.sonars.every((s) => s.pending.length === 0)) {
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
      m.target = { ...m.target, ...this.containPoint(m.x + away * 240 * this.unit, m.y - 60 * this.unit), speed: 84 }
    }

    // Discoveries: swim to the spot, dig the pearl out of the sand, then rise.
    if (this.queue > 0 && this.plan.kind !== 'seek' && this.plan.kind !== 'dig') {
      const outstanding = this.sonars.reduce((n, s) => n + s.pending.length, 0)
      if (this.queue > outstanding) {
        const [x, y] = this.pickPearlSpot(true)
        this.plan = { kind: 'seek', x, y, time: 0 }
      }
    }

    const plan = this.plan
    const arrived = m.distanceTo(m.target.x, m.target.y) < 36 * this.unit
    if (plan.kind === 'seek') {
      plan.time += dt
      // Recomputed every frame so a resize can't leave the goal off screen.
      const hover = this.containPoint(plan.x, plan.y - 48 * this.unit)
      m.target = { ...hover, depth: 0.12, speed: 58 }
      // Normally the manta gets there in 2–4 s; never let a missed approach stall the story.
      if (m.distanceTo(hover.x, hover.y) < 44 * this.unit || plan.time > 7) this.startDig(plan.x, plan.y)
    } else if (plan.kind === 'dig') {
      plan.time += dt
      const pearl = plan.pearl
      // Glide slowly and low over the spot, like the investigate linger, while the wings work the sand.
      m.target = { ...this.containPoint(m.x + Math.cos(m.yaw) * 40 * this.unit, pearl.y - 34 * this.unit), depth: 0.1, speed: 16 }
      const k = plan.time / DIG_SECONDS
      pearl.buried = 1 - smoothstep(0.2, 0.92, k)
      const effort = smoothstep(0, 0.15, k) * (1 - smoothstep(0.8, 1, k))
      this.pearls.stir(pearl.x, pearl.y, effort * (this.lite ? 26 : 44), this.unit, dt)
      if (plan.time >= DIG_SECONDS) this.finishDig(pearl)
    } else if (plan.kind === 'investigate') {
      if (arrived) {
        plan.linger -= dt
        m.target = { ...m.target, ...this.containPoint(m.x + Math.cos(m.yaw) * 60, m.target.y), speed: 18 }
        if (plan.linger <= 0) {
          this.plan = { kind: 'rise', time: 2 }
          m.target = { ...this.containPoint(m.x + Math.cos(m.yaw) * 220 * this.unit, m.y - 190 * this.unit), depth: m.depth, speed: 92 }
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
    if ((this.plan.kind === 'seek' || this.plan.kind === 'dig') && this.queue > 2) {
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

    this.brakeNearEdge(m)
    m.update(dt, this.unit)
    // Brushing an edge while wandering: pick a new heading instead of pressing against it.
    if (this.containManta(m) && this.plan.kind === 'wander') this.nextWaypoint()
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
