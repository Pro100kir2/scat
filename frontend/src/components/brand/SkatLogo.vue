<script setup lang="ts">
import SkatMark from './SkatMark.vue'
</script>

<template>
  <RouterLink class="logo" :to="{ path: '/', hash: '#top' }" aria-label="Скат — на главную">
    <SkatMark :size="26" class="logo__mark" />
    <span class="logo__word">Скат</span>
  </RouterLink>
</template>

<style scoped>
.logo {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: var(--text);
  text-decoration: none;
}

.logo__mark {
  color: var(--pearl);
  transition: transform 0.6s var(--ease-out);
}

.logo:hover .logo__mark {
  transform: translateY(-2px) rotate(-6deg);
}

.logo__word {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.32em;
  text-transform: uppercase;
  /* Optical balance for the trailing tracking. */
  margin-right: -0.32em;
}
</style>
