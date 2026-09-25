// Runs after `vite build` and `vite build --ssr`: puts the rendered page into dist/index.html
// so search engines and link previews get the full content without executing JavaScript.
import { readdir, readFile, rm, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

const dist = fileURLToPath(new URL('../dist/', import.meta.url))
const serverDir = fileURLToPath(new URL('../dist-server/', import.meta.url))

const { render } = await import(new URL('entry-server.js', `file://${serverDir}`).href)
const appHtml = await render()

const MOUNT = '<div id="app"></div>'
let html = await readFile(`${dist}index.html`, 'utf8')
if (!html.includes(MOUNT)) throw new Error(`prerender: ${MOUNT} not found in dist/index.html`)
html = html.replace(MOUNT, `<div id="app">${appHtml}</div>`)

// Preload the Cyrillic faces of the hero headline and body text: they decide when the
// largest text on the first screen settles (LCP) and stop the layout shifting when fonts swap in.
const assets = await readdir(`${dist}assets`)
const PRELOAD = [
  /^cormorant-garamond-cyrillic-500-normal-.+\.woff2$/,
  /^cormorant-garamond-cyrillic-500-italic-.+\.woff2$/,
  /^manrope-cyrillic-wght-normal-.+\.woff2$/,
]
const preloads = PRELOAD.map((re) => assets.find((f) => re.test(f)))
  .filter(Boolean)
  .map((f) => `    <link rel="preload" href="/assets/${f}" as="font" type="font/woff2" crossorigin />`)
  .join('\n')
if (preloads) html = html.replace('</title>', `</title>\n${preloads}`)

await writeFile(`${dist}index.html`, html)
await rm(serverDir, { recursive: true, force: true })
console.log(`prerender: dist/index.html (${(appHtml.length / 1024).toFixed(1)} kB of markup)`)
