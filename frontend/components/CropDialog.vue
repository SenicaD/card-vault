<script setup lang="ts">
// Drag a box around just the card. Works with a finger or a mouse. The box is reported as fractions
// (0-1) of the photo, so it doesn't depend on how large the photo is shown.
export interface CropBoxFrac { x: number; y: number; w: number; h: number }

const props = defineProps<{ modelValue: boolean; src: string | null; busy?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [v: boolean]; apply: [box: CropBoxFrac] }>()

const MIN = 0.08
const box = ref<CropBoxFrac>({ x: 0.15, y: 0.1, w: 0.7, h: 0.8 })
const stage = ref<HTMLElement | null>(null)
let drag: { mode: string; sx: number; sy: number; start: CropBoxFrac } | null = null

watch(() => props.modelValue, (open) => { if (open) box.value = { x: 0.15, y: 0.1, w: 0.7, h: 0.8 } })

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

function down(e: PointerEvent, mode: string) {
  e.preventDefault()
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  drag = { mode, sx: e.clientX, sy: e.clientY, start: { ...box.value } }
}
function move(e: PointerEvent) {
  if (!drag || !stage.value) return
  const r = stage.value.getBoundingClientRect()
  const dx = (e.clientX - drag.sx) / r.width
  const dy = (e.clientY - drag.sy) / r.height
  const s = drag.start
  let { x, y, w, h } = s
  if (drag.mode === 'move') {
    x = clamp(s.x + dx, 0, 1 - s.w)
    y = clamp(s.y + dy, 0, 1 - s.h)
  } else {
    if (drag.mode.includes('w')) { x = clamp(s.x + dx, 0, s.x + s.w - MIN); w = s.x + s.w - x }
    if (drag.mode.includes('e')) { w = clamp(s.w + dx, MIN, 1 - s.x) }
    if (drag.mode.includes('n')) { y = clamp(s.y + dy, 0, s.y + s.h - MIN); h = s.y + s.h - y }
    if (drag.mode.includes('s')) { h = clamp(s.h + dy, MIN, 1 - s.y) }
  }
  box.value = { x, y, w, h }
}
const up = () => { drag = null }

const style = computed(() => ({
  left: `${box.value.x * 100}%`, top: `${box.value.y * 100}%`,
  width: `${box.value.w * 100}%`, height: `${box.value.h * 100}%`,
}))
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="640" scrollable @update:model-value="(v) => emit('update:modelValue', v)">
    <v-card>
      <div class="card-head">
        <div>
          <h2><v-icon icon="mdi-crop" size="18" />Crop to the card</h2>
          <p>Drag the corners so only the card is inside the box.</p>
        </div>
        <v-btn icon="mdi-close" variant="text" size="small" aria-label="Close" @click="emit('update:modelValue', false)" />
      </div>
      <v-card-text class="d-flex justify-center">
        <div v-if="src" ref="stage" class="stage" @pointermove="move" @pointerup="up" @pointercancel="up">
          <img :src="src" alt="Photo to crop" draggable="false" />
          <div class="box" :style="style" @pointerdown="down($event, 'move')">
            <span v-for="h in ['nw', 'ne', 'sw', 'se']" :key="h" class="handle" :class="h" @pointerdown.stop="down($event, h)" />
          </div>
        </div>
      </v-card-text>
      <v-divider />
      <div class="d-flex ga-2 pa-3 justify-end">
        <v-btn variant="text" @click="emit('update:modelValue', false)">Cancel</v-btn>
        <v-btn color="primary" variant="flat" prepend-icon="mdi-crop" :loading="busy" @click="emit('apply', box)">Crop and search again</v-btn>
      </div>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.stage { position: relative; display: inline-block; line-height: 0; touch-action: none; user-select: none; max-width: 100%; }
.stage img { max-width: 100%; max-height: 62vh; display: block; border-radius: 6px; }
.box { position: absolute; border: 2px solid #fff; box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.55); cursor: move; touch-action: none; }
.handle { position: absolute; width: 26px; height: 26px; background: rgb(var(--v-theme-primary)); border: 2px solid #fff; border-radius: 50%; touch-action: none; }
.nw { left: -13px; top: -13px; cursor: nwse-resize; }
.ne { right: -13px; top: -13px; cursor: nesw-resize; }
.sw { left: -13px; bottom: -13px; cursor: nesw-resize; }
.se { right: -13px; bottom: -13px; cursor: nwse-resize; }
</style>
