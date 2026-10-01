// Runs after `vite build` and `vite build --ssr`: renders every page into its own HTML file
// (dist/index.html, dist/providers/index.html, ...) so search engines and link previews get
// the full content without executing JavaScript. Each file gets its own title, description,
// canonical URL and robots tag; the home page's structured data stays on the home page.
import { mkdir, readdir, readFile, rm, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

const dist = fileURLToPath(new URL('../dist/', import.meta.url))
const serverDir = fileURLToPath(new URL('../dist-server/', import.meta.url))

const { render, PAGES, ADMIN_PAGE } = await import(new URL('entry-server.js', `file://${serverDir}`).href)

const MOUNT = '<div id="app"></div>'
const template = await readFile(`${dist}index.html`, 'utf8')
if (!template.includes(MOUNT)) throw new Error(`prerender: ${MOUNT} not found in dist/index.html`)

// Preload the Cyrillic faces of the headlines and body text: they decide when the largest
// text on the first screen settles (LCP) and stop the layout shifting when fonts swap in.
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

const escapeAttr = (s) => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')
const escapeText = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;')

/** Replace one attribute value of the single tag matching `selector` (a regex source). */
function setContent(html, tagPattern, value) {
  const re = new RegExp(`(${tagPattern}[^>]*?content=")[^"]*(")`)
  if (!re.test(html)) return html
  return html.replace(re, `$1${escapeAttr(value)}$2`)
}

function headFor(page) {
  let html = template
  html = html.replace(/<title>[^<]*<\/title>/, `<title>${escapeText(page.title)}</title>`)
  html = setContent(html, '<meta\\s+name="description"', page.description)
  html = setContent(html, '<meta\\s+property="og:title"', page.title)
  html = setContent(html, '<meta\\s+property="og:description"', page.description)
  html = setContent(html, '<meta\\s+name="twitter:title"', page.title)
  html = setContent(html, '<meta\\s+name="twitter:description"', page.description)
  if (!page.indexable) html = setContent(html, '<meta\\s+name="robots"', 'noindex, follow')
  // canonical / og:url are absolute only when VITE_SITE_URL was set at build time.
  html = html.replace(/(<link rel="canonical" href=")([^"]*)\/("[^>]*>)/, `$1$2${page.path}$3`)
  html = html.replace(/(<meta property="og:url" content=")([^"]*)\/("[^>]*>)/, `$1$2${page.path}$3`)
  if (page.path !== '/') {
    // WebSite/FAQPage describe the home page; don't repeat them on inner pages.
    html = html.replace(/\s*<script type="application\/ld\+json">[\s\S]*?<\/script>/, '')
  }
  if (preloads) html = html.replace(/(<\/title>)/, `$1\n${preloads}`)
  return html
}

let total = 0
for (const page of Object.values(PAGES)) {
  const appHtml = await render(page.path)
  const html = headFor(page).replace(MOUNT, `<div id="app">${appHtml}</div>`)
  const dir = `${dist}${page.path.replace(/^\//, '')}`
  await mkdir(dir, { recursive: true })
  await writeFile(`${dir}index.html`, html)
  total += appHtml.length
  console.log(`prerender: ${page.path} → dist${page.path}index.html (${(appHtml.length / 1024).toFixed(1)} kB)`)
}

// The admin panel: an empty shell (it renders in the browser after sign-in), never indexed.
{
  const html = headFor(ADMIN_PAGE)
    .replace(/(<meta\s+name="robots"[^>]*?content=")[^"]*(")/, '$1noindex, nofollow$2')
    // No third-party ad script on the page where the admin token is typed.
    .replace(/\s*<!-- Google AdSense[\s\S]*?-->\s*<script[^>]*adsbygoogle[\s\S]*?<\/script>/, '')
  await mkdir(`${dist}admin`, { recursive: true })
  await writeFile(`${dist}admin/index.html`, html)
  console.log('prerender: /admin/ → dist/admin/index.html (shell)')
}

await rm(serverDir, { recursive: true, force: true })
console.log(`prerender: ${Object.keys(PAGES).length} pages, ${(total / 1024).toFixed(1)} kB of markup`)
