import { createApp, createSSRApp } from 'vue'
import { createWebHistory } from 'vue-router'
import '@fontsource-variable/manrope'
import '@fontsource-variable/jetbrains-mono'
import '@fontsource/cormorant-garamond/500.css'
import '@fontsource/cormorant-garamond/500-italic.css'
import './styles/main.css'
import App from './App.vue'
import { createAppRouter } from './router'

// Production builds ship every page prerendered (scripts/prerender.mjs), so crawlers and slow
// connections see real content before JavaScript runs; hydrate it. The dev server sends an empty shell.
const root = document.getElementById('app')!
const app = root.hasChildNodes() ? createSSRApp(App) : createApp(App)
const router = createAppRouter(createWebHistory())
app.use(router)
// Lazy pages must be loaded before hydration, or the markup would not match.
void router.isReady().then(() => app.mount(root))
