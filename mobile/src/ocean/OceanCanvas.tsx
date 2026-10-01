import { useCallback, useEffect, useRef, useState } from 'react'
import { AppState, Pressable, StyleSheet, type GestureResponderEvent, type LayoutChangeEvent } from 'react-native'
import { Canvas, Picture, type SkPicture } from '@shopify/react-native-skia'
import { OceanSim } from '@scat/shared/ocean'
import { SkiaRenderer } from './SkiaRenderer'
import { OCEAN_FPS, type OceanProps } from './types'

const FRAME_MS = 1000 / OCEAN_FPS
/** Tablets get the full scene; phones the lighter one (fewer particles, smaller manta). */
const LITE_BELOW = 720

/**
 * The ocean: the shared simulation stepped on a timer and drawn by Skia.
 * The simulation and the picture recording run on the JS thread a few milliseconds per
 * frame; drawing happens on the UI/GPU side, so scrolling and touches stay smooth.
 */
export default function OceanCanvas({ mode, found, reducedMotion, safeZone, paused, onReveal, onDrained, onPearl }: OceanProps) {
  const [picture, setPicture] = useState<SkPicture | null>(null)
  const [size, setSize] = useState<{ width: number; height: number } | null>(null)
  const [active, setActive] = useState(AppState.currentState === 'active')

  // The simulation outlives renders; it always calls the latest handlers.
  const callbacks = useRef({ onReveal, onDrained })
  useEffect(() => {
    callbacks.current = { onReveal, onDrained }
  })
  const scene = useRef<{ sim: OceanSim; renderer: SkiaRenderer; queued: number; revision: number } | null>(null)
  const stale = useRef<SkPicture[]>([])

  /** Draw one frame and release the pictures no longer on screen. */
  const paint = useCallback(() => {
    const s = scene.current
    if (!s) return
    const next = s.renderer.render()
    if (!next) return
    s.revision = s.sim.revision
    setPicture((previous) => {
      if (previous) stale.current.push(previous)
      // Keep the last two alive: the canvas may still be drawing the previous one.
      while (stale.current.length > 2) stale.current.shift()?.dispose()
      return next
    })
  }, [])

  // (Re)create the scene when its size class or the motion preference changes.
  const width = size?.width ?? 0
  const height = size?.height ?? 0
  const lite = width < LITE_BELOW
  const sized = width > 1 && height > 1
  useEffect(() => {
    if (!sized) return
    const sim = new OceanSim({
      reducedMotion,
      lite,
      onReveal: (total) => callbacks.current.onReveal(total),
      onDrained: () => callbacks.current.onDrained(),
    })
    const renderer = new SkiaRenderer(sim)
    scene.current = { sim, renderer, queued: 0, revision: -1 }
    return () => {
      renderer.dispose()
      scene.current = null
    }
  }, [reducedMotion, lite, sized])

  useEffect(() => {
    const s = scene.current
    if (!s || width < 2 || height < 2) return
    s.sim.resize(width, height)
    s.renderer.resize()
    paint()
  }, [width, height, reducedMotion, lite, paint])

  useEffect(() => {
    const s = scene.current
    if (!s) return
    s.sim.setSafeZone(safeZone)
    s.sim.setMode(mode)
    // A smaller `found` means a new dive: clear the sand first.
    if (found < s.queued) {
      s.sim.reset()
      s.queued = 0
    }
    s.sim.discover(found - s.queued)
    s.queued = found
    if (reducedMotion && s.sim.revision !== s.revision) paint()
  }, [mode, found, safeZone, reducedMotion, lite, width, height, paint])

  useEffect(() => {
    const sub = AppState.addEventListener('change', (next) => setActive(next === 'active'))
    return () => sub.remove()
  }, [])

  // The frame loop: only while visible, in the foreground, and motion is wanted.
  const running = active && !paused && !reducedMotion && width > 1
  useEffect(() => {
    if (!running) return
    let raf = 0
    let last = Date.now()
    let carry = 0
    const frame = () => {
      raf = requestAnimationFrame(frame)
      const now = Date.now()
      carry += now - last
      last = now
      if (carry < FRAME_MS) return
      const s = scene.current
      if (!s) return
      // Long gaps (a busy JS thread) are capped, as on the site, so nothing teleports.
      const dt = Math.min(0.05, carry / 1000)
      carry = 0
      // No pointer on a phone: let the layers sway on their own for the parallax.
      s.sim.drift(Math.sin(s.sim.time * 0.11) * 0.7, Math.sin(s.sim.time * 0.07 + 1) * 0.5)
      s.sim.step(dt)
      paint()
    }
    raf = requestAnimationFrame(frame)
    return () => cancelAnimationFrame(raf)
  }, [running, paint])

  useEffect(() => () => {
    stale.current.forEach((p) => p.dispose())
    stale.current = []
  }, [])

  const onLayout = (e: LayoutChangeEvent) => {
    const { width: w, height: h } = e.nativeEvent.layout
    setSize((prev) => (prev && Math.abs(prev.width - w) < 1 && Math.abs(prev.height - h) < 1 ? prev : { width: w, height: h }))
  }

  const onPress = (e: GestureResponderEvent) => {
    const pearl = scene.current?.sim.pearls.hit(e.nativeEvent.locationX, e.nativeEvent.locationY)
    if (pearl) onPearl(pearl.rank)
  }

  return (
    <Pressable style={styles.fill} onLayout={onLayout} onPress={onPress} accessible={false} importantForAccessibility="no-hide-descendants">
      <Canvas style={styles.fill} pointerEvents="none">
        {picture ? <Picture picture={picture} /> : null}
      </Canvas>
    </Pressable>
  )
}

const styles = StyleSheet.create({
  fill: { position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, backgroundColor: '#0b2533' },
})
