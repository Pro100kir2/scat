import { createRouter, type RouterHistory } from 'vue-router'
import HomePage from '@/pages/HomePage.vue'
import { PAGES, type PageMeta } from '@/content/pages'

/** The fixed header's height plus a little air, for section links. */
const HEADER_OFFSET = 76

export function createAppRouter(history: RouterHistory) {
  const router = createRouter({
    history,
    routes: [
      { path: PAGES.home.path, name: 'home', component: HomePage, meta: { page: PAGES.home } },
      // Provider pages load on demand: the home page bundle stays as small as before.
      { path: PAGES.providers.path, name: 'providers', component: () => import('@/pages/ProvidersPage.vue'), meta: { page: PAGES.providers } },
      { path: PAGES.addProvider.path, name: 'add-provider', component: () => import('@/pages/AddProviderPage.vue'), meta: { page: PAGES.addProvider } },
      { path: PAGES.install.path, name: 'install', component: () => import('@/pages/InstallGuidePage.vue'), meta: { page: PAGES.install } },
    ],
    scrollBehavior(to, from, saved) {
      if (saved) return saved
      if (to.hash) {
        const reduce = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
        return { el: to.hash, top: HEADER_OFFSET, behavior: reduce || to.path !== from.path ? 'auto' : 'smooth' }
      }
      return to.path === from.path ? false : { top: 0 }
    },
  })

  // Keep <head> in step on client-side navigation; prerendering writes it into each HTML file.
  router.afterEach((to) => {
    if (typeof document === 'undefined') return
    const page = to.meta.page as PageMeta | undefined
    if (!page) return
    document.title = page.title
    document.querySelector('meta[name="description"]')?.setAttribute('content', page.description)
    const canonical = document.querySelector('link[rel="canonical"]')
    if (canonical) canonical.setAttribute('href', new URL(page.path, canonical.getAttribute('href') ?? location.href).href)
    document.querySelector('meta[name="robots"]')?.setAttribute(
      'content',
      page.indexable ? 'index, follow, max-image-preview:large, max-snippet:-1' : 'noindex, follow',
    )
  })
  return router
}
