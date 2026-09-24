<script setup lang="ts">
import { onMounted, ref } from 'vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import DonateLink from '@/components/ui/DonateLink.vue'
import { env } from '@/config/env'

const STORAGE_KEY = 'skat_welcome_seen'
const dialog = ref<HTMLDialogElement>()

function closeModal() {
  localStorage.setItem(STORAGE_KEY, 'true')
  dialog.value?.close()
}

onMounted(() => {
  const seen = localStorage.getItem(STORAGE_KEY)
  if (!seen) {
    dialog.value?.showModal()
  }
})
</script>

<template>
  <dialog
    ref="dialog"
    class="notice"
    aria-labelledby="notice-title"
    @close="closeModal"
    @click.self="closeModal"
  >
    <div class="notice__body">
      <button class="btn btn--sm btn--quiet notice__close" type="button" aria-label="Закрыть" @click="closeModal">
        <AppIcon name="close" />
      </button>

      <div class="notice__badge" aria-hidden="true">
        <AppIcon name="heart" class="notice__heart" />
      </div>

      <h3 id="notice-title" class="notice__title">Приветствуем в Скате!</h3>

      <div class="notice__content">
        <p>
          Приносим извинения за наличие рекламы на сайте. Наша команда старается развивать
          и поддерживать этот сервис, и мы не хотим наживаться на пользователях.
          Показ рекламы — это единственный основной способ покрывать затраты на серверы
          и обеспечение работы проекта.
        </p>
        <p>
          Если вам нравится наш сервис и у вас есть желание поддержать команду,
          вы можете пожертвовать любую сумму. Мы будем очень благодарны!
        </p>
      </div>

      <div class="notice__actions">
        <DonateLink v-if="env.donateUrl" label="Пожертвовать" small />
        <button class="btn btn--primary btn--sm" type="button" @click="closeModal">
          Понятно
        </button>
      </div>
    </div>
  </dialog>
</template>

<style scoped>
.notice {
  width: min(440px, calc(100vw - 32px));
  padding: 0;
  border: 1px solid var(--line-strong);
  border-radius: 22px;
  background: var(--sea);
  color: var(--text);
  box-shadow: 0 40px 120px -20px rgba(0, 0, 0, 0.7);
}

.notice[open] {
  animation: rise 0.45s var(--ease-out);
}

.notice::backdrop {
  background: rgba(3, 10, 16, 0.72);
  backdrop-filter: blur(6px);
}

.notice__body {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding: 28px 24px 24px;
  text-align: center;
}

.notice__close {
  position: absolute;
  top: 12px;
  right: 12px;
  width: 36px;
  height: 36px;
  padding: 0;
}

.notice__badge {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: rgba(243, 233, 216, 0.08);
  border: 1px solid rgba(243, 233, 216, 0.2);
  color: var(--pearl);
}

.notice__heart {
  width: 22px;
  height: 22px;
  fill: rgba(243, 233, 216, 0.3);
}

.notice__title {
  font-family: var(--font-display);
  font-size: 26px;
  font-weight: 500;
  color: var(--pearl);
}

.notice__content {
  display: flex;
  flex-direction: column;
  gap: 12px;
  color: var(--text-2);
  font-size: 15px;
  line-height: 1.55;
  text-align: left;
}

.notice__actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
  width: 100%;
  margin-top: 8px;
}

@keyframes rise {
  from {
    opacity: 0;
    transform: translateY(16px) scale(0.98);
  }
}
</style>
