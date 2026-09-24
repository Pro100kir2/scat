/**
 * Everything around the manta. Static layers (water gradient, far rocks, seabed) are
 * painted once per resize into offscreen canvases; per frame we only blit them with a
 * parallax offset and draw the few things that really move.
 */
import { createNoise, lerp, TAU } from './math'

export interface Size {
  width: number
  height: number
  dpr: number
}

interface Mote {
  x: number
  y: number
  z: number // 0 far .. 1 near
  drift: number
}

interface Bubble {
  x: number
  y: number
  r: number
  speed: number
  wobble: number
}

interface Strand {
  x: number
  height: number
  width: number
  phase: number
  front: boolean
}

interface Fish {
  offset: number
  lane: number
  size: number
}

export const PAGE_BOTTOM = '#050d14' // must match --abyss in the stylesheet

function layer(size: Size) {
  const canvas = document.createElement('canvas')
  canvas.width = Math.ceil(size.width * size.dpr)
  canvas.height = Math.ceil(size.height * size.dpr)
  const ctx = canvas.getContext('2d')!
  ctx.scale(size.dpr, size.dpr)
  return { canvas, ctx }
}

export class Environment {
  size: Size = { width: 1, height: 1, dpr: 1 }
  private water!: HTMLCanvasElement
  private farRocks!: HTMLCanvasElement
  private seabed!: HTMLCanvasElement
  private rays!: HTMLCanvasElement
  private motes: Mote[] = []
  private bubbles: Bubble[] = []
  private strands: Strand[] = []
  private school: Fish[] = []
  private vents: number[] = []
  private readonly noise: (x: number) => number
  private readonly profile: (x: number) => number

  constructor(private readonly rand: () => number, private readonly lite: boolean) {
    this.noise = createNoise(rand)
    this.profile = createNoise(rand)
  }

  /** Sand line in px for a given x; gentle dunes. */
  seabedY(x: number) {
    const { width, height } = this.size
    const base = height * (width < 720 ? 0.86 : 0.83)
    return base + this.profile(x / 260) * 14 + this.profile(x / 90 + 40) * 4
  }

  resize(size: Size) {
    this.size = size
    this.paintWater()
    this.paintFarRocks()
    this.paintSeabed()
    this.paintRays()

    const area = (size.width * size.height) / 1_000_000
    const moteCount = Math.round((this.lite ? 45 : 110) * Math.min(1.6, Math.max(0.5, area)))
    this.motes = Array.from({ length: moteCount }, () => ({
      x: this.rand() * size.width,
      y: this.rand() * size.height,
      z: this.rand(),
      drift: this.rand() * 100,
    }))

    const strandCount = this.lite ? 7 : 14
    this.strands = Array.from({ length: strandCount }, (_, i) => ({
      x: ((i + this.rand() * 0.8) / strandCount) * size.width,
      height: 40 + this.rand() * (this.lite ? 70 : 130),
      width: 2.5 + this.rand() * 3.5,
      phase: this.rand() * 50,
      front: this.rand() < 0.22,
    }))

    this.vents = [0.18 + this.rand() * 0.2, 0.62 + this.rand() * 0.3].map((f) => f * size.width)
    this.bubbles = []
    this.school = Array.from({ length: this.lite ? 10 : 22 }, () => ({
      offset: this.rand() * 160,
      lane: this.rand() * 36,
      size: 3 + this.rand() * 3,
    }))
  }

  private paintWater() {
    const { width, height } = this.size
    const { canvas, ctx } = layer(this.size)
    const g = ctx.createLinearGradient(0, 0, 0, height)
    g.addColorStop(0, '#15475a')
    g.addColorStop(0.28, '#0c2f41')
    g.addColorStop(0.62, '#081d2b')
    g.addColorStop(1, '#050f18')
    ctx.fillStyle = g
    ctx.fillRect(0, 0, width, height)
    // Sun glow from the surface.
    const sun = ctx.createRadialGradient(width * 0.68, -height * 0.1, 0, width * 0.68, -height * 0.1, height * 0.9)
    sun.addColorStop(0, 'rgba(150, 214, 222, 0.28)')
    sun.addColorStop(1, 'rgba(150, 214, 222, 0)')
    ctx.fillStyle = sun
    ctx.fillRect(0, 0, width, height)
    this.water = canvas
  }

  private paintFarRocks() {
    const { width, height } = this.size
    const { canvas, ctx } = layer(this.size)
    const shapes = [
      { color: 'rgba(22, 62, 80, 0.5)', base: 0.72, amp: 70, freq: 230 },
      { color: 'rgba(13, 42, 57, 0.78)', base: 0.79, amp: 42, freq: 140 },
    ]
    for (const s of shapes) {
      const n = createNoise(this.rand)
      ctx.fillStyle = s.color
      ctx.beginPath()
      ctx.moveTo(-40, height)
      for (let x = -40; x <= width + 40; x += 8) {
        // Two octaves: broad hills plus a rougher crest.
        const ridge = (n(x / s.freq) * 0.5 + 0.5) ** 2 * s.amp * 1.6 + n(x / (s.freq * 0.22) + 17) * s.amp * 0.12
        ctx.lineTo(x, height * s.base - ridge)
      }
      ctx.lineTo(width + 40, height)
      ctx.fill()
    }
    this.farRocks = canvas
  }

  private paintSeabed() {
    const { width, height } = this.size
    const { canvas, ctx } = layer(this.size)
    const top = Math.min(...Array.from({ length: 20 }, (_, i) => this.seabedY((i / 19) * width)))
    const sand = ctx.createLinearGradient(0, top - 10, 0, height)
    sand.addColorStop(0, '#16303a')
    sand.addColorStop(0.35, '#0d2029')
    sand.addColorStop(1, PAGE_BOTTOM)
    ctx.fillStyle = sand
    ctx.beginPath()
    ctx.moveTo(0, height)
    for (let x = 0; x <= width; x += 6) ctx.lineTo(x, this.seabedY(x))
    ctx.lineTo(width, height)
    ctx.fill()

    // Light catching the crest of the sand.
    ctx.strokeStyle = 'rgba(120, 176, 186, 0.16)'
    ctx.lineWidth = 1
    ctx.beginPath()
    for (let x = 0; x <= width; x += 6) ctx.lineTo(x, this.seabedY(x) + 0.5)
    ctx.stroke()

    // Rocks half-buried in the sand.
    const rockCount = Math.round(width / 160)
    for (let i = 0; i < rockCount; i++) {
      const x = this.rand() * width
      const y = this.seabedY(x) + 6
      const w = 18 + this.rand() * 46
      const h = w * (0.35 + this.rand() * 0.3)
      const g = ctx.createLinearGradient(0, y - h, 0, y)
      g.addColorStop(0, '#1d3a45')
      g.addColorStop(1, '#0a1820')
      ctx.fillStyle = g
      ctx.beginPath()
      ctx.moveTo(x - w / 2, y)
      ctx.bezierCurveTo(x - w / 2, y - h * 0.9, x - w * 0.1, y - h * 1.1, x + w * 0.15, y - h)
      ctx.bezierCurveTo(x + w * 0.45, y - h * 0.9, x + w / 2, y - h * 0.4, x + w / 2, y)
      ctx.fill()
    }

    // Blend into the page below so the results section continues the seabed.
    const fade = ctx.createLinearGradient(0, height * 0.9, 0, height)
    fade.addColorStop(0, 'rgba(5, 13, 20, 0)')
    fade.addColorStop(1, PAGE_BOTTOM)
    ctx.fillStyle = fade
    ctx.fillRect(0, height * 0.9, width, height * 0.1)
    this.seabed = canvas
  }

  private paintRays() {
    const { width, height } = this.size
    const { canvas, ctx } = layer(this.size)
    const count = this.lite ? 4 : 7
    for (let i = 0; i < count; i++) {
      const x = width * (0.25 + (i / count) * 0.8) + this.rand() * 60
      const spread = 30 + this.rand() * 70
      const lean = height * (0.25 + this.rand() * 0.15)
      const g = ctx.createLinearGradient(0, 0, 0, height * 0.8)
      const a = 0.05 + this.rand() * 0.06
      g.addColorStop(0, `rgba(170, 225, 230, ${a})`)
      g.addColorStop(1, 'rgba(170, 225, 230, 0)')
      ctx.fillStyle = g
      ctx.beginPath()
      ctx.moveTo(x - spread * 0.2, 0)
      ctx.lineTo(x + spread * 0.2, 0)
      ctx.lineTo(x - lean + spread, height * 0.8)
      ctx.lineTo(x - lean - spread, height * 0.8)
      ctx.fill()
    }
    this.rays = canvas
  }

  /** Layers behind the manta. `px`/`py` are the smoothed pointer offsets in [-1, 1]. */
  drawBack(ctx: CanvasRenderingContext2D, time: number, dt: number, px: number, py: number) {
    const { width, height } = this.size
    ctx.drawImage(this.water, 0, 0, width, height)

    // Light shafts sway and breathe slowly.
    ctx.save()
    ctx.globalCompositeOperation = 'lighter'
    ctx.globalAlpha = 0.75 + this.noise(time * 0.07) * 0.25
    ctx.drawImage(this.rays, this.noise(time * 0.05 + 9) * 24 - px * 6, 0, width, height)
    ctx.restore()

    this.drawSchool(ctx, time)
    ctx.drawImage(this.farRocks, -px * 8, -py * 4, width, height)
    this.drawMotes(ctx, dt, px, py, false)
    this.drawBubbles(ctx, dt)
  }

  /** Seabed and the seaweed that grows behind the manta. */
  drawSeabed(ctx: CanvasRenderingContext2D, time: number) {
    const { width, height } = this.size
    ctx.drawImage(this.seabed, 0, 0, width, height)
    this.drawStrands(ctx, time, false)
  }

  /** Things in front of the manta: a few strands and the nearest particles. */
  drawFront(ctx: CanvasRenderingContext2D, time: number, dt: number, px: number, py: number) {
    this.drawStrands(ctx, time, true)
    this.drawMotes(ctx, dt, px, py, true)
  }

  /** Kelp: a chain of segments, each bending a little more than the one below it. */
  private drawStrands(ctx: CanvasRenderingContext2D, time: number, front: boolean) {
    const SEGMENTS = 9
    ctx.save()
    ctx.lineCap = 'round'
    ctx.strokeStyle = front ? 'rgba(6, 22, 24, 0.96)' : 'rgba(30, 78, 70, 0.7)'
    for (const s of this.strands) {
      if (s.front !== front) continue
      let x = s.x
      let y = this.seabedY(s.x) + 4
      let angle = -Math.PI / 2
      const step = s.height / SEGMENTS
      for (let i = 0; i < SEGMENTS; i++) {
        const k = i / SEGMENTS
        angle += (this.noise(time * 0.22 + s.phase + k * 1.4) * 0.16 + Math.sin(time * 0.8 + s.phase - k * 2.2) * 0.05) * (0.4 + k)
        const nx = x + Math.cos(angle) * step
        const ny = y + Math.sin(angle) * step
        ctx.lineWidth = s.width * (1 - k * 0.75)
        ctx.beginPath()
        ctx.moveTo(x, y)
        ctx.lineTo(nx, ny)
        ctx.stroke()
        x = nx
        y = ny
      }
    }
    ctx.restore()
  }

  private drawMotes(ctx: CanvasRenderingContext2D, dt: number, px: number, py: number, near: boolean) {
    const { width, height } = this.size
    ctx.save()
    for (const m of this.motes) {
      if ((m.z > 0.82) !== near) continue
      m.y += (4 + m.z * 8) * dt
      m.x += this.noise(m.drift + m.y * 0.01) * 6 * dt
      if (m.y > height) {
        m.y = -4
        m.x = this.rand() * width
      }
      const size = lerp(0.6, near ? 2.6 : 1.6, m.z)
      const x = m.x - px * lerp(2, 18, m.z)
      const y = m.y - py * lerp(1, 10, m.z)
      ctx.fillStyle = `rgba(190, 222, 228, ${lerp(0.12, near ? 0.3 : 0.4, m.z)})`
      ctx.fillRect(x, y, size, size)
    }
    ctx.restore()
  }

  private drawBubbles(ctx: CanvasRenderingContext2D, dt: number) {
    if (this.bubbles.length < (this.lite ? 5 : 12) && this.rand() < dt * 1.4) {
      const vent = this.vents[Math.floor(this.rand() * this.vents.length)]!
      this.bubbles.push({ x: vent, y: this.seabedY(vent), r: 1 + this.rand() * 2.2, speed: 22 + this.rand() * 26, wobble: this.rand() * TAU })
    }
    ctx.save()
    ctx.strokeStyle = 'rgba(200, 236, 240, 0.35)'
    ctx.lineWidth = 0.8
    for (const b of this.bubbles) {
      b.y -= b.speed * dt
      b.wobble += dt * 3
      b.r += dt * 0.08
      ctx.beginPath()
      ctx.arc(b.x + Math.sin(b.wobble) * 2.5, b.y, b.r, 0, TAU)
      ctx.stroke()
    }
    ctx.restore()
    this.bubbles = this.bubbles.filter((b) => b.y > this.size.height * 0.08)
  }

  /** A far-away school of fish drifting across: depth without distraction. */
  private drawSchool(ctx: CanvasRenderingContext2D, time: number) {
    const { width, height } = this.size
    const travel = width + 400
    const cx = ((time * 9) % travel) - 200
    const cy = height * 0.42 + this.noise(time * 0.05 + 3) * 40
    ctx.save()
    ctx.fillStyle = 'rgba(70, 120, 140, 0.22)'
    for (const f of this.school) {
      const x = width - (cx + f.offset + this.noise(time * 0.3 + f.offset) * 10)
      const y = cy + f.lane + this.noise(time * 0.2 + f.lane) * 8
      ctx.beginPath()
      ctx.ellipse(x, y, f.size, f.size * 0.35, 0, 0, TAU)
      ctx.fill()
    }
    ctx.restore()
  }
}
