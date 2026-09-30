<script setup lang="ts">
/** Loading / error / empty around a piece of content, so every page handles all three. */
defineProps<{ loading?: boolean; error?: string | null; empty?: boolean; emptyText?: string }>()
defineEmits<{ retry: [] }>()
</script>

<template>
  <div v-if="error" class="state state--error" role="alert">
    <p>Could not load: {{ error }}</p>
    <button class="btn btn--sm" @click="$emit('retry')">Retry</button>
  </div>
  <div v-else-if="loading" class="state" aria-busy="true">Loading…</div>
  <div v-else-if="empty" class="state state--empty">{{ emptyText ?? 'Nothing here yet.' }}</div>
  <slot v-else />
</template>
