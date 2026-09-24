<script setup lang="ts">
import { env } from '@/config/env'
import AppIcon from './AppIcon.vue'

withDefaults(defineProps<{ small?: boolean; label?: string }>(), { label: 'Поддержать Ската' })
</script>

<template>
  <a
    v-if="env.donateUrl"
    class="btn donate"
    :class="{ 'btn--sm': small }"
    :href="env.donateUrl"
    target="_blank"
    rel="noopener noreferrer"
  >
    <AppIcon name="heart" class="donate__heart" />
    <span class="donate__label">{{ label }}</span>
  </a>
</template>

<style scoped>
.donate {
  --btn-border: rgba(243, 233, 216, 0.28);
  color: var(--pearl);
}

.donate:hover {
  --btn-border: rgba(243, 233, 216, 0.55);
  --btn-bg: rgba(243, 233, 216, 0.06);
}

.donate__heart {
  transition: transform 0.4s var(--ease-spring), fill 0.3s;
}

.donate:hover .donate__heart {
  transform: scale(1.15);
  fill: rgba(243, 233, 216, 0.25);
}
</style>
