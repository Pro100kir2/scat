<script setup lang="ts">
import AppIcon from '@/components/ui/AppIcon.vue'
import { FAQ } from '@/content/faq'
</script>

<template>
  <section id="faq" class="faq container" aria-labelledby="faq-title">
    <div class="faq__intro">
      <p class="eyebrow">Вопросы</p>
      <h2 id="faq-title" class="section-title">Прежде чем <em>нырнуть</em></h2>
      <p class="faq__lead">Коротко о том, что такое Скат, как подключить найденный VPN и чего от него ждать.</p>
    </div>

    <div class="faq__list">
      <!-- <details> keeps every answer in the page for readers and search engines alike. -->
      <details v-for="item in FAQ" :id="`faq-${item.id}`" :key="item.id" class="faq__item">
        <summary class="faq__q">
          <h3 class="faq__q-text">{{ item.question }}</h3>
          <AppIcon name="chevron" class="faq__icon" />
        </summary>
        <div class="faq__a">
          <p v-for="(para, i) in item.answer" :key="i">{{ para }}</p>
        </div>
      </details>
    </div>
  </section>
</template>

<style scoped>
.faq {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.6fr);
  gap: clamp(28px, 5vw, 72px);
  padding-bottom: clamp(72px, 10vw, 128px);
}

.faq .eyebrow {
  margin-bottom: 10px;
}

.faq__lead {
  margin-top: 16px;
  color: var(--text-2);
}

.faq__list {
  border-top: 1px solid var(--line-strong);
}

.faq__item {
  border-bottom: 1px solid var(--line);
}

.faq__q {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 18px 2px;
  cursor: pointer;
  list-style: none;
  border-radius: var(--radius-sm);
}

.faq__q::-webkit-details-marker {
  display: none;
}

.faq__q:focus-visible {
  outline: 2px solid var(--aqua);
  outline-offset: 2px;
}

.faq__q-text {
  font-family: var(--font-ui);
  font-size: 17px;
  font-weight: 600;
  line-height: 1.4;
  transition: color 0.2s ease;
}

.faq__q:hover .faq__q-text {
  color: var(--pearl);
}

.faq__icon {
  flex: none;
  color: var(--text-3);
  transition: transform 0.3s var(--ease-out);
}

.faq__item[open] .faq__icon {
  transform: rotate(180deg);
}

.faq__a {
  padding: 0 2px 20px;
  color: var(--text-2);
}

.faq__a p + p {
  margin-top: 10px;
}

@media (max-width: 860px) {
  .faq {
    grid-template-columns: 1fr;
  }
}
</style>
