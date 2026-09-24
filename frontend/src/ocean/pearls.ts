import { clamp, easeOutCubic, lerp, smoothstep, TAU } from './math'

const REVEAL_SECONDS = 1.6

export interface Pearl {
  x: number
  y: number
  radius: number
  /** Rank of the result this pearl stands for (1 = best). */
  rank: number
  age: number // seconds since the reveal started
  phase: number // breathing offset
  /** 1 = still under the sand, 0 = fully uncovered. Buried pearls are not interactive. */
  buried: number
  /** Dug out by the manta: it rises from the sand instead of growing in place. */
  dug: boolean
}

interface Glint {
  x: number
  y: number
  age: number
  size: number
}

/** Sand kicked up while the manta digs: fine grains plus a few soft clouds. */
interface Grain {
  x: number
  y: number
  vx: number
  vy: number
  age: number
  life: number
  size: number
  cloud: boolean
}

const radiusFor = (rank: number) => (rank === 1 ? 7.5 : clamp(6.6 - rank * 0.12, 4.2, 6.4))

/** Pearls resting on the seabed, plus far-away glints for discoveries past the cap. */
export class PearlBed {
  pearls: Pearl[] = []
  private glints: Glint[] = []
  private grains: Grain[] = []
  private grainDebt = 0
  private cloudTimer = 0

  constructor(private readonly rand: () => number) {}

  clear() {
    this.pearls = []
    this.glints = []
    this.grains = []
  }

  add(x: number, y: number, rank: number, instant: boolean) {
    this.pearls.push({ x, y, radius: radiusFor(rank), rank, age: instant ? REVEAL_SECONDS : 0, phase: this.rand() * TAU, buried: 0, dug: false })
  }

  /** A pearl still hidden under the sand; `rank` only sizes it until unearth() assigns the real one. */
  bury(x: number, y: number, rank: number): Pearl {
    const pearl: Pearl = { x, y, radius: radiusFor(rank), rank: 0, age: 0, phase: this.rand() * TAU, buried: 1, dug: true }
    this.pearls.push(pearl)
    return pearl
  }

  /** The pearl is fully out of the sand: give it its rank and play the usual discovery bloom. */
  unearth(pearl: Pearl, rank: number) {
    pearl.buried = 0
    pearl.rank = rank
    pearl.radius = radiusFor(rank)
    pearl.age = 0
  }

  /** Kick up sand at (x, y). `rate` is grains per second, `unit` the scene scale. */
  stir(x: number, y: number, rate: number, unit: number, dt: number) {
    this.grainDebt += rate * dt
    while (this.grainDebt >= 1) {
      this.grainDebt -= 1
      const side = this.rand() * 2 - 1
      this.grains.push({
        x: x + side * 10 * unit,
        y: y + 1,
        vx: side * (30 + this.rand() * 50) * unit,
        vy: -(26 + this.rand() * 58) * unit,
        age: 0,
        life: 0.8 + this.rand() * 0.8,
        size: (0.7 + this.rand() * 1.1) * Math.max(unit, 0.7),
        cloud: false,
      })
    }
    this.cloudTimer -= dt
    if (rate > 0 && this.cloudTimer <= 0) {
      this.cloudTimer = 0.22 + this.rand() * 0.12
      this.grains.push({
        x: x + (this.rand() * 2 - 1) * 14 * unit,
        y: y - 2,
        vx: (this.rand() * 2 - 1) * 12 * unit,
        vy: -(6 + this.rand() * 10) * unit,
        age: 0,
        life: 1.4 + this.rand() * 0.6,
        size: (16 + this.rand() * 12) * unit,
        cloud: true,
      })
    }
  }

  glint(x: number, y: number) {
    this.glints.push({ x, y, age: 0, size: 0.6 + this.rand() * 0.7 })
  }

  isFree(x: number, minGap: number) {
    return this.pearls.every((p) => Math.abs(p.x - x) > minGap)
  }

  hit(x: number, y: number): Pearl | null {
    for (const p of this.pearls) if (p.buried === 0 && Math.hypot(p.x - x, p.y - y) < p.radius + 14) return p
    return null
  }

  update(dt: number) {
    for (const p of this.pearls) if (p.buried === 0) p.age += dt
    for (const g of this.glints) g.age += dt
    this.glints = this.glints.filter((g) => g.age < 1.4)

    // Grains fly out, slow in the water and sink back; clouds swell and drift up.
    for (const g of this.grains) {
      g.age += dt
      const drag = Math.exp(-(g.cloud ? 0.8 : 1.8) * dt)
      g.vx *= drag
      g.vy = g.vy * drag + (g.cloud ? 0 : 60 * dt)
      g.x += g.vx * dt
      g.y += g.vy * dt
    }
    this.grains = this.grains.filter((g) => g.age < g.life)
  }

  draw(ctx: CanvasRenderingContext2D, time: number, hovered: number | null) {
    for (const g of this.glints) {
      const a = Math.sin(clamp(g.age / 1.4, 0, 1) * Math.PI)
      drawSparkle(ctx, g.x, g.y, 5 * g.size * (0.6 + a * 0.6), a * 0.75)
    }
    for (const g of this.grains) if (g.cloud) drawGrain(ctx, g)
    for (const p of this.pearls) drawPearl(ctx, p, time, p.buried === 0 && p.rank === hovered)
    for (const g of this.grains) if (!g.cloud) drawGrain(ctx, g)
  }
}

function drawGrain(ctx: CanvasRenderingContext2D, g: Grain) {
  const t = g.age / g.life
  if (g.cloud) {
    const r = g.size * (0.5 + easeOutCubic(t) * 0.8)
    const alpha = Math.sin(t * Math.PI) * 0.22
    const c = ctx.createRadialGradient(g.x, g.y, 0, g.x, g.y, r)
    c.addColorStop(0, `rgba(96, 138, 146, ${alpha})`)
    c.addColorStop(1, 'rgba(96, 138, 146, 0)')
    ctx.fillStyle = c
    ctx.fillRect(g.x - r, g.y - r, r * 2, r * 2)
    return
  }
  ctx.fillStyle = `rgba(168, 198, 196, ${Math.sin(t * Math.PI) * 0.8})`
  ctx.fillRect(g.x - g.size / 2, g.y - g.size / 2, g.size, g.size)
}

function drawSparkle(ctx: CanvasRenderingContext2D, x: number, y: number, size: number, alpha: number) {
  ctx.save()
  ctx.globalCompositeOperation = 'lighter'
  const g = ctx.createRadialGradient(x, y, 0, x, y, size * 2.2)
  g.addColorStop(0, `rgba(255, 246, 228, ${alpha})`)
  g.addColorStop(1, 'rgba(255, 246, 228, 0)')
  ctx.fillStyle = g
  ctx.fillRect(x - size * 2.2, y - size * 2.2, size * 4.4, size * 4.4)
  ctx.strokeStyle = `rgba(255, 250, 240, ${alpha * 0.8})`
  ctx.lineWidth = 0.8
  ctx.beginPath()
  ctx.moveTo(x - size, y)
  ctx.lineTo(x + size, y)
  ctx.moveTo(x, y - size)
  ctx.lineTo(x, y + size)
  ctx.stroke()
  ctx.restore()
}

function drawPearl(ctx: CanvasRenderingContext2D, p: Pearl, time: number, hovered: boolean) {
  const t = clamp(p.age / REVEAL_SECONDS, 0, 1)
  const grow = p.dug ? 1 : easeOutCubic(smoothstep(0.08, 0.55, t))
  if (grow <= 0) return
  const breathe = 0.5 + 0.5 * Math.sin(time * 1.3 + p.phase)
  const r = p.radius * grow * (hovered ? 1.18 : 1)
  // While buried the pearl sits lower and only the part above the sand line shows.
  // `cover` fades the sand lip out over the last stretch so nothing pops at the end.
  const cover = smoothstep(0, 0.35, p.buried)
  const cy = p.y + p.buried * r * 2.3

  ctx.save()
  // Discovery bloom: a ring of light spreading over the sand, then a lingering glow.
  if (t < 1 && p.buried === 0) {
    const ring = easeOutCubic(t)
    ctx.strokeStyle = `rgba(247, 236, 214, ${(1 - ring) * 0.55})`
    ctx.lineWidth = 1.2
    ctx.beginPath()
    ctx.ellipse(p.x, p.y + 1, 8 + ring * 70, 3 + ring * 16, 0, 0, TAU)
    ctx.stroke()
  }
  const flash = t < 1 && p.buried === 0 ? Math.sin(t * Math.PI) * 1.6 : 0
  const glowR = r * (4.2 + breathe * 1.2 + flash * 2 + (hovered ? 1.6 : 0))
  ctx.globalCompositeOperation = 'lighter'
  const glow = ctx.createRadialGradient(p.x, cy, 0, p.x, cy, glowR)
  const glowAlpha = (0.2 + breathe * 0.1 + flash * 0.25 + (hovered ? 0.15 : 0)) * (1 - 0.85 * p.buried)
  glow.addColorStop(0, `rgba(250, 236, 212, ${glowAlpha})`)
  glow.addColorStop(0.45, `rgba(214, 226, 240, ${glowAlpha * 0.3})`)
  glow.addColorStop(1, 'rgba(214, 226, 240, 0)')
  ctx.fillStyle = glow
  ctx.fillRect(p.x - glowR, cy - glowR, glowR * 2, glowR * 2)
  ctx.globalCompositeOperation = 'source-over'

  // Contact shadow in the sand.
  ctx.fillStyle = `rgba(2, 10, 16, ${0.45 * (1 - cover)})`
  ctx.beginPath()
  ctx.ellipse(p.x, p.y + r * 0.85, r * 1.3, r * 0.35, 0, 0, TAU)
  ctx.fill()

  // Sand line: drops below the pearl as it comes free, so the clip never cuts visibly at the end.
  const sandY = p.y + r * lerp(1.2, 0.3, cover)
  if (p.buried > 0) {
    ctx.save()
    ctx.beginPath()
    ctx.rect(p.x - r * 2, cy - r * 2, r * 4, sandY - (cy - r * 2))
    ctx.clip()
  }

  // Nacre: warm body, cool iridescent rim, soft specular highlight.
  const body = ctx.createRadialGradient(p.x - r * 0.35, cy - r * 0.4, r * 0.1, p.x, cy, r)
  body.addColorStop(0, '#fffaf1')
  body.addColorStop(0.45, '#efe3cf')
  body.addColorStop(0.8, '#c9cfd8')
  body.addColorStop(1, '#a7b8c6')
  ctx.fillStyle = body
  ctx.beginPath()
  ctx.arc(p.x, cy, r, 0, TAU)
  ctx.fill()
  ctx.strokeStyle = `rgba(242, 208, 222, ${0.35 + breathe * 0.2})`
  ctx.lineWidth = Math.max(0.6, r * 0.12)
  ctx.beginPath()
  ctx.arc(p.x, cy, r * 0.9, Math.PI * 0.15, Math.PI * 0.85)
  ctx.stroke()
  ctx.fillStyle = 'rgba(255, 255, 255, 0.9)'
  ctx.beginPath()
  ctx.ellipse(p.x - r * 0.35, cy - r * 0.42, r * 0.28, r * 0.18, -0.6, 0, TAU)
  ctx.fill()

  if (p.buried > 0) {
    ctx.restore()
    // Churned sand heaped around the hole; its lip hides the clip edge.
    const w = r * (1.5 + cover * 0.9)
    const h = r * 0.5 * cover
    ctx.fillStyle = '#16303a'
    ctx.beginPath()
    ctx.ellipse(p.x, sandY, w, h + 0.5, 0, 0, TAU)
    ctx.fill()
    ctx.strokeStyle = `rgba(120, 176, 186, ${0.35 * cover})`
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.ellipse(p.x, sandY, w, h + 0.5, 0, Math.PI * 1.05, Math.PI * 1.95)
    ctx.stroke()
  }
  ctx.restore()
}
