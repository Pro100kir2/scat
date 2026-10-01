/**
 * Everything around the manta, as data: the water, far rocks, seabed, light shafts, kelp and
 * drifting particles. Static geometry is generated once per resize; update() moves the few
 * things that really move. Renderers (Canvas 2D on the site, Skia in the app) paint the
 * static parts into cached layers and draw the rest every frame.
 */
import { createNoise, lerp, TAU } from './math'

export interface Size {
  width: number
  height: number
}

export interface Mote {
  x: number
  y: number
  z: number // 0 far .. 1 near
  drift: number
}

export interface Bubble {
  x: number
  y: number
  r: number
  speed: number
  wobble: number
}

export interface Strand {
  x: number
  height: number
  width: number
  phase: number
  front: boolean
}

export interface Fish {
  offset: number
  lane: number
  size: number
}

export interface Ridge {
  color: string
  /** Crest line, left to right, as x, y pairs; the shape is closed along the bottom edge. */
  points: number[]
}

export interface Rock {
  x: number
  y: number
  w: number
  h: number
}

export interface Ray {
  x: number
  spread: number
  lean: number
  alpha: number
}

/** The page colour below the scene; must match --abyss in the stylesheet. */
export const PAGE_BOTTOM = '#050d14'

export const WATER_STOPS: ReadonlyArray<readonly [number, string]> = [
  [0, '#15475a'],
  [0.28, '#0c2f41'],
  [0.62, '#081d2b'],
  [1, '#050f18'],
]
export const SAND_STOPS: ReadonlyArray<readonly [number, string]> = [
  [0, '#16303a'],
  [0.35, '#0d2029'],
  [1, PAGE_BOTTOM],
]
export const STRAND_SEGMENTS = 9

export class Seascape {
  size: Size = { width: 1, height: 1 }
  ridges: Ridge[] = []
  rocks: Rock[] = []
  rays: Ray[] = []
  /** Sand crest, left to right, as x, y pairs. */
  sandLine: number[] = []
  sandTop = 0
  motes: Mote[] = []
  bubbles: Bubble[] = []
  strands: Strand[] = []
  school: Fish[] = []
  private vents: number[] = []
  readonly noise: (x: number) => number
  private readonly profile: (x: number) => number

  constructor(private readonly rand: () => number, readonly lite: boolean) {
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
    const { width, height } = size

    const shapes = [
      { color: 'rgba(22, 62, 80, 0.5)', base: 0.72, amp: 70, freq: 230 },
      { color: 'rgba(13, 42, 57, 0.78)', base: 0.79, amp: 42, freq: 140 },
    ]
    this.ridges = shapes.map((s) => {
      const n = createNoise(this.rand)
      const points: number[] = []
      for (let x = -40; x <= width + 40; x += 8) {
        // Two octaves: broad hills plus a rougher crest.
        const ridge = (n(x / s.freq) * 0.5 + 0.5) ** 2 * s.amp * 1.6 + n(x / (s.freq * 0.22) + 17) * s.amp * 0.12
        points.push(x, height * s.base - ridge)
      }
      return { color: s.color, points }
    })

    this.sandTop = Math.min(...Array.from({ length: 20 }, (_, i) => this.seabedY((i / 19) * width)))
    this.sandLine = []
    for (let x = 0; x <= width; x += 6) this.sandLine.push(x, this.seabedY(x))

    // Rocks half-buried in the sand.
    this.rocks = Array.from({ length: Math.round(width / 160) }, () => {
      const x = this.rand() * width
      const w = 18 + this.rand() * 46
      return { x, y: this.seabedY(x) + 6, w, h: w * (0.35 + this.rand() * 0.3) }
    })

    const rayCount = this.lite ? 4 : 7
    this.rays = Array.from({ length: rayCount }, (_, i) => ({
      x: width * (0.25 + (i / rayCount) * 0.8) + this.rand() * 60,
      spread: 30 + this.rand() * 70,
      lean: height * (0.25 + this.rand() * 0.15),
      alpha: 0.05 + this.rand() * 0.06,
    }))

    const area = (width * height) / 1_000_000
    const moteCount = Math.round((this.lite ? 45 : 110) * Math.min(1.6, Math.max(0.5, area)))
    this.motes = Array.from({ length: moteCount }, () => ({
      x: this.rand() * width,
      y: this.rand() * height,
      z: this.rand(),
      drift: this.rand() * 100,
    }))

    const strandCount = this.lite ? 7 : 14
    this.strands = Array.from({ length: strandCount }, (_, i) => ({
      x: ((i + this.rand() * 0.8) / strandCount) * width,
      height: 40 + this.rand() * (this.lite ? 70 : 130),
      width: 2.5 + this.rand() * 3.5,
      phase: this.rand() * 50,
      front: this.rand() < 0.22,
    }))

    this.vents = [0.18 + this.rand() * 0.2, 0.62 + this.rand() * 0.3].map((f) => f * width)
    this.bubbles = []
    this.school = Array.from({ length: this.lite ? 10 : 22 }, () => ({
      offset: this.rand() * 160,
      lane: this.rand() * 36,
      size: 3 + this.rand() * 3,
    }))
  }

  /** Drift the marine snow, raise the bubbles. */
  update(dt: number) {
    if (dt <= 0) return
    const { width, height } = this.size
    for (const m of this.motes) {
      m.y += (4 + m.z * 8) * dt
      m.x += this.noise(m.drift + m.y * 0.01) * 6 * dt
      if (m.y > height) {
        m.y = -4
        m.x = this.rand() * width
      }
    }
    if (this.bubbles.length < (this.lite ? 5 : 12) && this.rand() < dt * 1.4) {
      const vent = this.vents[Math.floor(this.rand() * this.vents.length)]!
      this.bubbles.push({ x: vent, y: this.seabedY(vent), r: 1 + this.rand() * 2.2, speed: 22 + this.rand() * 26, wobble: this.rand() * TAU })
    }
    for (const b of this.bubbles) {
      b.y -= b.speed * dt
      b.wobble += dt * 3
      b.r += dt * 0.08
    }
    this.bubbles = this.bubbles.filter((b) => b.y > height * 0.08)
  }

  // --- what things look like right now (shared by every renderer) ----------------

  /** Light shafts sway and breathe slowly. `px` is the smoothed pointer offset in [-1, 1]. */
  raysLook(time: number, px: number) {
    return { alpha: 0.75 + this.noise(time * 0.07) * 0.25, dx: this.noise(time * 0.05 + 9) * 24 - px * 6 }
  }

  /** Where a mote is drawn (parallax by depth), its size and opacity. */
  moteLook(m: Mote, px: number, py: number) {
    const near = m.z > 0.82
    return {
      near,
      x: m.x - px * lerp(2, 18, m.z),
      y: m.y - py * lerp(1, 10, m.z),
      size: lerp(0.6, near ? 2.6 : 1.6, m.z),
      alpha: lerp(0.12, near ? 0.3 : 0.4, m.z),
    }
  }

  /**
   * Kelp: a chain of segments, each bending a little more than the one below it.
   * Calls `segment(x0, y0, x1, y1, width)` for every piece of the strand.
   */
  strandSegments(s: Strand, time: number, segment: (x0: number, y0: number, x1: number, y1: number, width: number) => void) {
    let x = s.x
    let y = this.seabedY(s.x) + 4
    let angle = -Math.PI / 2
    const step = s.height / STRAND_SEGMENTS
    for (let i = 0; i < STRAND_SEGMENTS; i++) {
      const k = i / STRAND_SEGMENTS
      angle += (this.noise(time * 0.22 + s.phase + k * 1.4) * 0.16 + Math.sin(time * 0.8 + s.phase - k * 2.2) * 0.05) * (0.4 + k)
      const nx = x + Math.cos(angle) * step
      const ny = y + Math.sin(angle) * step
      segment(x, y, nx, ny, s.width * (1 - k * 0.75))
      x = nx
      y = ny
    }
  }

  /** A far-away school of fish drifting across: calls `fish(x, y, size)` for each. */
  schoolFish(time: number, fish: (x: number, y: number, size: number) => void) {
    const { width, height } = this.size
    const travel = width + 400
    const cx = ((time * 9) % travel) - 200
    const cy = height * 0.42 + this.noise(time * 0.05 + 3) * 40
    for (const f of this.school) {
      fish(width - (cx + f.offset + this.noise(time * 0.3 + f.offset) * 10), cy + f.lane + this.noise(time * 0.2 + f.lane) * 8, f.size)
    }
  }
}
