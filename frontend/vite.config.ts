/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv, type HtmlTagDescriptor, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
import pkg from './package.json' with { type: 'json' }
import { FAQ } from '../shared/src/content/faq.ts'
import { PAGES } from './src/content/pages.ts'

/**
 * Search-engine plumbing that depends on the production domain:
 * canonical and absolute share-preview URLs, structured data, robots.txt and sitemap.xml.
 */
function siteMeta(env: Record<string, string>): Plugin {
  const base = (env.VITE_SITE_URL ?? '').trim().replace(/\/+$/, '')
  const url = (path: string) => `${base}${path}`
  const description =
    'Бесплатный поиск рабочих VPN: каждый день проверяет тысячи открытых VLESS-конфигураций через Xray ' +
    'и оставляет пять самых быстрых и стабильных.'

  // Describes what is visible on the page; FAQPage repeats the FAQ section word for word.
  const jsonLd = {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'WebSite',
        '@id': url('/#website'),
        url: url('/'),
        name: 'Скат',
        alternateName: ['SKAT', 'Skat'],
        description,
        inLanguage: 'ru',
      },
      {
        '@type': 'WebApplication',
        '@id': url('/#app'),
        name: 'Скат',
        alternateName: 'SKAT',
        url: url('/'),
        description,
        image: url('/og.png'),
        applicationCategory: 'UtilitiesApplication',
        operatingSystem: 'Any',
        browserRequirements: 'Requires JavaScript',
        inLanguage: 'ru',
        isAccessibleForFree: true,
        offers: { '@type': 'Offer', price: '0', priceCurrency: 'RUB' },
      },
      {
        '@type': 'FAQPage',
        '@id': url('/#faq'),
        inLanguage: 'ru',
        mainEntity: FAQ.map((item) => ({
          '@type': 'Question',
          name: item.question,
          acceptedAnswer: { '@type': 'Answer', text: item.answer.join(' ') },
        })),
      },
    ],
  }

  const verification: [string, string | undefined][] = [
    ['google-site-verification', env.VITE_GOOGLE_SITE_VERIFICATION],
    ['yandex-verification', env.VITE_YANDEX_VERIFICATION],
  ]

  return {
    name: 'skat-site-meta',
    transformIndexHtml(html) {
      const tags: HtmlTagDescriptor[] = [
        {
          tag: 'script',
          attrs: { type: 'application/ld+json' },
          // `<` escaped so text can never close the script element.
          children: JSON.stringify(jsonLd).replace(/</g, '\\u003c'),
          injectTo: 'head',
        },
      ]
      for (const [name, content] of verification) {
        if (content?.trim()) tags.push({ tag: 'meta', attrs: { name, content: content.trim() }, injectTo: 'head' })
      }
      if (!base) return { html, tags }
      tags.push(
        { tag: 'link', attrs: { rel: 'canonical', href: url('/') }, injectTo: 'head' },
        { tag: 'meta', attrs: { property: 'og:url', content: url('/') }, injectTo: 'head' },
      )
      // Share previews and structured data need absolute URLs.
      return { html: html.replaceAll('content="/og.png"', `content="${url('/og.png')}"`), tags }
    },
    generateBundle() {
      const robots = ['User-agent: *', 'Allow: /', 'Disallow: /admin/', ...(base ? ['', `Sitemap: ${url('/sitemap.xml')}`] : [])]
      this.emitFile({ type: 'asset', fileName: 'robots.txt', source: `${robots.join('\n')}\n` })
      if (!base) return
      const lastmod = new Date().toISOString().slice(0, 10)
      const entries = Object.values(PAGES)
        .filter((page) => page.indexable)
        .map((page) => `  <url>\n    <loc>${url(page.path)}</loc>\n    <lastmod>${lastmod}</lastmod>\n  </url>\n`)
        .join('')
      this.emitFile({
        type: 'asset',
        fileName: 'sitemap.xml',
        source:
          '<?xml version="1.0" encoding="UTF-8"?>\n' +
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
          entries +
          '</urlset>\n',
      })
    },
  }
}

const shared = fileURLToPath(new URL('../shared/src', import.meta.url))

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  return {
    plugins: [vue(), siteMeta(env)],
    // Shown on the admin System page next to the backend's version.
    define: { __APP_VERSION__: JSON.stringify(pkg.version) },
    resolve: {
      alias: [
        { find: '@', replacement: fileURLToPath(new URL('./src', import.meta.url)) },
        // Framework-independent logic shared with the mobile app (../shared).
        { find: /^@scat\/shared$/, replacement: `${shared}/index.ts` },
        { find: /^@scat\/shared\/(.*)$/, replacement: `${shared}/$1` },
      ],
    },
    server: {
      // Local dev: same-origin /api calls go to the FastAPI server.
      proxy: { '/api': 'http://127.0.0.1:8000' },
      // The shared package lives next to this project, outside Vite's root.
      fs: { allow: ['.', shared] },
    },
    build: {
      target: 'es2022',
    },
    test: {
      environment: 'jsdom',
    },
  }
})
