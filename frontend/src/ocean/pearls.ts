import { clamp, easeOutCubic, smoothstep, TAU } from './math'

const REVEAL_SECONDS = 1.6

export interface Pearl {
  x: number
  y: number
  radius: number
  /** Rank of the result this pearl stands for (1 = best). */
  rank: number
  age: number // seconds since the reveal started
  phase: number // breathing offset
}

interface Glint {
  x: number
  y: number
  age: number
  size: number
}

/** Pearls resting on the seabed, plus far-away glints for discoveries past the cap. */
export class PearlBed {
  pearls: Pearl[] = []
  private glints: Glint[] = []

  constructor(private readonly rand: () => number) {}

  clear() {
    this.pearls = []
    this.glints = []
  }

  add(x: number, y: number, rank: number, instant: boolean) {
    const radius = rank === 1 ? 7.5 : clamp(6.6 - rank * 0.12, 4.2, 6.4)
    this.pearls.push({ x, y, radius, rank, age: instant ? REVEAL_SECONDS : 0, phase: this.rand() * TAU })
  }

  glint(x: number, y: number) {
    this.glints.push({ x, y, age: 0, size: 0.6 + this.rand() * 0.7 })
  }

  isFree(x: number, minGap: number) {
    return this.pearls.every((p) => Math.abs(p.x - x) > minGap)
  }

  hit(x: number, y: number): Pearl | null {
    for (const p of this.pearls) if (Math.hypot(p.x - x, p.y - y) < p.radius + 14) return p
    return null
  }

  update(dt: number) {
    for (const p of this.pearls) p.age += dt
    for (const g of this.glints) g.age += dt
    this.glints = this.glints.filter((g) => g.age < 1.4)
  }

  draw(ctx: CanvasRenderingContext2D, time: number, hovered: number | null) {
    for (const g of this.glints) {
      const a = Math.sin(clamp(g.age / 1.4, 0, 1) * Math.PI)
      drawSparkle(ctx, g.x, g.y, 5 * g.size * (0.6 + a * 0.6), a * 0.75)
    }
    for (const p of this.pearls) drawPearl(ctx, p, time, p.rank === hovered)
  }
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
  const grow = easeOutCubic(smoothstep(0.08, 0.55, t))
  if (grow <= 0) return
  const breathe = 0.5 + 0.5 * Math.sin(time * 1.3 + p.phase)
  const r = p.radius * grow * (hovered ? 1.18 : 1)

  ctx.save()
  // Discovery bloom: a ring of light spreading over the sand, then a lingering glow.
  if (t < 1) {
    const ring = easeOutCubic(t)
    ctx.strokeStyle = `rgba(247, 236, 214, ${(1 - ring) * 0.55})`
    ctx.lineWidth = 1.2
    ctx.beginPath()
    ctx.ellipse(p.x, p.y + 1, 8 + ring * 70, 3 + ring * 16, 0, 0, TAU)
    ctx.stroke()
  }
  const flash = t < 1 ? Math.sin(t * Math.PI) * 1.6 : 0
  const glowR = r * (4.2 + breathe * 1.2 + flash * 2 + (hovered ? 1.6 : 0))
  ctx.globalCompositeOperation = 'lighter'
  const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, glowR)
  const glowAlpha = 0.2 + breathe * 0.1 + flash * 0.25 + (hovered ? 0.15 : 0)
  glow.addColorStop(0, `rgba(250, 236, 212, ${glowAlpha})`)
  glow.addColorStop(0.45, `rgba(214, 226, 240, ${glowAlpha * 0.3})`)
  glow.addColorStop(1, 'rgba(214, 226, 240, 0)')
  ctx.fillStyle = glow
  ctx.fillRect(p.x - glowR, p.y - glowR, glowR * 2, glowR * 2)
  ctx.globalCompositeOperation = 'source-over'

  // Contact shadow in the sand.
  ctx.fillStyle = 'rgba(2, 10, 16, 0.45)'
  ctx.beginPath()
  ctx.ellipse(p.x, p.y + r * 0.85, r * 1.3, r * 0.35, 0, 0, TAU)
  ctx.fill()

  // Nacre: warm body, cool iridescent rim, soft specular highlight.
  const body = ctx.createRadialGradient(p.x - r * 0.35, p.y - r * 0.4, r * 0.1, p.x, p.y, r)
  body.addColorStop(0, '#fffaf1')
  body.addColorStop(0.45, '#efe3cf')
  body.addColorStop(0.8, '#c9cfd8')
  body.addColorStop(1, '#a7b8c6')
  ctx.fillStyle = body
  ctx.beginPath()
  ctx.arc(p.x, p.y, r, 0, TAU)
  ctx.fill()
  ctx.strokeStyle = `rgba(242, 208, 222, ${0.35 + breathe * 0.2})`
  ctx.lineWidth = Math.max(0.6, r * 0.12)
  ctx.beginPath()
  ctx.arc(p.x, p.y, r * 0.9, Math.PI * 0.15, Math.PI * 0.85)
  ctx.stroke()
  ctx.fillStyle = 'rgba(255, 255, 255, 0.9)'
  ctx.beginPath()
  ctx.ellipse(p.x - r * 0.35, p.y - r * 0.42, r * 0.28, r * 0.18, -0.6, 0, TAU)
  ctx.fill()
  ctx.restore()
}
