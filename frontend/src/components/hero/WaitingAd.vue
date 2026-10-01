<script setup lang="ts">
import { onMounted } from 'vue'

/**
 * The one AdSense unit of the site ("Scat Web Search Waiting"): shown only while a live scan is
 * running. The parent mounts it with v-if, so every search gets a fresh <ins> and exactly one
 * request, and the ad leaves together with the waiting state. The format stays Responsive Auto:
 * no size or orientation is set here. The global AdSense script is in index.html.
 */
declare global {
  interface Window {
    adsbygoogle?: unknown[]
  }
}

onMounted(() => {
  try {
    ;(window.adsbygoogle = window.adsbygoogle || []).push({})
  } catch {
    // Blocked or failed to load: the waiting screen works the same without the ad.
  }
})
</script>

<template>
  <aside class="ad" @click.stop>
    <p class="ad__label mono">Реклама</p>
    <ins
      class="adsbygoogle"
      style="display: block"
      data-ad-client="ca-pub-9243108413661209"
      data-ad-slot="8787910210"
      data-ad-format="auto"
      data-full-width-responsive="true"
    ></ins>
  </aside>
</template>

<style scoped>
/* Nothing is visible until Google fills the slot; an unfilled or blocked ad leaves no gap.
   Google keeps the size it chose for an unfilled slot, so it is hidden (Google's own advice). */
.ad ins.adsbygoogle[data-ad-status='unfilled'] {
  display: none !important;
}

.ad {
  width: 100%;
}

.ad__label {
  display: none;
  margin-bottom: 6px;
  color: var(--text-3);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.ad:has(ins[data-ad-status='filled']) {
  margin-top: 22px;
}

.ad:has(ins[data-ad-status='filled']) .ad__label {
  display: block;
}
</style>
