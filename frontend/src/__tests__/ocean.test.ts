import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { OceanEngine } from '@/ocean/engine'
import type { Manta, Pearl } from '@scat/shared/ocean'

/** A 2-D context that accepts every call and ignores it. */
function fakeContext(): CanvasRenderingContext2D {
  const noop: object = new Proxy(function () {}, {
    get: (_t, key) => (key === Symbol.toPrimitive ? () => 0 : noop),
    apply: () => noop,
    set: () => true,
  })
  return noop as CanvasRenderingContext2D
}

/** The shared simulation behind the engine (its private parts included, for the tests). */
interface SimInternals {
  manta: Manta
  plan: { kind: string; pearl?: Pearl }
  pearls: { pearls: Pearl[] }
  env: { size: { width: number; height: number } }
  update(dt: number): void
}

let size = { width: 1440, height: 900 }
let onResize: () => void = () => {}

function createEngine(opts: Partial<ConstructorParameters<typeof OceanEngine>[1]> = {}) {
  const canvas = document.createElement('canvas')
  canvas.getBoundingClientRect = () => ({ ...size, x: 0, y: 0, left: 0, top: 0, right: size.width, bottom: size.height, toJSON: () => ({}) })
  const engine = new OceanEngine(canvas, { reducedMotion: false, onReveal: () => {}, onDrained: () => {}, ...opts })
  const render = () => (engine as unknown as { renderer: { render(): void } }).renderer.render()
  return { engine, inner: engine.sim as unknown as SimInternals, render }
}

/** Every point of the drawn manta, not just its centre, must be on the canvas. */
function expectInside(inner: SimInternals) {
  const m = inner.manta
  const b = m.pose()
  const { width, height } = inner.env.size
  expect(m.x + b.left).toBeGreaterThanOrEqual(-0.01)
  expect(m.x + b.right).toBeLessThanOrEqual(width + 0.01)
  expect(m.y + b.top).toBeGreaterThanOrEqual(-0.01)
  expect(m.y + b.bottom).toBeLessThanOrEqual(height + 0.01)
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { constructor(cb: () => void) { onResize = cb } observe() {} disconnect() {} })
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} })
  vi.stubGlobal('requestAnimationFrame', () => 0)
  vi.stubGlobal('cancelAnimationFrame', () => {})
  vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('max-width') && size.width <= 720 }))
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => fakeContext() as never)
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const VIEWPORTS = [
  { width: 320, height: 640 },
  { width: 375, height: 667 },
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1024, height: 700 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
]

describe('ocean manta', () => {
  it.each(VIEWPORTS)('stays fully on screen at $width×$height', (viewport) => {
    size = viewport
    const { engine, inner } = createEngine()
    engine.setMode('scanning')
    const dt = 1 / 60
    let prev = { x: inner.manta.x, y: inner.manta.y }
    for (let i = 0; i < 60 * 90; i++) {
      if (i % 600 === 0) engine.discover(3)
      inner.update(dt)
      expectInside(inner)
      // No teleporting: per-frame movement stays within what the swim physics can produce.
      const step = Math.hypot(inner.manta.x - prev.x, inner.manta.y - prev.y)
      expect(step).toBeLessThan(12)
      prev = { x: inner.manta.x, y: inner.manta.y }
    }
    engine.destroy()
  })

  it('stays on screen after the viewport shrinks and grows', () => {
    size = { width: 1440, height: 900 }
    const { engine, inner } = createEngine()
    for (const next of [{ width: 390, height: 700 }, { width: 1200, height: 800 }, { width: 320, height: 640 }]) {
      for (let i = 0; i < 600; i++) inner.update(1 / 60)
      size = next
      onResize()
      expectInside(inner)
      for (let i = 0; i < 600; i++) {
        inner.update(1 / 60)
        expectInside(inner)
      }
    }
    engine.destroy()
  })

  it('digs each pearl out before counting it', () => {
    size = { width: 1440, height: 900 }
    const reveals: number[] = []
    const { engine, inner } = createEngine({ onReveal: (n) => reveals.push(n) })
    engine.setMode('scanning')
    engine.discover(1)

    const buriedLevels: number[] = []
    for (let i = 0; i < 60 * 15 && !reveals.length; i++) {
      inner.update(1 / 60)
      if (inner.plan.kind === 'dig') buriedLevels.push(inner.plan.pearl!.buried)
    }

    expect(reveals).toEqual([1])
    // It emerged gradually: fully buried at first, never going back down.
    expect(buriedLevels[0]).toBeGreaterThan(0.99)
    expect(buriedLevels.length).toBeGreaterThan(60)
    buriedLevels.reduce((a, b) => (expect(b).toBeLessThanOrEqual(a + 1e-9), b))
    const [pearl] = inner.pearls.pearls
    expect(pearl).toMatchObject({ buried: 0, rank: 1, dug: true })
    engine.destroy()
  })

  it('does not report drained while a pearl is still being dug', async () => {
    size = { width: 1440, height: 900 }
    const drained = vi.fn()
    const { engine, inner } = createEngine({ onDrained: drained })
    engine.discover(1)
    await Promise.resolve()
    drained.mockClear()
    for (let i = 0; i < 60 * 15 && inner.plan.kind !== 'dig'; i++) inner.update(1 / 60)
    expect(inner.plan.kind).toBe('dig')
    await Promise.resolve()
    expect(drained).not.toHaveBeenCalled()
    for (let i = 0; i < 60 * 3; i++) inner.update(1 / 60)
    await Promise.resolve()
    expect(drained).toHaveBeenCalled()
    engine.destroy()
  })

  it('renders every phase without throwing', () => {
    size = { width: 390, height: 844 }
    const { engine, inner, render } = createEngine()
    engine.discover(2)
    for (let i = 0; i < 60 * 12; i++) {
      inner.update(1 / 60)
      render()
    }
    engine.destroy()
  })
})
