// Regenerates the app icon, Android adaptive icon layers, splash mark and favicon from the
// Скат manta mark (the same path as frontend/src/components/brand/SkatMark.vue).
//   node scripts/make-assets.mjs
// Uses CanvasKit, which is already installed with @shopify/react-native-skia.
import { writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const CanvasKitInit = require('canvaskit-wasm/bin/full/canvaskit.js')
const bin = dirname(require.resolve('canvaskit-wasm/bin/full/canvaskit.js'))
const ck = await CanvasKitInit({ locateFile: (file) => join(bin, file) })
const assets = join(dirname(fileURLToPath(import.meta.url)), '..', 'assets')

const BODY = 'M16 8.4c-1.1 0-1.7-.9-2.1-2.4-.8.5-1.1 1.7-1.1 2.8C10 10.4 5.6 14 2.4 17.7c3.8-.3 7.8.5 10.5 2.7 1 .8 2.1 1.2 3.1 1.2s2.1-.4 3.1-1.2c2.7-2.2 6.7-3 10.5-2.7C26.4 14 22 10.4 19.2 8.8c0-1.1-.3-2.3-1.1-2.8-.4 1.5-1 2.4-2.1 2.4Z'
const TAIL = 'M16 21.6c0 3 .2 5.6.9 8.4'
const PEARL = [0xf3 / 255, 0xe9 / 255, 0xd8 / 255, 1]
const WHITE = [1, 1, 1, 1]

/** size: output px; mark: fraction of the canvas the 32-unit mark box covers; background: 'ocean' | null. */
function render(name, size, mark, color, background) {
  const surface = ck.MakeSurface(size, size)
  const canvas = surface.getCanvas()
  canvas.clear(ck.TRANSPARENT)
  if (background === 'ocean') {
    const paint = new ck.Paint()
    paint.setShader(ck.Shader.MakeLinearGradient([0, 0], [0, size],
      [ck.parseColorString('#15475a'), ck.parseColorString('#0c2f41'), ck.parseColorString('#081d2b'), ck.parseColorString('#050f18')],
      [0, 0.3, 0.65, 1], ck.TileMode.Clamp))
    canvas.drawRect(ck.XYWHRect(0, 0, size, size), paint)
    const glow = new ck.Paint()
    glow.setShader(ck.Shader.MakeRadialGradient([size * 0.68, -size * 0.1], size * 0.9,
      [ck.Color(150, 214, 222, 0.3), ck.Color(150, 214, 222, 0)], [0, 1], ck.TileMode.Clamp))
    canvas.drawRect(ck.XYWHRect(0, 0, size, size), glow)
  }
  const scale = (size * mark) / 32
  // The mark's ink is not centred in its 32-unit box (the tail hangs low): centre the ink.
  canvas.translate(size / 2 - 16 * scale, size / 2 - 18 * scale)
  canvas.scale(scale, scale)
  const fill = new ck.Paint()
  fill.setAntiAlias(true)
  fill.setColor(color)
  canvas.drawPath(ck.Path.MakeFromSVGString(BODY), fill)
  const stroke = new ck.Paint()
  stroke.setAntiAlias(true)
  stroke.setColor(color)
  stroke.setStyle(ck.PaintStyle.Stroke)
  stroke.setStrokeWidth(1.1)
  stroke.setStrokeCap(ck.StrokeCap.Round)
  canvas.drawPath(ck.Path.MakeFromSVGString(TAIL), stroke)
  writeFileSync(join(assets, name), surface.makeImageSnapshot().encodeToBytes())
  console.log(`assets/${name} ${size}×${size}`)
}

render('icon.png', 1024, 0.7, PEARL, 'ocean') // iOS and the stores: no transparency
render('android-icon-foreground.png', 1024, 0.46, PEARL, null) // inside the adaptive-icon safe zone
render('android-icon-monochrome.png', 1024, 0.46, WHITE, null) // Android 13 themed icons
render('splash-icon.png', 1024, 0.8, PEARL, null)
render('favicon.png', 48, 0.9, PEARL, 'ocean')
