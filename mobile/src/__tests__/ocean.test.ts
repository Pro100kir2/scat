/* eslint-disable @typescript-eslint/no-require-imports -- modules are loaded after CanvasKit starts */
import type { OceanSim as Sim } from '@scat/shared/ocean'
import type { SkiaRenderer as Renderer } from '@/ocean/SkiaRenderer'

/**
 * The Skia renderer against the real simulation, drawing with real Skia (CanvasKit, the
 * WebAssembly build Skia's own Jest setup uses). CanvasKit is started inside the test so its
 * typed arrays belong to the same JS realm as the code under test.
 */
let OceanSim: typeof Sim
let SkiaRenderer: typeof Renderer

beforeAll(async () => {
  // CanvasKit wants a full TextDecoder (utf-16le); Expo's Jest runtime ships a utf-8-only one.
  global.TextDecoder = require('node:util').TextDecoder
  const init = require('canvaskit-wasm/bin/full/canvaskit')
  ;(global as unknown as { CanvasKit: unknown }).CanvasKit = await init({})
  OceanSim = require('@scat/shared/ocean').OceanSim
  SkiaRenderer = require('@/ocean/SkiaRenderer').SkiaRenderer
})

describe('Skia ocean renderer', () => {
  const scene = (reducedMotion = false) => {
    const sim = new OceanSim({ reducedMotion, lite: true, onReveal: () => {}, onDrained: () => {}, seed: 7 })
    const renderer = new SkiaRenderer(sim)
    sim.resize(390, 760)
    renderer.resize()
    return { sim, renderer }
  }

  it('draws every phase of a dive without throwing', () => {
    const { sim, renderer } = scene()
    sim.setMode('scanning')
    sim.discover(3)
    for (let i = 0; i < 30 * 14; i++) {
      sim.step(1 / 30)
      const picture = renderer.render()
      expect(picture).not.toBeNull()
      picture!.dispose()
    }
    expect(sim.pearls.pearls.length).toBeGreaterThan(0)
    renderer.dispose()
  })

  it('draws the still scene for reduced motion', () => {
    const { sim, renderer } = scene(true)
    sim.discover(4)
    expect(sim.pearls.pearls).toHaveLength(4) // shown at once, no animation needed
    expect(renderer.render()).not.toBeNull()
    renderer.dispose()
  })

  it('has nothing to draw before it is sized', () => {
    const sim = new OceanSim({ reducedMotion: false, lite: true, onReveal: () => {}, onDrained: () => {} })
    expect(new SkiaRenderer(sim).render()).toBeNull()
  })
})
