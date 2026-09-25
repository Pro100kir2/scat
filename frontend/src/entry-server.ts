import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import App from './App.vue'

/** Build-time only: the page's initial state as HTML, before any API data arrives. */
export function render(): Promise<string> {
  return renderToString(createSSRApp(App))
}
