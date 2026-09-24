<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { OceanEngine, type Rect, type SceneMode } from '@/ocean/engine'

const props = defineProps<{
  mode: SceneMode
  /** Discoveries so far in the current dive; increments become pearls. */
  found: number
  reducedMotion: boolean
  safeZone: Rect | null
}>()

const emit = defineEmits<{
  reveal: [total: number]
  drained: []
  pearl: [rank: number]
}>()

const canvas = ref<HTMLCanvasElement>()
const hovering = ref(false)
let engine: OceanEngine | null = null
let queued = 0

function create() {
  engine?.destroy()
  queued = 0
  engine = new OceanEngine(canvas.value!, {
    reducedMotion: props.reducedMotion,
    onReveal: (total) => emit('reveal', total),
    onDrained: () => emit('drained'),
  })
  engine.setSafeZone(props.safeZone)
  engine.setMode(props.mode)
  sync(props.found)
}

function sync(found: number) {
  if (!engine) return
  if (found < queued) {
    engine.reset()
    queued = 0
  }
  engine.discover(found - queued)
  queued = found
}

onMounted(create)
onBeforeUnmount(() => engine?.destroy())

watch(() => props.found, sync)
watch(() => props.mode, (mode) => engine?.setMode(mode))
watch(() => props.safeZone, (rect) => engine?.setSafeZone(rect))
watch(() => props.reducedMotion, create)

function localPoint(e: PointerEvent) {
  const rect = canvas.value!.getBoundingClientRect()
  return { x: e.clientX - rect.left, y: e.clientY - rect.top }
}

/** Pointer events come from the hero so text on top stays interactive. */
function onPointerMove(e: PointerEvent) {
  if (!engine || e.pointerType === 'touch') return
  const { x, y } = localPoint(e)
  hovering.value = engine.pointerMove(x, y, true) !== null
}

function onPointerLeave() {
  engine?.pointerMove(0, 0, false)
  hovering.value = false
}

function onClick(e: PointerEvent) {
  if (!engine) return
  const { x, y } = localPoint(e)
  const rank = engine.pointerMove(x, y, true)
  if (rank !== null) emit('pearl', rank)
}

defineExpose({ onPointerMove, onPointerLeave, onClick, hovering })
</script>

<template>
  <canvas ref="canvas" class="ocean" aria-hidden="true"></canvas>
</template>

<style scoped>
.ocean {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
  background: #0b2533;
}
</style>
