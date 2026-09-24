<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import SkatLogo from '@/components/brand/SkatLogo.vue'
import DonateLink from '@/components/ui/DonateLink.vue'

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
      <div class="header__end">
        <nav class="header__nav" aria-label="Разделы">
          <a href="#catch">Улов</a>
          <a href="#subscribe" class="header__wide">Подписка</a>
          <a href="#how" class="header__wide">Как это работает</a>
        </nav>
        <DonateLink small label="Пожертвовать" class="header__donate" />
      </div>
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

.header__end {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* A pearl-toned pill like the nav links: part of the product, not an ad. */
.header__donate {
  --btn-border: rgba(243, 233, 216, 0.2);
  --btn-bg: rgba(243, 233, 216, 0.03);
  gap: 8px;
  font-weight: 500;
}

.header__donate:hover {
  --btn-border: rgba(243, 233, 216, 0.45);
  --btn-bg: rgba(243, 233, 216, 0.07);
}

@media (max-width: 380px) {
  /* Heart only on the narrowest phones; the label stays for screen readers. */
  .header__donate {
    width: 36px;
    padding: 0;
  }

  .header__donate :deep(.donate__label) {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }
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
