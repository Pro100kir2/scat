<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { env } from '@/config/env'
import { storage } from '@/utils/storage'
import AppIcon from './AppIcon.vue'
import DonateLink from './DonateLink.vue'

const SEEN_KEY = 'skat:welcome-seen'
// Let the page settle first so the note doesn't pop up over a blank screen.
const SHOW_DELAY_MS = 1200

const dialog = ref<HTMLDialogElement>()
let timer: ReturnType<typeof setTimeout> | undefined

onMounted(() => {
  if (storage.get(SEEN_KEY)) return
  timer = setTimeout(() => dialog.value?.showModal(), SHOW_DELAY_MS)
})
onBeforeUnmount(() => clearTimeout(timer))

function onClose() {
  storage.set(SEEN_KEY, '1')
}
</script>

<template>
  <dialog ref="dialog" class="sheet welcome" aria-labelledby="welcome-title" @close="onClose" @click.self="dialog?.close()">
    <div class="welcome__body">
      <button class="btn btn--sm btn--quiet sheet__close" type="button" aria-label="Закрыть" @click="dialog?.close()">
        <AppIcon name="close" />
      </button>
      <p class="eyebrow">Пара слов от команды</p>
      <h2 id="welcome-title" class="welcome__title">Привет! Рады, что вы заплыли</h2>
      <p>
        Сразу извинимся за рекламу на сайте — мы знаем, что она мало кому нравится.
        Скат делает небольшая команда, и мы не пытаемся на вас заработать лишнего.
      </p>
      <p>
        Но серверы и ежедневные проверки тысяч конфигураций стоят денег, и реклама — главное,
        что помогает Скату оставаться бесплатным и развиваться дальше.
      </p>
      <p v-if="env.donateUrl">
        Если захочется помочь, можно поддержать проект любой суммой. Это совсем необязательно,
        но каждый раз очень нас радует.
      </p>
      <div class="welcome__actions">
        <button class="btn btn--primary" type="button" @click="dialog?.close()">Понятно, спасибо</button>
        <DonateLink />
      </div>
    </div>
  </dialog>
</template>

<style scoped>
.welcome {
  width: min(460px, calc(100vw - 32px));
}

.welcome__body {
  position: relative;
  display: grid;
  gap: 14px;
  padding: 30px 28px 26px;
  color: var(--text-2);
  font-size: 15px;
  line-height: 1.6;
}

.welcome__title {
  margin-bottom: 4px;
  padding-right: 32px;
  color: var(--text);
  font-family: var(--font-display);
  font-size: 30px;
  font-weight: 500;
  line-height: 1.1;
}

.welcome__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 8px;
}

@media (max-width: 480px) {
  .welcome__body {
    padding: 26px 20px 20px;
  }

  .welcome__actions .btn {
    flex: 1 1 100%;
  }
}
</style>
