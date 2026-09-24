<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import SkatLogo from '@/components/brand/SkatLogo.vue'

const scrolled = ref(false)
const onScroll = () => (scrolled.value = window.scrollY > 24)
onMounted(() => {
  onScroll()
  window.addEventListener('scroll', onScroll, { passive: true })
})
onBeforeUnmount(() => window.removeEventListener('scroll', onScroll))
</script>

<template>
  <header class="header" :class="{ 'is-scrolled': scrolled }">
    <div class="container header__inner">
      <SkatLogo />
      <nav class="header__nav" aria-label="Разделы">
        <a href="#catch">Улов</a>
        <a href="#subscribe" class="header__wide">Подписка</a>
        <a href="#how" class="header__wide">Как это работает</a>
      </nav>
    </div>
  </header>
</template>

<style scoped>
.header {
  position: fixed;
  inset: 0 0 auto;
  z-index: 20;
  border-bottom: 1px solid transparent;
  transition: background-color 0.4s var(--ease-out), border-color 0.4s var(--ease-out), backdrop-filter 0.4s;
}

.header.is-scrolled {
  border-bottom-color: var(--line);
  background: rgba(5, 13, 20, 0.72);
  backdrop-filter: blur(14px) saturate(1.2);
  -webkit-backdrop-filter: blur(14px) saturate(1.2);
}

.header__inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 68px;
}

.header__nav {
  display: flex;
  gap: 4px;
}

.header__nav a {
  padding: 8px 14px;
  border-radius: 999px;
  color: var(--text-2);
  font-size: 14px;
  font-weight: 500;
  text-decoration: none;
  transition: color 0.2s, background-color 0.2s;
}

.header__nav a:hover {
  color: var(--text);
  background: rgba(168, 208, 222, 0.06);
}

@media (max-width: 640px) {
  .header__wide {
    display: none;
  }
}
</style>
