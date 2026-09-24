export const TAU = Math.PI * 2

export const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v))
export const lerp = (a: number, b: number, t: number) => a + (b - a) * t
export const smoothstep = (a: number, b: number, v: number) => {
  const t = clamp((v - a) / (b - a), 0, 1)
  return t * t * (3 - 2 * t)
}
/** Frame-rate independent exponential approach towards `target`. */
export const damp = (current: number, target: number, rate: number, dt: number) =>
  lerp(current, target, 1 - Math.exp(-rate * dt))

/** Shortest signed difference between two angles, in (-π, π]. */
export const angleDelta = (from: number, to: number) => {
  let d = (to - from) % TAU
  if (d > Math.PI) d -= TAU
  if (d <= -Math.PI) d += TAU
  return d
}

export const easeOutCubic = (t: number) => 1 - (1 - t) ** 3
export const easeInOutSine = (t: number) => -(Math.cos(Math.PI * t) - 1) / 2

/** Small seeded PRNG so each visit gets its own, but reproducible, variation. */
export function mulberry32(seed: number) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** 1-D value noise in [-1, 1]; smooth, non-repeating drift for organic motion. */
export function createNoise(rand: () => number, size = 256) {
  const values = Array.from({ length: size }, () => rand() * 2 - 1)
  return (x: number) => {
    const i = Math.floor(x)
    const f = x - i
    const a = values[((i % size) + size) % size]!
    const b = values[(((i + 1) % size) + size) % size]!
    const t = f * f * (3 - 2 * f)
    return a + (b - a) * t
  }
}
