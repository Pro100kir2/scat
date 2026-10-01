<script setup lang="ts">
import { onMounted, ref } from 'vue'
import CopyButton from '@/components/ui/CopyButton.vue'
import { SUBSCRIPTION_CLIENTS as CLIENTS, SUBSCRIPTION_PATH as SUB_PATH } from '@scat/shared'
import { apiHref, env } from '@/config/env'
// Without a configured API origin the link is same-origin, which only the browser knows;
// the prerendered page starts with the bare path and the full link replaces it after hydration.
const url = ref(`${env.apiUrl}${SUB_PATH}`)
onMounted(() => (url.value = apiHref(SUB_PATH)))

</script>

<template>
  <section id="subscribe" class="sub container" aria-labelledby="sub-title">
    <div class="sub__panel">
      <div class="sub__text">
        <p class="eyebrow">Подписка</p>
        <h2 id="sub-title" class="section-title">Одна ссылка — <em>всегда свежий</em> улов</h2>
        <p class="sub__lead">
          Добавьте ссылку в VPN-клиент как подписку. Он будет сам забирать пять лучших жемчужин после каждого погружения.
        </p>
        <ul class="sub__clients">
          <li v-for="c in CLIENTS" :key="c.name">
            <span class="sub__client">{{ c.name }}</span><span class="sub__where">{{ c.where }}</span>
          </li>
        </ul>
      </div>

      <div class="sub__box">
        <code class="sub__url mono">{{ url }}</code>
        <CopyButton :text="url" label="Копировать ссылку" variant="primary" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.sub {
  padding-bottom: clamp(64px, 10vw, 120px);
}

.sub__panel {
  display: grid;
  grid-template-columns: 1.1fr 1fr;
  gap: clamp(28px, 5vw, 64px);
  align-items: center;
  padding: clamp(28px, 5vw, 56px);
  border: 1px solid var(--line);
  border-radius: 24px;
  background:
    radial-gradient(90% 120% at 100% 0%, rgba(143, 214, 207, 0.07), transparent 60%),
    var(--deep);
}

.sub__text .eyebrow {
  margin-bottom: 10px;
}

.sub__lead {
  margin-top: 16px;
  color: var(--text-2);
}

.sub__clients {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 20px;
  margin: 26px 0 0;
  padding: 0;
  list-style: none;
}

.sub__clients li {
  display: grid;
}

.sub__client {
  font-weight: 600;
  font-size: 15px;
}

.sub__where {
  color: var(--text-3);
  font-size: 13px;
}

.sub__box {
  display: grid;
  gap: 16px;
  justify-items: start;
}

.sub__url {
  width: 100%;
  padding: 16px 18px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: rgba(3, 12, 18, 0.6);
  color: var(--text-2);
  font-size: 13px;
  word-break: break-all;
}

@media (max-width: 860px) {
  .sub__panel {
    grid-template-columns: 1fr;
  }
}
</style>
