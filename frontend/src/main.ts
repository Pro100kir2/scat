import { createApp, createSSRApp } from 'vue'
import '@fontsource-variable/manrope'
import '@fontsource-variable/jetbrains-mono'
import '@fontsource/cormorant-garamond/500.css'
import '@fontsource/cormorant-garamond/500-italic.css'
import './styles/main.css'
import App from './App.vue'

// Production builds ship the page prerendered (scripts/prerender.mjs), so crawlers and slow
// connections see real content before JavaScript runs; hydrate it. The dev server sends an empty shell.
const root = document.getElementById('app')!
const app = root.hasChildNodes() ? createSSRApp(App) : createApp(App)
app.mount(root)
