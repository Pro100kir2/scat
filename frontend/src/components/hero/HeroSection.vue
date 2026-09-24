<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import OceanScene from '@/components/ocean/OceanScene.vue'
import AppIcon from '@/components/ui/AppIcon.vue'
import DiveLog from './DiveLog.vue'
import { useScan } from '@/composables/useScan'
import { useReducedMotion } from '@/composables/useReducedMotion'
import { useSpotlight } from '@/composables/useSpotlight'
import type { Rect, SceneMode } from '@/ocean/engine'
import { plural, PEARLS_NOM } from '@/utils/format'

const scan = useScan()
const reducedMotion = useReducedMotion()
const { focusRank } = useSpotlight()

const hero = ref<HTMLElement>()
const content = ref<HTMLElement>()
const scene = ref<InstanceType<typeof OceanScene>>()
const safeZone = shallowRef<Rect | null>(null)
const revealed = ref(0)

const mode = computed<SceneMode>(() => {
  if (scan.journey.value !== 'diving') return 'idle'
  return scan.diveKind.value === 'live' ? 'scanning' : 'replay'
})
const sceneFound = computed(() => (scan.journey.value === 'resting' ? 0 : scan.found.value))
watch(sceneFound, (n) => {
  if (n === 0) revealed.value = 0
})

const diving = computed(() => scan.journey.value === 'diving')
const catchCount = computed(() => scan.nodes.value.length)
const showCounter = computed(() => scan.journey.value !== 'resting')

const COOLDOWN_KEY = 'skat_reset_cooldown_until'
const resetSecondsLeft = ref(0)
let timerInterval: ReturnType<typeof setInterval> | undefined

const formattedResetTimer = computed(() => {
  const m = Math.floor(resetSecondsLeft.value / 60)
  const s = resetSecondsLeft.value % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
})

const isResetDisabled = computed(() => diving.value || resetSecondsLeft.value > 0)

function updateResetTimer() {
  const stored = localStorage.getItem(COOLDOWN_KEY)
  if (!stored) {
    resetSecondsLeft.value = 0
    return
  }
  const endTime = Number(stored)
  const remaining = Math.max(0, Math.ceil((endTime - Date.now()) / 1000))
  resetSecondsLeft.value = remaining
  if (remaining <= 0) {
    localStorage.removeItem(COOLDOWN_KEY)
  }
}

function onResetCache() {
  if (isResetDisabled.value) return
  const endTime = Date.now() + 10 * 60 * 1000
  localStorage.setItem(COOLDOWN_KEY, String(endTime))
  updateResetTimer()
  void scan.resetCache()
}

function onDrained() {
  if (mode.value === 'replay') scan.completeReplay()
}

function onPrimary() {
  if (scan.journey.value === 'surfaced' && catchCount.value) {
    document.getElementById('catch')?.scrollIntoView({ behavior: reducedMotion.value ? 'auto' : 'smooth' })
    return
  }
  void scan.startSearch()
}

/** Keep the manta out from behind the headline. */
function measure() {
  if (!hero.value || !content.value) return
  const h = hero.value.getBoundingClientRect()
  const c = content.value.getBoundingClientRect()
  safeZone.value = { x: c.left - h.left, y: c.top - h.top, width: c.width, height: c.height }
}

let observer: ResizeObserver | undefined
onMounted(() => {
  observer = new ResizeObserver(measure)
  if (hero.value) observer.observe(hero.value)
  if (content.value) observer.observe(content.value)
  updateResetTimer()
  timerInterval = setInterval(updateResetTimer, 1000)
})
onBeforeUnmount(() => {
  observer?.disconnect()
  if (timerInterval) clearInterval(timerInterval)
})
</script>

<template>
  <section
    id="top"
    ref="hero"
    class="hero"
    :class="{ 'is-pointer': scene?.hovering }"
    @pointermove="scene?.onPointerMove($event)"
    @pointerleave="scene?.onPointerLeave()"
    @click="scene?.onClick($event as PointerEvent)"
  >
    <OceanScene
      ref="scene"
      :mode="mode"
      :found="sceneFound"
      :reduced-motion="reducedMotion"
      :safe-zone="safeZone"
      @reveal="revealed = $event"
      @drained="onDrained"
      @pearl="focusRank"
    />

    <div class="container hero__inner">
      <div ref="content" class="hero__content">
        <p class="eyebrow hero__eyebrow">VLESS · проверка через Xray</p>
        <h1 class="hero__title">
          Ищет жемчужины<br />
          <em>в глубинах интернета</em>
        </h1>
        <p class="hero__lead">
          Скат просматривает тысячи открытых конфигураций, подключается к каждой по‑настоящему
          и поднимает на поверхность только те, что работают быстро и стабильно.
        </p>

        <div class="hero__actions">
          <button class="btn btn--primary hero__cta" type="button" :disabled="diving" @click.stop="onPrimary">
            <template v-if="diving">
              <span class="hero__cta-pulse" aria-hidden="true"></span>
              Скат на глубине
            </template>
            <template v-else-if="scan.journey.value === 'surfaced' && catchCount">
              Смотреть улов
              <AppIcon name="arrowDown" />
            </template>
            <template v-else>Начать поиск</template>
          </button>

          <button
            class="btn btn--secondary hero__reset"
            type="button"
            :disabled="isResetDisabled"
            @click.stop="onResetCache"
          >
            Сбросить кеш
            <span v-if="resetSecondsLeft > 0" class="hero__timer mono">({{ formattedResetTimer }})</span>
          </button>

          <a class="btn btn--quiet" href="#how" @click.stop>Как это работает</a>
        </div>

        <div class="hero__status">
          <Transition name="counter">
            <div v-if="showCounter" class="hero__counter" aria-live="polite">
              <span class="hero__pearl" aria-hidden="true"></span>
              <span class="mono hero__count">{{ revealed }}</span>
              <span class="hero__count-label">{{ plural(revealed, PEARLS_NOM) }}</span>
            </div>
          </Transition>
          <DiveLog :revealed="revealed" />
        </div>
      </div>
    </div>

    <a class="hero__scroll" href="#catch" aria-label="К результатам" @click.stop>
      <AppIcon name="chevron" />
    </a>
  </section>
</template>

<style scoped>
.hero {
  position: relative;
  min-height: max(640px, 100svh);
  display: flex;
  overflow: hidden;
  isolation: isolate;
}

.hero.is-pointer {
  cursor: pointer;
}

.hero__inner {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  padding-top: 88px;
  padding-bottom: 20svh;
  pointer-events: none;
}

.hero__content {
  max-width: 640px;
  pointer-events: auto;
  cursor: auto;
}

.hero__eyebrow {
  margin-bottom: 22px;
  color: var(--text-2);
  opacity: 0.8;
}

.hero__title {
  font-family: var(--font-display);
  font-size: clamp(2.6rem, 5.4vw, 4.6rem);
  font-weight: 500;
  line-height: 0.98;
  letter-spacing: -0.015em;
  text-shadow: 0 2px 30px rgba(3, 12, 18, 0.5);
}

.hero__title em {
  display: inline-block;
  margin-top: 0.06em;
  color: var(--pearl);
  font-style: italic;
}

.hero__lead {
  max-width: 440px;
  margin-top: 22px;
  color: var(--text-2);
  font-size: 17px;
  line-height: 1.6;
}

.hero__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 34px;
}

.hero__cta {
  min-height: 52px;
  padding: 0 28px;
  font-size: 16px;
}

.hero__cta:disabled {
  opacity: 1;
  --btn-bg: rgba(230, 238, 241, 0.1);
  --btn-fg: var(--text);
  --btn-border: var(--line-strong);
  box-shadow: none;
}

.hero__cta-pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--aqua);
  animation: breathe 1.8s var(--ease-in-out) infinite;
}

.hero__reset {
  min-height: 52px;
  padding: 0 20px;
  font-size: 15px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.hero__reset:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.hero__timer {
  font-size: 14px;
  color: var(--pearl);
  opacity: 0.9;
}

.hero__status {
  display: grid;
  gap: 18px;
  min-height: 92px;
  margin-top: 30px;
}

.hero__counter {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.hero__pearl {
  width: 12px;
  height: 12px;
  align-self: center;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 30%, #fffaf1, #eadcc6 50%, #aebccb 100%);
  box-shadow: 0 0 18px var(--pearl-glow);
}

.hero__count {
  font-size: 30px;
  font-weight: 500;
  color: var(--pearl);
  line-height: 1;
}

.hero__count-label {
  color: var(--text-2);
  font-size: 15px;
}

.hero__scroll {
  position: absolute;
  left: 50%;
  bottom: 22px;
  z-index: 1;
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  margin-left: -22px;
  border-radius: 50%;
  color: var(--text-3);
  transition: color 0.2s;
  animation: drift 3.2s var(--ease-in-out) infinite;
}

.hero__scroll:hover {
  color: var(--text);
}

.counter-enter-active {
  transition: opacity 0.5s var(--ease-out), transform 0.6s var(--ease-out);
}

.counter-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

@keyframes breathe {
  50% {
    opacity: 0.35;
    transform: scale(0.8);
  }
}

@keyframes drift {
  50% {
    transform: translateY(5px);
  }
}

@media (max-width: 720px) {
  .hero {
    min-height: max(700px, 100svh);
  }

  .hero__inner {
    align-items: flex-start;
    padding-top: 104px;
    padding-bottom: 0;
  }

  .hero__lead {
    font-size: 16px;
  }

  .hero__actions {
    margin-top: 28px;
  }

  .hero__cta {
    flex: 1 1 100%;
  }

  .hero__actions .btn--quiet {
    display: none;
  }

  .hero__status {
    margin-top: 26px;
  }

  .hero__scroll {
    display: none;
  }
}
</style>
