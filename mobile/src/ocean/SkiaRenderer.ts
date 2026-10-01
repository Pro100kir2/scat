/**
 * Skia renderer for the shared ocean simulation (@scat/shared/ocean): the same scene as the
 * site's Canvas 2D renderer (frontend/src/ocean/canvasRenderer.ts), drawn with Skia.
 *
 * Built for phones:
 * - the static layers (water, light shafts, far rocks, seabed) are recorded once per resize
 *   as pictures and only replayed, with a parallax offset, every frame;
 * - soft glows reuse a few unit-sized gradient shaders, placed with canvas transforms,
 *   instead of creating a gradient per pearl per frame;
 * - paints are reused.
 */
import {
  BlendMode,
  ClipOp,
  createPicture,
  PaintStyle,
  Skia,
  StrokeCap,
  TileMode,
  type SkCanvas,
  type SkColor,
  type SkPaint,
  type SkPicture,
  type SkShader,
} from '@shopify/react-native-skia'
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
  WATER_STOPS,
  type Manta,
  type OceanSim,
  type Pearl,
} from '@scat/shared/ocean'

const DEG = 180 / Math.PI
const c = (css: string): SkColor => Skia.Color(css)
const rect = (x: number, y: number, w: number, h: number) => Skia.XYWHRect(x, y, w, h)
const UNIT = rect(-1, -1, 2, 2)

/** A radial gradient of radius 1 around the origin; scaled and moved by the canvas. */
function unitRadial(colors: string[], pos: number[] | null = null): SkShader {
  return Skia.Shader.MakeRadialGradient({ x: 0, y: 0 }, 1, colors.map(c), pos, TileMode.Clamp)
}

export class SkiaRenderer {
  private water: SkPicture | null = null
  private rays: SkPicture | null = null
  private farRocks: SkPicture | null = null
  private seabed: SkPicture | null = null
  private vignette: SkShader | null = null

  private readonly fill: SkPaint
  private readonly stroke: SkPaint
  private readonly shaded: SkPaint
  private readonly layer: SkPaint

  // Unit-sized glows, placed with transforms.
  private readonly shadowGlow = unitRadial(['rgba(1, 8, 12, 1)', 'rgba(1, 8, 12, 0)'])
  private readonly cloudGlow = unitRadial(['rgba(96, 138, 146, 1)', 'rgba(96, 138, 146, 0)'])
  private readonly sparkleGlow = unitRadial(['rgba(255, 246, 228, 1)', 'rgba(255, 246, 228, 0)'])
  private readonly pearlGlow = unitRadial(['rgba(250, 236, 212, 1)', 'rgba(214, 226, 240, 0.3)', 'rgba(214, 226, 240, 0)'], [0, 0.45, 1])
  /** Nacre: warm body, cool rim; the light sits up and to the left. */
  private readonly nacre = Skia.Shader.MakeTwoPointConicalGradient(
    { x: -0.35, y: -0.4 }, 0.1, { x: 0, y: 0 }, 1,
    ['#fffaf1', '#efe3cf', '#c9cfd8', '#a7b8c6'].map(c), [0, 0.45, 0.8, 1], TileMode.Clamp,
  )

  constructor(private readonly sim: OceanSim) {
    this.fill = Skia.Paint()
    this.fill.setAntiAlias(true)
    this.stroke = Skia.Paint()
    this.stroke.setAntiAlias(true)
    this.stroke.setStyle(PaintStyle.Stroke)
    this.stroke.setStrokeCap(StrokeCap.Round)
    this.shaded = Skia.Paint()
    this.shaded.setAntiAlias(true)
    this.layer = Skia.Paint()
  }

  /** Re-record the static layers; call after the simulation was resized. */
  resize() {
    const { width, height } = this.sim.env.size
    const bounds = rect(0, 0, width, height)
    this.dispose()
    this.water = createPicture((canvas) => this.paintWater(canvas), bounds)
    this.rays = createPicture((canvas) => this.paintRays(canvas), bounds)
    this.farRocks = createPicture((canvas) => this.paintFarRocks(canvas), bounds)
    this.seabed = createPicture((canvas) => this.paintSeabed(canvas), bounds)
    const centre = { x: width * 0.55, y: height * 0.45 }
    this.vignette = Skia.Shader.MakeTwoPointConicalGradient(
      centre, height * 0.3, centre, Math.max(width, height) * 0.85,
      [c('rgba(3, 10, 16, 0)'), c('rgba(3, 10, 16, 0.55)')], null, TileMode.Clamp,
    )
  }

  dispose() {
    for (const picture of [this.water, this.rays, this.farRocks, this.seabed]) picture?.dispose()
    this.water = this.rays = this.farRocks = this.seabed = null
  }

  /** One frame of the scene as a picture for <Picture />. */
  render(): SkPicture | null {
    if (!this.water || !this.sim.manta) return null
    const { width, height } = this.sim.env.size
    return createPicture((canvas) => this.draw(canvas), rect(0, 0, width, height))
  }

  // --- static layers -------------------------------------------------------------

  private paintWater(canvas: SkCanvas) {
    const { width, height } = this.sim.env.size
    const paint = Skia.Paint()
    paint.setShader(Skia.Shader.MakeLinearGradient(
      { x: 0, y: 0 }, { x: 0, y: height }, WATER_STOPS.map(([, color]) => c(color)), WATER_STOPS.map(([stop]) => stop), TileMode.Clamp,
    ))
    canvas.drawRect(rect(0, 0, width, height), paint)
    // Sun glow from the surface.
    paint.setShader(Skia.Shader.MakeRadialGradient(
      { x: width * 0.68, y: -height * 0.1 }, height * 0.9,
      [c('rgba(150, 214, 222, 0.28)'), c('rgba(150, 214, 222, 0)')], null, TileMode.Clamp,
    ))
    canvas.drawRect(rect(0, 0, width, height), paint)
  }

  private paintRays(canvas: SkCanvas) {
    const { height } = this.sim.env.size
    const paint = Skia.Paint()
    paint.setAntiAlias(true)
    for (const { x, spread, lean, alpha } of this.sim.env.rays) {
      paint.setShader(Skia.Shader.MakeLinearGradient(
        { x: 0, y: 0 }, { x: 0, y: height * 0.8 },
        [c(`rgba(170, 225, 230, ${alpha})`), c('rgba(170, 225, 230, 0)')], null, TileMode.Clamp,
      ))
      const path = Skia.PathBuilder.Make()
      path.moveTo(x - spread * 0.2, 0)
      path.lineTo(x + spread * 0.2, 0)
      path.lineTo(x - lean + spread, height * 0.8)
      path.lineTo(x - lean - spread, height * 0.8)
      path.close()
      canvas.drawPath(path.detach(), paint)
    }
  }

  private paintFarRocks(canvas: SkCanvas) {
    const { width, height } = this.sim.env.size
    const paint = Skia.Paint()
    paint.setAntiAlias(true)
    for (const ridge of this.sim.env.ridges) {
      paint.setColor(c(ridge.color))
      const path = Skia.PathBuilder.Make()
      path.moveTo(-40, height)
      for (let i = 0; i < ridge.points.length; i += 2) path.lineTo(ridge.points[i]!, ridge.points[i + 1]!)
      path.lineTo(width + 40, height)
      path.close()
      canvas.drawPath(path.detach(), paint)
    }
  }

  private paintSeabed(canvas: SkCanvas) {
    const env = this.sim.env
    const { width, height } = env.size
    const paint = Skia.Paint()
    paint.setAntiAlias(true)
    paint.setShader(Skia.Shader.MakeLinearGradient(
      { x: 0, y: env.sandTop - 10 }, { x: 0, y: height }, SAND_STOPS.map(([, color]) => c(color)), SAND_STOPS.map(([stop]) => stop), TileMode.Clamp,
    ))
    const sand = Skia.PathBuilder.Make()
    sand.moveTo(0, height)
    for (let i = 0; i < env.sandLine.length; i += 2) sand.lineTo(env.sandLine[i]!, env.sandLine[i + 1]!)
    sand.lineTo(width, height)
    sand.close()
    canvas.drawPath(sand.detach(), paint)

    // Light catching the crest of the sand.
    const crest = Skia.PathBuilder.Make()
    for (let i = 0; i < env.sandLine.length; i += 2) {
      if (i === 0) crest.moveTo(env.sandLine[i]!, env.sandLine[i + 1]! + 0.5)
      else crest.lineTo(env.sandLine[i]!, env.sandLine[i + 1]! + 0.5)
    }
    const line = Skia.Paint()
    line.setAntiAlias(true)
    line.setStyle(PaintStyle.Stroke)
    line.setStrokeWidth(1)
    line.setColor(c('rgba(120, 176, 186, 0.16)'))
    canvas.drawPath(crest.detach(), line)

    // Rocks half-buried in the sand.
    for (const { x, y, w, h } of env.rocks) {
      paint.setShader(Skia.Shader.MakeLinearGradient({ x: 0, y: y - h }, { x: 0, y }, [c('#1d3a45'), c('#0a1820')], null, TileMode.Clamp))
      const rock = Skia.PathBuilder.Make()
      rock.moveTo(x - w / 2, y)
      rock.cubicTo(x - w / 2, y - h * 0.9, x - w * 0.1, y - h * 1.1, x + w * 0.15, y - h)
      rock.cubicTo(x + w * 0.45, y - h * 0.9, x + w / 2, y - h * 0.4, x + w / 2, y)
      rock.close()
      canvas.drawPath(rock.detach(), paint)
    }

    // Blend into the screen below so the results continue the seabed.
    paint.setShader(Skia.Shader.MakeLinearGradient(
      { x: 0, y: height * 0.9 }, { x: 0, y: height }, [c('rgba(5, 13, 20, 0)'), c(PAGE_BOTTOM)], null, TileMode.Clamp,
    ))
    canvas.drawRect(rect(0, height * 0.9, width, height * 0.1), paint)
  }

  // --- the frame -------------------------------------------------------------------

  private draw(canvas: SkCanvas) {
    const { sim, fill, stroke } = this
    const env = sim.env
    const { width, height } = env.size
    const m = sim.manta!
    const { sx, sy } = sim.pointer
    const time = sim.time

    // Behind the manta: water, swaying light, a far school of fish, rocks, marine snow, bubbles.
    canvas.drawPicture(this.water!)
    const rays = env.raysLook(time, sx)
    this.layer.setBlendMode(BlendMode.Plus)
    this.layer.setAlphaf(Math.max(0, Math.min(1, rays.alpha)))
    canvas.saveLayer(this.layer)
    canvas.translate(rays.dx, 0)
    canvas.drawPicture(this.rays!)
    canvas.restore()

    fill.setColor(c('rgba(70, 120, 140, 0.22)'))
    env.schoolFish(time, (x, y, size) => canvas.drawOval(rect(x - size, y - size * 0.35, size * 2, size * 0.7), fill))

    canvas.save()
    canvas.translate(-sx * 8, -sy * 4)
    canvas.drawPicture(this.farRocks!)
    canvas.restore()
    this.drawMotes(canvas, false)

    stroke.setColor(c('rgba(200, 236, 240, 0.35)'))
    stroke.setStrokeWidth(0.8)
    for (const b of env.bubbles) canvas.drawCircle(b.x + Math.sin(b.wobble) * 2.5, b.y, b.r, stroke)

    // The seabed, kelp behind the manta, its shadow, sonar, pearls.
    canvas.drawPicture(this.seabed!)
    this.drawStrands(canvas, false)
    const shadow = sim.shadow()
    if (shadow) this.glow(canvas, this.shadowGlow, shadow.x, shadow.y, shadow.rx, shadow.ry, shadow.alpha)
    this.drawSonars(canvas)
    this.drawPearls(canvas)
    this.drawManta(canvas, m)

    // In front of the manta: a few strands and the nearest particles.
    this.drawStrands(canvas, true)
    this.drawMotes(canvas, true)

    // Soft vignette keeps the eye in the middle and the edges calm.
    this.shaded.setShader(this.vignette)
    this.shaded.setAlphaf(1)
    this.shaded.setBlendMode(BlendMode.SrcOver)
    canvas.drawRect(rect(0, 0, width, height), this.shaded)
  }

  /** A soft round glow: a unit gradient stretched to (rx, ry) at (x, y). */
  private glow(canvas: SkCanvas, shader: SkShader, x: number, y: number, rx: number, ry: number, alpha: number, plus = false) {
    if (alpha <= 0.003 || rx <= 0 || ry <= 0) return
    const paint = this.shaded
    paint.setShader(shader)
    paint.setAlphaf(Math.min(1, alpha))
    paint.setBlendMode(plus ? BlendMode.Plus : BlendMode.SrcOver)
    canvas.save()
    canvas.translate(x, y)
    canvas.scale(rx, ry)
    canvas.drawRect(UNIT, paint)
    canvas.restore()
  }

  private drawStrands(canvas: SkCanvas, front: boolean) {
    const { sim, stroke } = this
    stroke.setColor(c(front ? 'rgba(6, 22, 24, 0.96)' : 'rgba(30, 78, 70, 0.7)'))
    for (const s of sim.env.strands) {
      if (s.front !== front) continue
      sim.env.strandSegments(s, sim.time, (x0, y0, x1, y1, w) => {
        stroke.setStrokeWidth(w)
        canvas.drawLine(x0, y0, x1, y1, stroke)
      })
    }
  }

  private drawMotes(canvas: SkCanvas, near: boolean) {
    const { sim, fill } = this
    const { sx, sy } = sim.pointer
    for (const mote of sim.env.motes) {
      const look = sim.env.moteLook(mote, sx, sy)
      if (look.near !== near) continue
      fill.setColor(c(`rgba(190, 222, 228, ${look.alpha})`))
      canvas.drawRect(rect(look.x, look.y, look.size, look.size), fill)
    }
  }

  private drawSonars(canvas: SkCanvas) {
    const { sim, stroke } = this
    stroke.setStrokeWidth(1)
    for (const s of sim.sonars) {
      const look = sim.sonarLook(s)
      if (look.alpha <= 0.01 || look.rx <= 0) continue
      const oval = rect(look.x - look.rx, look.y - look.ry, look.rx * 2, look.ry * 2)
      stroke.setColor(c(`rgba(150, 222, 226, ${look.alpha})`))
      canvas.drawArc(oval, 180, 180, false, stroke) // the far half
      stroke.setColor(c(`rgba(150, 222, 226, ${look.alpha * 0.45})`))
      canvas.drawArc(oval, 0, 180, false, stroke)
    }
  }

  private drawPearls(canvas: SkCanvas) {
    const { sim, fill, stroke } = this
    const bed = sim.pearls
    for (const g of bed.glints) {
      const look = glintLook(g)
      this.glow(canvas, this.sparkleGlow, g.x, g.y, look.size * 2.2, look.size * 2.2, look.alpha, true)
      stroke.setColor(c(`rgba(255, 250, 240, ${look.alpha * 0.8})`))
      stroke.setStrokeWidth(0.8)
      canvas.drawLine(g.x - look.size, g.y, g.x + look.size, g.y, stroke)
      canvas.drawLine(g.x, g.y - look.size, g.x, g.y + look.size, stroke)
    }
    for (const g of bed.grains) {
      if (!g.cloud) continue
      const look = grainLook(g)
      this.glow(canvas, this.cloudGlow, g.x, g.y, look.r, look.r, look.alpha)
    }
    for (const p of bed.pearls) this.drawPearl(canvas, p, sim.time, p.buried === 0 && p.rank === sim.hovered)
    for (const g of bed.grains) {
      if (g.cloud) continue
      fill.setColor(c(`rgba(168, 198, 196, ${grainLook(g).alpha})`))
      canvas.drawRect(rect(g.x - g.size / 2, g.y - g.size / 2, g.size, g.size), fill)
    }
  }

  private drawPearl(canvas: SkCanvas, p: Pearl, time: number, hovered: boolean) {
    const look = pearlLook(p, time, hovered)
    if (!look.visible) return
    const { fill, stroke, shaded } = this
    const { r, cy } = look

    if (look.bloom) {
      stroke.setColor(c(`rgba(247, 236, 214, ${look.bloom.alpha})`))
      stroke.setStrokeWidth(1.2)
      canvas.drawOval(rect(p.x - look.bloom.rx, p.y + 1 - look.bloom.ry, look.bloom.rx * 2, look.bloom.ry * 2), stroke)
    }
    this.glow(canvas, this.pearlGlow, p.x, cy, look.glowR, look.glowR, look.glowAlpha, true)

    // Contact shadow in the sand.
    fill.setColor(c(`rgba(2, 10, 16, ${look.shadowAlpha})`))
    canvas.drawOval(rect(p.x - r * 1.3, p.y + r * 0.85 - r * 0.35, r * 2.6, r * 0.7), fill)

    // While buried, only the part above the sand line shows.
    canvas.save()
    if (p.buried > 0) canvas.clipRect(rect(p.x - r * 2, cy - r * 2, r * 4, look.sandY - (cy - r * 2)), ClipOp.Intersect, true)

    shaded.setShader(this.nacre)
    shaded.setAlphaf(1)
    shaded.setBlendMode(BlendMode.SrcOver)
    canvas.save()
    canvas.translate(p.x, cy)
    canvas.scale(r, r)
    canvas.drawCircle(0, 0, 1, shaded)
    canvas.restore()

    // Iridescent rim along the lower edge, and a soft specular highlight.
    stroke.setColor(c(`rgba(242, 208, 222, ${look.rimAlpha})`))
    stroke.setStrokeWidth(Math.max(0.6, r * 0.12))
    canvas.drawArc(rect(p.x - r * 0.9, cy - r * 0.9, r * 1.8, r * 1.8), 0.15 * 180, 0.7 * 180, false, stroke)
    fill.setColor(c('rgba(255, 255, 255, 0.9)'))
    canvas.save()
    canvas.rotate(-0.6 * DEG, p.x - r * 0.35, cy - r * 0.42)
    canvas.drawOval(rect(p.x - r * 0.35 - r * 0.28, cy - r * 0.42 - r * 0.18, r * 0.56, r * 0.36), fill)
    canvas.restore()
    canvas.restore()

    if (look.mound) {
      // Churned sand heaped around the hole; its lip hides the clip edge.
      const oval = rect(p.x - look.mound.w, look.sandY - look.mound.h, look.mound.w * 2, look.mound.h * 2)
      fill.setColor(c('#16303a'))
      canvas.drawOval(oval, fill)
      stroke.setColor(c(`rgba(120, 176, 186, ${look.mound.lipAlpha})`))
      stroke.setStrokeWidth(1)
      canvas.drawArc(oval, 1.05 * 180, 0.9 * 180, false, stroke)
    }
  }

  private drawManta(canvas: SkCanvas, m: Manta) {
    m.pose()
    const look = m.look()
    const { fill, stroke, shaded } = this

    stroke.setColor(c(look.tailColor))
    for (let i = 0; i < TAIL_SEGMENTS; i++) {
      stroke.setStrokeWidth(look.tailWidth(i))
      canvas.drawLine(m.tail[i * 2]!, m.tail[i * 2 + 1]!, m.tail[i * 2 + 2]!, m.tail[i * 2 + 3]!, stroke)
    }

    // Body: one smooth path, shaded across the span so each wing catches its own light.
    const body = Skia.PathBuilder.Make()
    smoothPath(m.pts, OUTLINE.length, (x, y) => body.moveTo(x, y), (cx, cy, x, y) => body.quadTo(cx, cy, x, y))
    body.close()
    const outline = body.detach()
    const left = c(look.left)
    const right = c(look.right)
    const wings = Skia.Shader.MakeLinearGradient(look.tipLeft, look.tipRight, [left, left, right, right], [0, 0.38, 0.62, 1], TileMode.Clamp)
    shaded.setShader(wings)
    shaded.setAlphaf(1)
    shaded.setBlendMode(BlendMode.SrcOver)
    canvas.drawPath(outline, shaded)

    // Shoulder patches, visible when the back faces us.
    if (look.back > 0.05) {
      fill.setColor(c(`rgba(214, 226, 228, ${look.patchAlpha})`))
      for (const side of [1, -1] as const) {
        const pts = m.shoulderPatch(side)
        const patch = Skia.PathBuilder.Make()
        smoothPath(pts, SHOULDER_PATCH.length, (x, y) => patch.moveTo(x, y), (cx, cy, x, y) => patch.quadTo(cx, cy, x, y))
        patch.close()
        canvas.drawPath(patch.detach(), fill)
      }
    }

    // Rim light: sunlight from the surface catches whichever edges face up on screen.
    const rim = Skia.Paint()
    rim.setAntiAlias(true)
    rim.setStyle(PaintStyle.Stroke)
    rim.setStrokeWidth(look.rimWidth)
    rim.setShader(Skia.Shader.MakeLinearGradient(
      { x: 0, y: look.rimTop }, { x: 0, y: look.rimBottom },
      [c(`rgba(200, 236, 242, ${0.62 * look.rimAlpha})`), c(`rgba(200, 236, 242, ${0.14 * look.rimAlpha})`), c('rgba(200, 236, 242, 0)')],
      [0, 0.45, 1], TileMode.Clamp,
    ))
    canvas.drawPath(outline, rim)
  }
}
