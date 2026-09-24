/**
 * The manta: a small 3-D body model rendered with a 2-D canvas.
 *
 * The outline lives in local coordinates (f = forward, r = right, u = up, wingspan = 2).
 * Every frame each outline point is deformed by the wing stroke (a wave that travels
 * from the body to the tips and from leading to trailing edge), rotated by bank, pitch
 * and yaw, and projected through a camera that looks slightly down into the water.
 * Movement is driven by flapping: thrust arrives on the down-stroke, drag slows the
 * animal between strokes, so speed pulses naturally instead of moving linearly.
 */
import { angleDelta, clamp, damp, lerp, smoothstep, TAU } from './math'

type RGB = readonly [number, number, number]

/** Right half of the outline, from the mouth to the root of the tail. */
const HALF_OUTLINE: ReadonlyArray<readonly [number, number]> = [
  [0.3, 0.0],
  [0.335, 0.045],
  [0.47, 0.075], // cephalic fin tip
  [0.455, 0.12],
  [0.35, 0.155],
  [0.285, 0.24],
  [0.19, 0.4],
  [0.07, 0.6],
  [-0.06, 0.8],
  [-0.16, 0.95],
  [-0.2, 1.0], // wing tip
  [-0.19, 0.9],
  [-0.145, 0.76],
  [-0.13, 0.6],
  [-0.165, 0.45],
  [-0.25, 0.3],
  [-0.35, 0.19],
  [-0.43, 0.13], // pelvic fin
  [-0.51, 0.1],
  [-0.48, 0.04],
  [-0.52, 0.0],
]
const WING_TIP = 10 // index of the wing tip in HALF_OUTLINE

const OUTLINE: ReadonlyArray<readonly [number, number]> = [
  ...HALF_OUTLINE,
  ...HALF_OUTLINE.slice(1, -1)
    .reverse()
    .map(([f, r]) => [f, -r] as const),
]

/** Pale shoulder patches on the back, the manta's signature marking. */
const SHOULDER_PATCH: ReadonlyArray<readonly [number, number]> = [
  [0.2, 0.12], [0.16, 0.3], [0.06, 0.42], [0.02, 0.36], [0.08, 0.24], [0.12, 0.13],
]

const TAIL_SEGMENTS = 9
const TAIL_LENGTH = 0.9

const CAMERA_ELEVATION = 0.82 // radians the camera looks down
const COS_E = Math.cos(CAMERA_ELEVATION)
const SIN_E = Math.sin(CAMERA_ELEVATION)

const DORSAL: RGB = [12, 22, 30]
const VENTRAL: RGB = [150, 168, 174] // the belly is in its own shadow
const WATER: RGB = [9, 34, 48]

export interface MantaTarget {
  x: number
  y: number
  depth: number
  speed: number // desired speed, px/s at unit scale
}

export class Manta {
  x: number
  y: number
  depth = 0.3 // 0 = near the camera, 1 = far away
  yaw: number // 0 = swimming right, π = swimming left
  pitch = 0
  bank = 0
  speed = 60
  private yawRate = 0

  flapPhase = 0
  flapAmp = 0.3
  private flapFreq = 0.4
  private gliding = 0 // seconds of glide left
  private glideCooldown = 4

  /** Wingspan in px at depth 0. */
  span = 200
  target: MantaTarget
  /** A brief bank-and-climb flourish after a discovery, 0..1. */
  private delight = 0

  private readonly pts: number[] = new Array(OUTLINE.length * 2).fill(0)
  private readonly tail: number[] = new Array((TAIL_SEGMENTS + 1) * 2).fill(0)

  constructor(x: number, y: number, yaw: number, private readonly rand: () => number) {
    this.x = x
    this.y = y
    this.yaw = yaw
    this.target = { x, y, depth: 0.3, speed: 60 }
  }

  get scale() {
    return this.span * (1 - 0.42 * this.depth) * 0.5
  }

  distanceTo(x: number, y: number) {
    return Math.hypot(x - this.x, y - this.y)
  }

  celebrate() {
    this.delight = 1
    this.gliding = 0
  }

  // --- motion -----------------------------------------------------------------

  update(dt: number, unit: number) {
    const t = this.target
    const dx = t.x - this.x
    const dy = t.y - this.y
    const dist = Math.hypot(dx, dy)

    // Heading: steer towards the target through the depth axis, so a U-turn sweeps
    // towards or away from the camera instead of flipping in place.
    const depthPull = (t.depth - this.depth) * 360 * unit
    const desiredYaw = Math.abs(dx) < 4 && Math.abs(depthPull) < 4 ? this.yaw : Math.atan2(depthPull, dx)
    const maxTurn = 0.85
    const wantRate = clamp(angleDelta(this.yaw, desiredYaw) * 1.1, -maxTurn, maxTurn)
    this.yawRate = damp(this.yawRate, wantRate, 2.2, dt)
    this.yaw = (this.yaw + this.yawRate * dt) % TAU

    // Pitch towards the target's height; lift the nose during the delight flourish.
    const horizontal = Math.max(Math.abs(dx), 90 * unit)
    const wantPitch = clamp(Math.atan2(-dy, horizontal), -0.5, 0.5) + this.delight * 0.18
    this.pitch = damp(this.pitch, wantPitch, 1.6, dt)

    // Bank into turns, like a bird. Extra roll while celebrating.
    const flourish = Math.sin(this.delight * Math.PI) * 0.28
    this.bank = damp(this.bank, clamp(-this.yawRate * 0.95 + flourish, -0.7, 0.7), 2.6, dt)
    this.delight = Math.max(0, this.delight - dt * 0.55)

    // Slow down on arrival.
    const arrive = smoothstep(0, 140 * unit, dist)
    const desired = t.speed * unit * lerp(0.45, 1, arrive) * (1 + this.delight * 0.8)

    // Occasional glides between bursts of flapping.
    this.glideCooldown -= dt
    if (this.gliding <= 0 && this.glideCooldown <= 0 && this.speed > desired * 0.95 && this.delight === 0) {
      this.gliding = 1.4 + this.rand() * 2.6
      this.glideCooldown = 5 + this.rand() * 7
    }
    if (this.gliding > 0) {
      this.gliding -= dt
      if (this.speed < desired * 0.62) this.gliding = 0
    }

    // Flapping: effort rises when the manta is slower than it wants to be.
    const effort = clamp((desired - this.speed) / Math.max(desired, 1) * 2.4 + 0.35 + this.delight, 0, 1)
    const wantAmp = this.gliding > 0 ? 0.07 : lerp(0.2, 0.44, effort)
    this.flapAmp = damp(this.flapAmp, wantAmp, this.gliding > 0 ? 1.2 : 2.4, dt)
    this.flapFreq = damp(this.flapFreq, lerp(0.26, 0.52, effort), 1.5, dt)
    this.flapPhase = (this.flapPhase + this.flapFreq * TAU * dt) % TAU

    const downstroke = Math.max(0, Math.sin(this.flapPhase))
    const thrust = this.flapAmp * downstroke * 470 * unit * (t.speed / 70)
    const drag = this.speed * (this.gliding > 0 ? 0.16 : 0.55)
    this.speed = Math.max(8 * unit, this.speed + (thrust - drag) * dt)

    const along = this.speed * Math.cos(this.pitch)
    this.x += along * Math.cos(this.yaw) * dt
    this.y -= this.speed * Math.sin(this.pitch) * dt * 0.9
    this.depth = clamp(this.depth + (along * Math.sin(this.yaw) * dt) / (360 * unit), 0, 1)
  }

  // --- rendering --------------------------------------------------------------

  /** Deform, rotate and project one local point into this.pts / this.tail. */
  private project(f: number, r: number, u: number, out: number[], i: number, s: number) {
    const cb = Math.cos(this.bank)
    const sb = Math.sin(this.bank)
    const r1 = r * cb - u * sb
    const u1 = r * sb + u * cb
    const cp = Math.cos(this.pitch)
    const sp = Math.sin(this.pitch)
    const f2 = f * cp - u1 * sp
    const u2 = f * sp + u1 * cp
    const cy = Math.cos(this.yaw)
    const sy = Math.sin(this.yaw)
    const X = f2 * cy - r1 * sy
    const D = f2 * sy + r1 * cy
    out[i] = this.x + X * s
    out[i + 1] = this.y - (u2 * COS_E + D * SIN_E) * s
  }

  private wingLift(f: number, r: number) {
    const span = Math.abs(r)
    const w = smoothstep(0.16, 1, span)
    // Inner wing strokes a little less while turning.
    const side = Math.sign(r) * this.yawRate * 0.25
    const amp = this.flapAmp * (1 + side)
    const wave = this.flapPhase - span * 1.5 + f * 1.1
    const bodyBob = -0.045 * this.flapAmp * Math.sin(this.flapPhase)
    return amp * w ** 1.35 * Math.sin(wave + Math.PI) * 0.95 + bodyBob
  }

  /** Is the back (1) or the belly (0) facing the camera for this wing? */
  private wingFacing(side: 1 | -1) {
    const lift = this.wingLift(-0.05, side * 0.75) - this.wingLift(-0.05, side * 0.2)
    const tilt = Math.atan2(lift, 0.55) * side
    // Local wing normal after flap tilt, then rotated like the points.
    let r = -Math.sin(tilt)
    let u = Math.cos(tilt)
    const cb = Math.cos(this.bank)
    const sb = Math.sin(this.bank)
    ;[r, u] = [r * cb - u * sb, r * sb + u * cb]
    const u2 = u * Math.cos(this.pitch)
    const f2 = -u * Math.sin(this.pitch)
    const D = f2 * Math.sin(this.yaw) + r * Math.cos(this.yaw)
    const towardCamera = u2 * SIN_E - D * COS_E
    return { facing: smoothstep(-0.18, 0.18, towardCamera), light: Math.abs(u2) }
  }

  private shade(side: 1 | -1, haze: number) {
    const { facing, light } = this.wingFacing(side)
    const k = 0.72 + 0.4 * light
    return `rgb(${[0, 1, 2]
      .map((c) => lerp(lerp(VENTRAL[c]!, DORSAL[c]!, facing) * k, WATER[c]!, haze) | 0)
      .join(',')})`
  }

  draw(ctx: CanvasRenderingContext2D) {
    const s = this.scale
    const haze = this.depth * 0.55

    OUTLINE.forEach(([f, r], i) => this.project(f, r, this.wingLift(f, r), this.pts, i * 2, s))

    // Tail: follows the body with a lagging sway, bends against the turn.
    for (let i = 0; i <= TAIL_SEGMENTS; i++) {
      const k = i / TAIL_SEGMENTS
      const f = -0.52 - k * TAIL_LENGTH
      const sway = Math.sin(this.flapPhase * 0.5 - k * 3) * 0.05 * k - this.yawRate * 0.22 * k * k
      this.project(f, sway, this.wingLift(-0.5, 0) - k * k * 0.04, this.tail, i * 2, s)
    }

    ctx.save()
    ctx.lineCap = 'round'
    ctx.strokeStyle = this.shade(1, haze * 1.3)
    for (let i = 0; i < TAIL_SEGMENTS; i++) {
      ctx.lineWidth = Math.max(0.6, (1 - i / TAIL_SEGMENTS) * s * 0.028)
      ctx.beginPath()
      ctx.moveTo(this.tail[i * 2]!, this.tail[i * 2 + 1]!)
      ctx.lineTo(this.tail[i * 2 + 2]!, this.tail[i * 2 + 3]!)
      ctx.stroke()
    }

    // Body: one smooth path, shaded across the span so each wing catches its own light.
    const tipR = WING_TIP * 2
    const tipL = (OUTLINE.length - WING_TIP) * 2
    const gradient = ctx.createLinearGradient(
      this.pts[tipL]!, this.pts[tipL + 1]!, this.pts[tipR]!, this.pts[tipR + 1]!,
    )
    const left = this.shade(-1, haze)
    const right = this.shade(1, haze)
    gradient.addColorStop(0, left)
    gradient.addColorStop(0.38, left)
    gradient.addColorStop(0.62, right)
    gradient.addColorStop(1, right)
    ctx.fillStyle = gradient
    this.tracePath(ctx, this.pts, OUTLINE.length, true)
    ctx.fill()

    // Shoulder patches, visible when the back faces us.
    const back = (this.wingFacing(1).facing + this.wingFacing(-1).facing) / 2
    if (back > 0.05) {
      ctx.fillStyle = `rgba(214, 226, 228, ${0.17 * back * (1 - haze)})`
      for (const side of [1, -1] as const) {
        const patch: number[] = []
        SHOULDER_PATCH.forEach(([f, r], i) => this.project(f, r * side, this.wingLift(f, r * side) + 0.004, patch, i * 2, s))
        this.tracePath(ctx, patch, SHOULDER_PATCH.length, true)
        ctx.fill()
      }
    }

    // Rim light: sunlight from the surface catches whichever edges face up on screen.
    let top = Infinity
    let bottom = -Infinity
    for (let i = 1; i < this.pts.length; i += 2) {
      top = Math.min(top, this.pts[i]!)
      bottom = Math.max(bottom, this.pts[i]!)
    }
    const rim = ctx.createLinearGradient(0, top, 0, bottom)
    rim.addColorStop(0, `rgba(200, 236, 242, ${0.62 * (1 - haze)})`)
    rim.addColorStop(0.45, `rgba(200, 236, 242, ${0.14 * (1 - haze)})`)
    rim.addColorStop(1, 'rgba(200, 236, 242, 0)')
    ctx.strokeStyle = rim
    ctx.lineWidth = Math.max(1, s * 0.013)
    this.tracePath(ctx, this.pts, OUTLINE.length, true)
    ctx.stroke()
    ctx.restore()
  }

  /** Smooth closed path through projected points (quadratic curves via midpoints). */
  private tracePath(ctx: CanvasRenderingContext2D, pts: number[], count: number, closed: boolean) {
    const px = (i: number) => pts[((i + count) % count) * 2]!
    const py = (i: number) => pts[((i + count) % count) * 2 + 1]!
    ctx.beginPath()
    ctx.moveTo((px(-1) + px(0)) / 2, (py(-1) + py(0)) / 2)
    for (let i = 0; i < count; i++) {
      ctx.quadraticCurveTo(px(i), py(i), (px(i) + px(i + 1)) / 2, (py(i) + py(i + 1)) / 2)
    }
    if (closed) ctx.closePath()
  }
}
