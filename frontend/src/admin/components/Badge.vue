<script setup lang="ts">
defineProps<{ tone?: 'ok' | 'warn' | 'bad' | 'muted' | 'info' }>()
</script>

<template>
  <span class="badge" :class="`badge--${tone ?? 'muted'}`"><slot /></span>
</template>
