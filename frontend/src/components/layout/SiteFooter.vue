<script setup lang="ts">
import SkatMark from '@/components/brand/SkatMark.vue'
import DonateLink from '@/components/ui/DonateLink.vue'
</script>

<template>
  <footer class="footer">
    <div class="container footer__inner">
      <div class="footer__brand">
        <SkatMark :size="36" class="footer__mark" />
        <div>
          <p class="footer__name">Скат</p>
          <p class="footer__tag">Поиск бесплатных VPN-конфигураций. Без регистрации, с любовью ❤️</p>
        </div>
      </div>
      <nav class="footer__nav" aria-label="Разделы сайта">
        <RouterLink :to="{ path: '/', hash: '#catch' }">Улов</RouterLink>
        <RouterLink :to="{ path: '/', hash: '#subscribe' }">Подписка</RouterLink>
        <RouterLink :to="{ path: '/', hash: '#how' }">Как это работает</RouterLink>
        <RouterLink :to="{ path: '/', hash: '#faq' }">Вопросы</RouterLink>
        <RouterLink to="/providers/">Провайдеры</RouterLink>
        <RouterLink to="/providers/add/">Добавить провайдера</RouterLink>
        <RouterLink to="/providers/install/">Установка зонда</RouterLink>
      </nav>
      <div class="footer__actions">
        <DonateLink small />
        <a class="btn btn--sm btn--quiet" href="#top">Наверх</a>
      </div>
    </div>
  </footer>
</template>

<style scoped>
.footer {
  border-top: 1px solid var(--line);
  background: #040a10;
}

.footer__inner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding-block: 36px 40px;
}

.footer__brand {
  display: flex;
  align-items: center;
  gap: 14px;
}

.footer__mark {
  color: var(--text-3);
}

.footer__name {
  font-weight: 700;
  letter-spacing: 0.3em;
  text-transform: uppercase;
  font-size: 13px;
}

.footer__tag {
  color: var(--text-3);
  font-size: 14px;
}

.footer__nav {
  order: 3;
  display: flex;
  flex-wrap: wrap;
  flex-basis: 100%;
  gap: 4px 24px;
  padding-top: 20px;
  border-top: 1px solid var(--line);
  font-size: 14px;
}

.footer__nav a {
  color: var(--text-3);
  text-decoration: none;
  transition: color 0.2s ease;
}

.footer__nav a:hover {
  color: var(--text);
}

.footer__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

@media (max-width: 560px) {
  .footer__inner {
    flex-direction: column;
    align-items: flex-start;
  }

  .footer__nav {
    align-self: stretch;
  }
}
</style>
