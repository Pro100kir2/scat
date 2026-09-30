import { createSSRApp } from 'vue'
import { createMemoryHistory } from 'vue-router'
import { renderToString } from 'vue/server-renderer'
import App from './App.vue'
import { createAppRouter } from './router'
import { ADMIN_PAGE, PAGES } from './content/pages'

export { ADMIN_PAGE, PAGES }

/** Build-time only: one page's initial state as HTML, before any API data arrives. */
export async function render(url: string): Promise<string> {
  const app = createSSRApp(App)
  const router = createAppRouter(createMemoryHistory())
  app.use(router)
  await router.push(url)
  await router.isReady()
  return renderToString(app)
}
