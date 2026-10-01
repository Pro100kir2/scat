/**
 * The ocean scene in the browser: the shared simulation (@scat/shared/ocean) driven by
 * requestAnimationFrame and drawn with Canvas 2D. Only the DOM side lives here: sizing,
 * the frame loop, pausing when the canvas is off screen or the tab is hidden.
 */
import { OceanSim, type OceanOptions, type Rect, type SceneMode } from '@scat/shared/ocean'
import { CanvasRenderer } from './canvasRenderer'

export type { Rect, SceneMode }
export type EngineOptions = Omit<OceanOptions, 'lite' | 'seed'>

export class OceanEngine {
  readonly sim: OceanSim
  private readonly renderer: CanvasRenderer
  private last = 0
  private raf = 0
  private running = false
  private visible = true
  private drawnRevision = -1
  private readonly observers: { disconnect(): void }[] = []

  constructor(private readonly canvas: HTMLCanvasElement, private readonly opts: EngineOptions) {
    const lite = window.matchMedia('(max-width: 720px), (pointer: coarse)').matches
    this.sim = new OceanSim({ ...opts, lite })
    this.renderer = new CanvasRenderer(canvas.getContext('2d', { alpha: false })!, this.sim)

    const resize = new ResizeObserver(() => this.resize())
    resize.observe(canvas)
    const onScreen = new IntersectionObserver(([entry]) => {
      this.visible = !!entry?.isIntersecting
      this.syncLoop()
    })
    onScreen.observe(canvas)
    this.observers.push(resize, onScreen)
    document.addEventListener('visibilitychange', this.syncLoop)
    this.resize()
  }

  setSafeZone(rect: Rect | null) {
    this.sim.setSafeZone(rect)
    this.redrawIfStatic()
  }

  setMode(mode: SceneMode) {
    this.sim.setMode(mode)
    this.redrawIfStatic()
  }

  /** Queue `count` new discoveries; each becomes a pearl (or a far glint past the cap). */
  discover(count: number) {
    this.sim.discover(count)
    this.redrawIfStatic()
  }

  reset() {
    this.sim.reset()
    this.redrawIfStatic()
  }

  pointerMove(x: number, y: number, inside: boolean) {
    const hovered = this.sim.pointerMove(x, y, inside)
    this.redrawIfStatic()
    return hovered
  }

  destroy() {
    this.running = false
    cancelAnimationFrame(this.raf)
    this.observers.forEach((o) => o.disconnect())
    document.removeEventListener('visibilitychange', this.syncLoop)
  }

  private resize() {
    const rect = this.canvas.getBoundingClientRect()
    if (rect.width < 2 || rect.height < 2) return
    const dpr = Math.min(window.devicePixelRatio || 1, this.sim.lite ? 1.5 : 2)
    this.canvas.width = Math.round(rect.width * dpr)
    this.canvas.height = Math.round(rect.height * dpr)
    this.sim.resize(rect.width, rect.height)
    this.renderer.resize(dpr)
    this.syncLoop()
    this.drawnRevision = -1
    this.redrawIfStatic()
  }

  private readonly syncLoop = () => {
    const shouldRun = !this.opts.reducedMotion && this.visible && !document.hidden
    if (shouldRun && !this.running) {
      this.running = true
      this.last = performance.now()
      this.raf = requestAnimationFrame(this.frame)
    } else if (!shouldRun && this.running) {
      this.running = false
      cancelAnimationFrame(this.raf)
    }
  }

  private readonly frame = (now: number) => {
    if (!this.running) return
    const dt = Math.min(0.05, (now - this.last) / 1000)
    this.last = now
    this.sim.step(dt)
    this.renderer.render()
    this.raf = requestAnimationFrame(this.frame)
  }

  /** A still (reduced-motion) scene is drawn only when the simulation says it changed. */
  private redrawIfStatic() {
    if (!this.opts.reducedMotion || this.sim.revision === this.drawnRevision) return
    this.drawnRevision = this.sim.revision
    this.renderer.render()
  }
}
