/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'

/** Adds canonical and absolute Open Graph URLs once the production domain is known. */
function siteMeta(siteUrl: string): Plugin {
  return {
    name: 'skat-site-meta',
    transformIndexHtml(html) {
      if (!siteUrl) return html
      const base = siteUrl.replace(/\/+$/, '')
      return {
        html: html.replace('content="/og.png"', `content="${base}/og.png"`),
        tags: [
          { tag: 'link', attrs: { rel: 'canonical', href: `${base}/` }, injectTo: 'head' },
          { tag: 'meta', attrs: { property: 'og:url', content: `${base}/` }, injectTo: 'head' },
        ],
      }
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  return {
    plugins: [vue(), siteMeta(env.VITE_SITE_URL ?? '')],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    server: {
      // Local dev: same-origin /api calls go to the FastAPI server.
      proxy: { '/api': 'http://127.0.0.1:8000' },
    },
    build: {
      target: 'es2022',
    },
    test: {
      environment: 'jsdom',
    },
  }
})
