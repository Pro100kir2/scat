<script setup lang="ts">
import { computed, ref } from 'vue'
import type { PearlNode } from '@/types/api'
import CopyButton from '@/components/ui/CopyButton.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import NetworkAvailability from './NetworkAvailability.vue'
import {
  countryName,
  latencyQuality,
  networkLabel,
  QUALITY_LABEL,
  QUALITY_LEVEL,
  securityHint,
  securityLabel,
} from '@/utils/format'

const props = defineProps<{ node: PearlNode; spotlit: boolean }>()
const emit = defineEmits<{ qr: [node: PearlNode] }>()

const open = ref(false)
const quality = computed(() => latencyQuality(props.node.latency_ms))
const place = computed(() => countryName(props.node.country) ?? 'Страна неизвестна')
const detailsId = computed(() => `pearl-details-${props.node.id}`)
</script>

<template>
  <article :id="`pearl-${node.rank}`" class="card" :class="{ 'is-open': open, 'is-spotlit': spotlit, 'is-top': node.rank === 1 }">
    <div class="card__main">
      <div class="card__rank" :title="`Место ${node.rank}`">
        <span class="card__pearl" :class="`q-${quality}`" aria-hidden="true"></span>
        <span class="mono card__rank-num">{{ String(node.rank).padStart(2, '0') }}</span>
      </div>

      <div class="card__where">
        <p class="card__place">
          <span v-if="node.country" class="card__cc mono">{{ node.country }}</span>
          {{ place }}
        </p>
        <p class="card__host mono" :title="`${node.address}:${node.port}`">{{ node.address }}</p>
      </div>

      <div class="card__tags">
        <span class="tag" :title="securityHint(node.security)" :class="{ 'tag--warn': node.security === 'none' }">
          {{ securityLabel(node.security) }}
        </span>
        <span class="tag">{{ networkLabel(node.network) }}</span>
      </div>

      <div class="card__speed" :title="`Время полного запроса через конфигурацию (обычный пинг ниже). Прошла ${node.checks_passed} из ${node.checks_total} проверок.`">
        <span class="meter" :data-level="QUALITY_LEVEL[quality]" aria-hidden="true">
          <i></i><i></i><i></i><i></i>
        </span>
        <span class="card__speed-text">
          <span class="mono card__ms">{{ node.latency_ms ?? '—' }}<small> мс</small><template v-if="node.speed_mbps"><small class="card__sep"> · </small>{{ node.speed_mbps }}<small> Мбит/с</small></template></span>
          <span class="card__quality">
            {{ QUALITY_LABEL[quality] }}<template v-if="!node.stable"> · {{ node.checks_passed }}/{{ node.checks_total }}</template>
          </span>
        </span>
      </div>

      <div class="card__actions">
        <CopyButton :text="node.link" small />
        <button
          class="btn btn--sm btn--quiet card__toggle"
          type="button"
          :aria-expanded="open"
          :aria-controls="detailsId"
          :aria-label="open ? 'Скрыть подробности' : 'Подробнее'"
          @click="open = !open"
        >
          <AppIcon name="chevron" class="card__chevron" />
        </button>
      </div>
    </div>

    <div :id="detailsId" class="card__details" :inert="!open">
      <div class="card__details-inner">
        <dl class="facts">
          <div>
            <dt>Адрес</dt>
            <dd class="mono">{{ node.address }}:{{ node.port }}</dd>
          </div>
          <div>
            <dt>Глубокая проверка</dt>
            <dd>
              {{ node.stable ? 'Стабильна' : 'Нестабильна' }}
              <span class="mono muted">{{ node.checks_passed }}/{{ node.checks_total }} проверок</span>
            </dd>
          </div>
          <div v-if="node.probe_total">
            <dt>Подтверждено провайдерами</dt>
            <dd>
              <span class="mono">{{ node.probe_score }}/{{ node.probe_total }}</span>
              <span class="muted"> — зонды в сетях провайдеров дотянулись до сервера</span>
            </dd>
          </div>
          <div v-if="node.speed_mbps">
            <dt>Скорость загрузки</dt>
            <dd class="mono">{{ node.speed_mbps }} Мбит/с</dd>
          </div>
          <div v-if="node.jitter_ms !== null">
            <dt>Разброс задержки</dt>
            <dd class="mono">± {{ node.jitter_ms }} мс</dd>
          </div>
          <div v-if="node.sni">
            <dt>Маскировка (SNI)</dt>
            <dd class="mono">{{ node.sni }}</dd>
          </div>
          <div v-if="node.flow">
            <dt>Flow</dt>
            <dd class="mono">{{ node.flow }}</dd>
          </div>
          <div v-if="node.tls_fingerprint">
            <dt>Отпечаток TLS</dt>
            <dd class="mono">{{ node.tls_fingerprint }}</dd>
          </div>
          <div v-if="node.label">
            <dt>Метка источника</dt>
            <dd>{{ node.label }}</dd>
          </div>
        </dl>
        <NetworkAvailability v-if="node.reachability" :rows="node.reachability" />
        <div class="card__link">
          <code class="mono">{{ node.link }}</code>
        </div>
        <div class="card__more">
          <CopyButton :text="node.link" label="Копировать ссылку" small variant="primary" />
          <button class="btn btn--sm" type="button" @click="emit('qr', node)">
            <AppIcon name="qr" /> QR-код
          </button>
        </div>
      </div>
    </div>
  </article>
</template>

<style scoped>
.card {
  position: relative;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: linear-gradient(180deg, rgba(16, 38, 52, 0.55), rgba(10, 26, 37, 0.55));
  transition: border-color 0.3s var(--ease-out), background-color 0.3s var(--ease-out), box-shadow 0.5s var(--ease-out);
}

.card:hover {
  border-color: var(--line-strong);
}

.card.is-top {
  border-color: rgba(243, 233, 216, 0.2);
}

.card.is-spotlit {
  border-color: rgba(243, 233, 216, 0.6);
  box-shadow: 0 0 0 4px rgba(243, 233, 216, 0.08), 0 20px 60px -20px var(--pearl-glow);
}

.card__main {
  display: grid;
  grid-template-columns: 64px minmax(0, 1.3fr) minmax(0, 0.8fr) 210px auto;
  align-items: center;
  gap: 18px;
  padding: 16px 16px 16px 20px;
}

.card__rank {
  display: flex;
  align-items: center;
  gap: 10px;
}

.card__pearl {
  --size: 12px;
  width: var(--size);
  height: var(--size);
  flex: none;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 30%, #fffaf1, #eadcc6 50%, #9fb0bf 100%);
  box-shadow: 0 0 14px rgba(243, 233, 216, 0.35);
}

.card__pearl.q-good {
  --size: 11px;
  opacity: 0.9;
}

.card__pearl.q-fair {
  --size: 10px;
  opacity: 0.75;
  box-shadow: none;
}

.card__pearl.q-slow {
  --size: 9px;
  opacity: 0.55;
  box-shadow: none;
}

.is-top .card__pearl {
  --size: 15px;
  box-shadow: 0 0 22px rgba(243, 233, 216, 0.55);
}

.card__rank-num {
  color: var(--text-3);
  font-size: 13px;
}

.card__place {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 15.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.card__cc {
  padding: 1px 6px;
  border: 1px solid var(--line-strong);
  border-radius: 5px;
  font-size: 11px;
  color: var(--text-2);
}

.card__host {
  margin-top: 2px;
  font-size: 12.5px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.card__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tag {
  padding: 3px 9px;
  border-radius: 6px;
  background: rgba(168, 208, 222, 0.07);
  color: var(--text-2);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.tag--warn {
  background: var(--coral-soft);
  color: var(--coral);
}

.card__speed {
  display: flex;
  align-items: center;
  gap: 12px;
}

.meter {
  display: flex;
  align-items: flex-end;
  gap: 2px;
  height: 16px;
}

.meter i {
  width: 3px;
  border-radius: 1px;
  background: var(--line-strong);
}

.meter i:nth-child(1) { height: 5px; }
.meter i:nth-child(2) { height: 8px; }
.meter i:nth-child(3) { height: 12px; }
.meter i:nth-child(4) { height: 16px; }

.meter[data-level='4'] i,
.meter[data-level='3'] i:nth-child(-n + 3),
.meter[data-level='2'] i:nth-child(-n + 2),
.meter[data-level='1'] i:nth-child(1) {
  background: var(--pearl);
}

.meter[data-level='1'] i:nth-child(1) {
  background: var(--sand);
}

.card__speed-text {
  display: grid;
  line-height: 1.2;
}

.card__ms {
  font-size: 16px;
  font-weight: 500;
}

.card__sep {
  margin: 0 2px;
}

.card__ms small {
  font-size: 11px;
  color: var(--text-3);
}

.card__quality {
  font-size: 12px;
  color: var(--text-3);
}

.card__actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.card__toggle {
  width: 36px;
  padding: 0;
}

.card__chevron {
  transition: transform 0.4s var(--ease-out);
}

.is-open .card__chevron {
  transform: rotate(180deg);
}

/* Height animation without measuring: grid rows 0fr → 1fr. */
.card__details {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows 0.45s var(--ease-out);
}

.is-open .card__details {
  grid-template-rows: 1fr;
}

.card__details-inner {
  min-height: 0;
  overflow: hidden;
  padding: 0 20px;
  opacity: 0;
  transition: opacity 0.3s var(--ease-out), padding 0.45s var(--ease-out);
}

.is-open .card__details-inner {
  padding: 4px 20px 20px;
  opacity: 1;
}

.facts {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 14px 24px;
  margin: 0;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}

.facts dt {
  font-size: 12px;
  color: var(--text-3);
}

.facts dd {
  margin: 2px 0 0;
  font-size: 14px;
  overflow-wrap: anywhere;
}

.facts dd.mono {
  font-size: 13px;
}

.muted {
  margin-left: 6px;
  font-size: 12px;
  color: var(--text-3);
}

.card__link {
  margin-top: 18px;
  padding: 12px 14px;
  border-radius: var(--radius-sm);
  background: rgba(3, 12, 18, 0.55);
  max-height: 96px;
  overflow: auto;
}

.card__link code {
  font-size: 12px;
  line-height: 1.55;
  color: var(--text-2);
  word-break: break-all;
}

.card__more {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

@media (max-width: 900px) {
  .card__main {
    grid-template-columns: auto minmax(0, 1fr) auto;
    grid-template-areas:
      'rank where speed'
      'tags tags actions';
    gap: 14px 14px;
    padding: 16px;
  }

  .card__rank { grid-area: rank; }
  .card__where { grid-area: where; }
  .card__speed { grid-area: speed; }
  .card__tags { grid-area: tags; }
  .card__actions { grid-area: actions; }

  .card__rank-num {
    display: none;
  }

  .card__quality {
    display: none;
  }

  .card__details-inner,
  .is-open .card__details-inner {
    padding-inline: 16px;
  }
}
</style>
