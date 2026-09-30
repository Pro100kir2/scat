<script setup lang="ts">
import CopyButton from '@/components/ui/CopyButton.vue'

/** A shell command to copy, laid out like the subscription link box. */
defineProps<{ command: string; label?: string }>()
</script>

<template>
  <div class="cmd">
    <p v-if="label" class="cmd__label">{{ label }}</p>
    <div class="cmd__box">
      <code class="cmd__text mono">{{ command }}</code>
      <CopyButton :text="command" small label="Копировать" />
    </div>
  </div>
</template>

<style scoped>
.cmd + .cmd {
  margin-top: 12px;
}

.cmd__label {
  margin-bottom: 6px;
  font-size: 13px;
  color: var(--text-3);
}

.cmd__box {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 10px 10px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: rgba(3, 12, 18, 0.6);
}

.cmd__text {
  flex: 1;
  min-width: 0;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--text);
  overflow-wrap: anywhere;
}

@media (max-width: 560px) {
  .cmd__box {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
