<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { dialog, settle } from '../confirm'

const el = ref<HTMLDialogElement | null>(null)
const cancelButton = ref<HTMLButtonElement | null>(null)

watch(() => dialog.open, async (open) => {
  await nextTick()
  if (!el.value) return
  if (open && !el.value.open) {
    el.value.showModal?.()
    cancelButton.value?.focus()  // the safe choice has focus
  } else if (!open && el.value.open) {
    el.value.close()
  }
})
</script>

<template>
  <dialog ref="el" class="sheet confirm" aria-labelledby="confirm-title" @cancel.prevent="settle(false)">
    <h2 id="confirm-title" class="confirm__title">{{ dialog.title }}</h2>
    <p class="confirm__body">{{ dialog.body }}</p>
    <div class="confirm__actions">
      <button ref="cancelButton" class="btn btn--quiet" @click="settle(false)">Cancel</button>
      <button class="btn" :class="dialog.danger ? 'btn--danger' : 'btn--primary'" @click="settle(true)">{{ dialog.confirm }}</button>
    </div>
  </dialog>
</template>
