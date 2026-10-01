/**
 * Canvas 2D renderer for the shared ocean simulation (@scat/shared/ocean).
 * Static layers (water gradient, far rocks, seabed, light shafts) are painted once per resize
 * into offscreen canvases; per frame we only blit them with a parallax offset and draw the
 * few things that really move.
 */
import {
  glintLook,
  grainLook,
  OUTLINE,
  PAGE_BOTTOM,
  pearlLook,
  SAND_STOPS,
  SHOULDER_PATCH,
  smoothPath,
  TAIL_SEGMENTS,
  TAU,
  WATER_STOPS,
  type Manta,
  type OceanSim,
  type Pearl,
} from '@scat/shared/ocean'

type Ctx = CanvasRenderingContext2D

export class CanvasRenderer {
  private dpr = 1
  private water!: HTMLCanvasElement
  private farRocks!: HTMLCanvasElement
  private seabed!: HTMLCanvasElement
  private rays!: HTMLCanvasElement

  constructor(private readonly ctx: Ctx, private readonly sim: OceanSim) {}

  /** Repaint the static layers; call after the simulation was resized. */
  resize(dpr: number) {
    this.dpr = dpr
    this.water = this.paintWater()
    this.farRocks = this.paintFarRocks()
    this.seabed = this.paintSeabed()
    this.rays = this.paintRays()
  }

  private layer() {
    const { width, height } = this.sim.env.size
    const canvas = document.createElement('canvas')
    canvas.width = Math.ceil(width * this.dpr)
    canvas.height = Math.ceil(height * this.dpr)
    const ctx = canvas.getContext('2d')!
    ctx.scale(this.dpr, this.dpr)
    return { canvas, ctx }
  }

  private paintWater() {
    const { width, height } = this.sim.env.size
    const { canvas, ctx } = this.layer()
    const g = ctx.createLinearGradient(0, 0, 0, height)
    for (const [stop, color] of WATER_STOPS) g.addColorStop(stop, color)
    ctx.fillStyle = g
    ctx.fillRect(0, 0, width, height)
    // Sun glow from the surface.
    const sun = ctx.createRadialGradient(width * 0.68, -height * 0.1, 0, width * 0.68, -height * 0.1, height * 0.9)
    sun.addColorStop(0, 'rgba(150, 214, 222, 0.28)')
    sun.addColorStop(1, 'rgba(150, 214, 222, 0)')
    ctx.fillStyle = sun
    ctx.fillRect(0, 0, width, height)
    return canvas
  }

  private paintFarRocks() {
    const { width, height } = this.sim.env.size
    const { canvas, ctx } = this.layer()
    for (const ridge of this.sim.env.ridges) {
      ctx.fillStyle = ridge.color
      ctx.beginPath()
      ctx.moveTo(-40, height)
      for (let i = 0; i < ridge.points.length; i += 2) ctx.lineTo(ridge.points[i]!, ridge.points[i + 1]!)
      ctx.lineTo(width + 40, height)
      ctx.fill()
    }
    return canvas
  }

  private paintSeabed() {
    const env = this.sim.env
    const { width, height } = env.size
    const { canvas, ctx } = this.layer()
    const sand = ctx.createLinearGradient(0, env.sandTop - 10, 0, height)
    for (const [stop, color] of SAND_STOPS) sand.addColorStop(stop, color)
    ctx.fillStyle = sand
    ctx.beginPath()
    ctx.moveTo(0, height)
    for (let i = 0; i < env.sandLine.length; i += 2) ctx.lineTo(env.sandLine[i]!, env.sandLine[i + 1]!)
    ctx.lineTo(width, height)
    ctx.fill()

    // Light catching the crest of the sand.
    ctx.strokeStyle = 'rgba(120, 176, 186, 0.16)'
    ctx.lineWidth = 1
    ctx.beginPath()
    for (let i = 0; i < env.sandLine.length; i += 2) ctx.lineTo(env.sandLine[i]!, env.sandLine[i + 1]! + 0.5)
    ctx.stroke()

    for (const { x, y, w, h } of env.rocks) {
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
    return canvas
  }

  private paintRays() {
    const { height } = this.sim.env.size
    const { canvas, ctx } = this.layer()
    for (const { x, spread, lean, alpha } of this.sim.env.rays) {
      const g = ctx.createLinearGradient(0, 0, 0, height * 0.8)
      g.addColorStop(0, `rgba(170, 225, 230, ${alpha})`)
      g.addColorStop(1, 'rgba(170, 225, 230, 0)')
      ctx.fillStyle = g
      ctx.beginPath()
      ctx.moveTo(x - spread * 0.2, 0)
      ctx.lineTo(x + spread * 0.2, 0)
      ctx.lineTo(x - lean + spread, height * 0.8)
      ctx.lineTo(x - lean - spread, height * 0.8)
      ctx.fill()
    }
    return canvas
  }

  render() {
    const { ctx, sim } = this
    const env = sim.env
    const { width, height } = env.size
    const m = sim.manta
    if (!m) return
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0)
    const { sx, sy } = sim.pointer
    const time = sim.time

    // --- behind the manta
    ctx.drawImage(this.water, 0, 0, width, height)
    const rays = env.raysLook(time, sx)
    ctx.save()
    ctx.globalCompositeOperation = 'lighter'
    ctx.globalAlpha = rays.alpha
    ctx.drawImage(this.rays, rays.dx, 0, width, height)
    ctx.restore()

    ctx.fillStyle = 'rgba(70, 120, 140, 0.22)'
    env.schoolFish(time, (x, y, size) => {
      ctx.beginPath()
      ctx.ellipse(x, y, size, size * 0.35, 0, 0, TAU)
      ctx.fill()
    })
    ctx.drawImage(this.farRocks, -sx * 8, -sy * 4, width, height)
    this.drawMotes(false)

    ctx.save()
    ctx.strokeStyle = 'rgba(200, 236, 240, 0.35)'
    ctx.lineWidth = 0.8
    for (const b of env.bubbles) {
      ctx.beginPath()
      ctx.arc(b.x + Math.sin(b.wobble) * 2.5, b.y, b.r, 0, TAU)
      ctx.stroke()
    }
    ctx.restore()

    // --- the seabed, the manta's shadow on it, sonar, pearls
    ctx.drawImage(this.seabed, 0, 0, width, height)
    this.drawStrands(false)
    const shadow = sim.shadow()
    if (shadow) {
      const g = ctx.createRadialGradient(shadow.x, shadow.y, 0, shadow.x, shadow.y, shadow.rx)
      g.addColorStop(0, `rgba(1, 8, 12, ${shadow.alpha})`)
      g.addColorStop(1, 'rgba(1, 8, 12, 0)')
      ctx.fillStyle = g
      ctx.beginPath()
      ctx.ellipse(shadow.x, shadow.y, shadow.rx, shadow.ry, 0, 0, TAU)
      ctx.fill()
    }
    this.drawSonars()
    this.drawPearls()
    drawManta(ctx, m)

    // --- in front of the manta
    this.drawStrands(true)
    this.drawMotes(true)

    // Soft vignette keeps the eye in the middle and the edges calm.
    const v = ctx.createRadialGradient(width * 0.55, height * 0.45, height * 0.3, width * 0.55, height * 0.45, Math.max(width, height) * 0.85)
    v.addColorStop(0, 'rgba(3, 10, 16, 0)')
    v.addColorStop(1, 'rgba(3, 10, 16, 0.55)')
    ctx.fillStyle = v
    ctx.fillRect(0, 0, width, height)
  }

  private drawStrands(front: boolean) {
    const { ctx, sim } = this
    ctx.save()
    ctx.lineCap = 'round'
    ctx.strokeStyle = front ? 'rgba(6, 22, 24, 0.96)' : 'rgba(30, 78, 70, 0.7)'
    for (const s of sim.env.strands) {
      if (s.front !== front) continue
      sim.env.strandSegments(s, sim.time, (x0, y0, x1, y1, w) => {
        ctx.lineWidth = w
        ctx.beginPath()
        ctx.moveTo(x0, y0)
        ctx.lineTo(x1, y1)
        ctx.stroke()
      })
    }
    ctx.restore()
  }

  private drawMotes(near: boolean) {
    const { ctx, sim } = this
    const { sx, sy } = sim.pointer
    for (const mote of sim.env.motes) {
      const look = sim.env.moteLook(mote, sx, sy)
      if (look.near !== near) continue
      ctx.fillStyle = `rgba(190, 222, 228, ${look.alpha})`
      ctx.fillRect(look.x, look.y, look.size, look.size)
    }
  }

  private drawSonars() {
    const { ctx, sim } = this
    ctx.save()
    ctx.lineWidth = 1
    for (const s of sim.sonars) {
      const look = sim.sonarLook(s)
      if (look.alpha <= 0.01) continue
      ctx.strokeStyle = `rgba(150, 222, 226, ${look.alpha})`
      ctx.beginPath()
      ctx.ellipse(look.x, look.y, look.rx, look.ry, 0, Math.PI, TAU)
      ctx.stroke()
      ctx.strokeStyle = `rgba(150, 222, 226, ${look.alpha * 0.45})`
      ctx.beginPath()
      ctx.ellipse(look.x, look.y, look.rx, look.ry, 0, 0, Math.PI)
      ctx.stroke()
    }
    ctx.restore()
  }

  private drawPearls() {
    const { ctx, sim } = this
    const bed = sim.pearls
    for (const g of bed.glints) {
      const look = glintLook(g)
      drawSparkle(ctx, g.x, g.y, look.size, look.alpha)
    }
    for (const g of bed.grains) if (g.cloud) this.drawGrain(g)
    for (const p of bed.pearls) drawPearl(ctx, p, sim.time, p.buried === 0 && p.rank === sim.hovered)
    for (const g of bed.grains) if (!g.cloud) this.drawGrain(g)
  }

  private drawGrain(g: OceanSim['pearls']['grains'][number]) {
    const ctx = this.ctx
    const look = grainLook(g)
    if (g.cloud) {
      const c = ctx.createRadialGradient(g.x, g.y, 0, g.x, g.y, look.r)
      c.addColorStop(0, `rgba(96, 138, 146, ${look.alpha})`)
      c.addColorStop(1, 'rgba(96, 138, 146, 0)')
      ctx.fillStyle = c
      ctx.fillRect(g.x - look.r, g.y - look.r, look.r * 2, look.r * 2)
      return
    }
    ctx.fillStyle = `rgba(168, 198, 196, ${look.alpha})`
    ctx.fillRect(g.x - g.size / 2, g.y - g.size / 2, g.size, g.size)
  }
}

function tracePath(ctx: Ctx, pts: ArrayLike<number>, count: number) {
  ctx.beginPath()
  smoothPath(pts, count, (x, y) => ctx.moveTo(x, y), (cx, cy, x, y) => ctx.quadraticCurveTo(cx, cy, x, y))
  ctx.closePath()
}

function drawManta(ctx: Ctx, m: Manta) {
  m.pose()
  const look = m.look()

  ctx.save()
  ctx.lineCap = 'round'
  ctx.strokeStyle = look.tailColor
  for (let i = 0; i < TAIL_SEGMENTS; i++) {
    ctx.lineWidth = look.tailWidth(i)
    ctx.beginPath()
    ctx.moveTo(m.tail[i * 2]!, m.tail[i * 2 + 1]!)
    ctx.lineTo(m.tail[i * 2 + 2]!, m.tail[i * 2 + 3]!)
    ctx.stroke()
  }

  // Body: one smooth path, shaded across the span so each wing catches its own light.
  const gradient = ctx.createLinearGradient(look.tipLeft.x, look.tipLeft.y, look.tipRight.x, look.tipRight.y)
  gradient.addColorStop(0, look.left)
  gradient.addColorStop(0.38, look.left)
  gradient.addColorStop(0.62, look.right)
  gradient.addColorStop(1, look.right)
  ctx.fillStyle = gradient
  tracePath(ctx, m.pts, OUTLINE.length)
  ctx.fill()

  // Shoulder patches, visible when the back faces us.
  if (look.back > 0.05) {
    ctx.fillStyle = `rgba(214, 226, 228, ${look.patchAlpha})`
    for (const side of [1, -1] as const) {
      tracePath(ctx, m.shoulderPatch(side), SHOULDER_PATCH.length)
      ctx.fill()
    }
  }

  // Rim light: sunlight from the surface catches whichever edges face up on screen.
  const rim = ctx.createLinearGradient(0, look.rimTop, 0, look.rimBottom)
  rim.addColorStop(0, `rgba(200, 236, 242, ${0.62 * look.rimAlpha})`)
  rim.addColorStop(0.45, `rgba(200, 236, 242, ${0.14 * look.rimAlpha})`)
  rim.addColorStop(1, 'rgba(200, 236, 242, 0)')
  ctx.strokeStyle = rim
  ctx.lineWidth = look.rimWidth
  tracePath(ctx, m.pts, OUTLINE.length)
  ctx.stroke()
  ctx.restore()
}

function drawSparkle(ctx: Ctx, x: number, y: number, size: number, alpha: number) {
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

function drawPearl(ctx: Ctx, p: Pearl, time: number, hovered: boolean) {
  const look = pearlLook(p, time, hovered)
  if (!look.visible) return
  const { r, cy } = look

  ctx.save()
  if (look.bloom) {
    ctx.strokeStyle = `rgba(247, 236, 214, ${look.bloom.alpha})`
    ctx.lineWidth = 1.2
    ctx.beginPath()
    ctx.ellipse(p.x, p.y + 1, look.bloom.rx, look.bloom.ry, 0, 0, TAU)
    ctx.stroke()
  }
  ctx.globalCompositeOperation = 'lighter'
  const glow = ctx.createRadialGradient(p.x, cy, 0, p.x, cy, look.glowR)
  glow.addColorStop(0, `rgba(250, 236, 212, ${look.glowAlpha})`)
  glow.addColorStop(0.45, `rgba(214, 226, 240, ${look.glowAlpha * 0.3})`)
  glow.addColorStop(1, 'rgba(214, 226, 240, 0)')
  ctx.fillStyle = glow
  ctx.fillRect(p.x - look.glowR, cy - look.glowR, look.glowR * 2, look.glowR * 2)
  ctx.globalCompositeOperation = 'source-over'

  // Contact shadow in the sand.
  ctx.fillStyle = `rgba(2, 10, 16, ${look.shadowAlpha})`
  ctx.beginPath()
  ctx.ellipse(p.x, p.y + r * 0.85, r * 1.3, r * 0.35, 0, 0, TAU)
  ctx.fill()

  // While buried, only the part above the sand line shows.
  if (p.buried > 0) {
    ctx.save()
    ctx.beginPath()
    ctx.rect(p.x - r * 2, cy - r * 2, r * 4, look.sandY - (cy - r * 2))
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
  ctx.strokeStyle = `rgba(242, 208, 222, ${look.rimAlpha})`
  ctx.lineWidth = Math.max(0.6, r * 0.12)
  ctx.beginPath()
  ctx.arc(p.x, cy, r * 0.9, Math.PI * 0.15, Math.PI * 0.85)
  ctx.stroke()
  ctx.fillStyle = 'rgba(255, 255, 255, 0.9)'
  ctx.beginPath()
  ctx.ellipse(p.x - r * 0.35, cy - r * 0.42, r * 0.28, r * 0.18, -0.6, 0, TAU)
  ctx.fill()

  if (look.mound) {
    ctx.restore()
    // Churned sand heaped around the hole; its lip hides the clip edge.
    ctx.fillStyle = '#16303a'
    ctx.beginPath()
    ctx.ellipse(p.x, look.sandY, look.mound.w, look.mound.h, 0, 0, TAU)
    ctx.fill()
    ctx.strokeStyle = `rgba(120, 176, 186, ${look.mound.lipAlpha})`
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.ellipse(p.x, look.sandY, look.mound.w, look.mound.h, 0, Math.PI * 1.05, Math.PI * 1.95)
    ctx.stroke()
  }
  ctx.restore()
}
