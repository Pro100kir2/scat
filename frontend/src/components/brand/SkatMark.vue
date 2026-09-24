<script setup lang="ts">
/** The manta seen from above: cephalic fins, swept wings, a long thin tail. */
defineProps<{ size?: number; title?: string }>()
</script>

<template>
  <svg
    class="skat-mark"
    :width="size ?? 28"
    :height="size ?? 28"
    viewBox="0 0 32 32"
    :role="title ? 'img' : undefined"
    :aria-hidden="title ? undefined : 'true'"
  >
    <title v-if="title">{{ title }}</title>
    <path
      fill="currentColor"
      d="M16 8.4c-1.1 0-1.7-.9-2.1-2.4-.8.5-1.1 1.7-1.1 2.8C10 10.4 5.6 14 2.4 17.7c3.8-.3 7.8.5 10.5 2.7 1 .8 2.1 1.2 3.1 1.2s2.1-.4 3.1-1.2c2.7-2.2 6.7-3 10.5-2.7C26.4 14 22 10.4 19.2 8.8c0-1.1-.3-2.3-1.1-2.8-.4 1.5-1 2.4-2.1 2.4Z"
    />
    <path d="M16 21.6c0 3 .2 5.6.9 8.4" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" />
  </svg>
</template>
