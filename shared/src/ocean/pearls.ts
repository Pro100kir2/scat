import { clamp, easeOutCubic, lerp, smoothstep, TAU } from './math'

export const REVEAL_SECONDS = 1.6

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

export interface Glint {
  x: number
  y: number
  age: number
  size: number
}

/** Sand kicked up while the manta digs: fine grains plus a few soft clouds. */
export interface Grain {
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
  glints: Glint[] = []
  grains: Grain[] = []
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
}

/** Everything about how a pearl looks right now; renderers only turn it into shapes. */
export function pearlLook(p: Pearl, time: number, hovered: boolean) {
  const t = clamp(p.age / REVEAL_SECONDS, 0, 1)
  const grow = p.dug ? 1 : easeOutCubic(smoothstep(0.08, 0.55, t))
  const breathe = 0.5 + 0.5 * Math.sin(time * 1.3 + p.phase)
  const r = p.radius * grow * (hovered ? 1.18 : 1)
  // While buried the pearl sits lower and only the part above the sand line shows.
  // `cover` fades the sand lip out over the last stretch so nothing pops at the end.
  const cover = smoothstep(0, 0.35, p.buried)
  const cy = p.y + p.buried * r * 2.3
  const blooming = t < 1 && p.buried === 0
  const ring = easeOutCubic(t)
  const flash = blooming ? Math.sin(t * Math.PI) * 1.6 : 0
  return {
    visible: grow > 0,
    r,
    cy,
    cover,
    breathe,
    /** Discovery bloom: a ring of light spreading over the sand. */
    bloom: blooming ? { rx: 8 + ring * 70, ry: 3 + ring * 16, alpha: (1 - ring) * 0.55 } : null,
    glowR: r * (4.2 + breathe * 1.2 + flash * 2 + (hovered ? 1.6 : 0)),
    glowAlpha: (0.2 + breathe * 0.1 + flash * 0.25 + (hovered ? 0.15 : 0)) * (1 - 0.85 * p.buried),
    shadowAlpha: 0.45 * (1 - cover),
    /** Sand line: drops below the pearl as it comes free. */
    sandY: p.y + r * lerp(1.2, 0.3, cover),
    rimAlpha: 0.35 + breathe * 0.2,
    /** Churned sand heaped around the hole while it is still buried. */
    mound: p.buried > 0 ? { w: r * (1.5 + cover * 0.9), h: r * 0.5 * cover + 0.5, lipAlpha: 0.35 * cover } : null,
  }
}

export function grainLook(g: Grain) {
  const t = g.age / g.life
  return g.cloud
    ? { r: g.size * (0.5 + easeOutCubic(t) * 0.8), alpha: Math.sin(t * Math.PI) * 0.22 }
    : { r: g.size, alpha: Math.sin(t * Math.PI) * 0.8 }
}

export function glintLook(g: Glint) {
  const a = Math.sin(clamp(g.age / 1.4, 0, 1) * Math.PI)
  return { size: 5 * g.size * (0.6 + a * 0.6), alpha: a * 0.75 }
}
