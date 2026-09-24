<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { PearlNode } from '@/types/api'
import AppIcon from '@/components/ui/AppIcon.vue'
import CopyButton from '@/components/ui/CopyButton.vue'
import { countryName } from '@/utils/format'

const props = defineProps<{ node: PearlNode | null }>()
const emit = defineEmits<{ close: [] }>()

const dialog = ref<HTMLDialogElement>()
const svg = ref('')

watch(
  () => props.node,
  async (node) => {
    if (!node) {
      dialog.value?.close()
      return
    }
    svg.value = ''
    // The QR encoder is only needed here, so it is loaded on first use.
    const { renderSVG } = await import('uqr')
    svg.value = renderSVG(node.link, { ecc: 'M', border: 2, whiteColor: '#f3e9d8', blackColor: '#06131b' })
    await nextTick()
    if (!dialog.value?.open) dialog.value?.showModal()
  },
)
</script>

<template>
  <dialog ref="dialog" class="qr" aria-labelledby="qr-title" @close="emit('close')" @click.self="dialog?.close()">
    <div v-if="node" class="qr__body">
      <button class="btn btn--sm btn--quiet qr__close" type="button" aria-label="Закрыть" @click="dialog?.close()">
        <AppIcon name="close" />
      </button>
      <p class="eyebrow">Жемчужина № {{ node.rank }}</p>
      <h3 id="qr-title" class="qr__title">{{ countryName(node.country) ?? node.address }}</h3>
      <!-- SVG is generated locally by uqr from the link; no external HTML is injected. -->
      <!-- eslint-disable-next-line vue/no-v-html -->
      <div class="qr__code" v-html="svg"></div>
      <p class="qr__hint">Отсканируйте камерой в v2rayNG, Hiddify, Streisand или другом клиенте.</p>
      <CopyButton :text="node.link" label="Копировать ссылку" variant="primary" />
    </div>
  </dialog>
</template>

<style scoped>
.qr {
  width: min(360px, calc(100vw - 32px));
  padding: 0;
  border: 1px solid var(--line-strong);
  border-radius: 22px;
  background: var(--sea);
  color: var(--text);
  box-shadow: 0 40px 120px -20px rgba(0, 0, 0, 0.7);
}

.qr[open] {
  animation: rise 0.45s var(--ease-out);
}

.qr::backdrop {
  background: rgba(3, 10, 16, 0.72);
  backdrop-filter: blur(6px);
}

.qr__body {
  position: relative;
  display: grid;
  justify-items: center;
  gap: 12px;
  padding: 28px 24px 24px;
  text-align: center;
}

.qr__close {
  position: absolute;
  top: 10px;
  right: 10px;
  width: 40px;
  padding: 0;
}

.qr__title {
  font-family: var(--font-display);
  font-size: 28px;
}

.qr__code {
  width: 240px;
  height: 240px;
  margin: 6px 0;
  overflow: hidden;
  border-radius: 14px;
  background: var(--pearl);
}

.qr__code :deep(svg) {
  display: block;
  width: 100%;
  height: 100%;
}

.qr__hint {
  max-width: 260px;
  margin-bottom: 6px;
  color: var(--text-3);
  font-size: 13px;
}

@keyframes rise {
  from {
    opacity: 0;
    transform: translateY(16px) scale(0.98);
  }
}
</style>
