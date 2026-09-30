<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ page: number; perPage: number; total: number }>()
const emit = defineEmits<{ 'update:page': [page: number] }>()
const pages = computed(() => Math.max(1, Math.ceil(props.total / props.perPage)))
const from = computed(() => (props.total ? (props.page - 1) * props.perPage + 1 : 0))
const to = computed(() => Math.min(props.total, props.page * props.perPage))
</script>

<template>
  <nav class="pager" aria-label="Pagination">
    <span class="muted">{{ from }}–{{ to }} of {{ total }}</span>
    <button class="btn btn--sm btn--quiet" :disabled="page <= 1" @click="emit('update:page', page - 1)">← Prev</button>
    <span class="mono">{{ page }} / {{ pages }}</span>
    <button class="btn btn--sm btn--quiet" :disabled="page >= pages" @click="emit('update:page', page + 1)">Next →</button>
  </nav>
</template>
