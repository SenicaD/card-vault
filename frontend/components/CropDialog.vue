<script setup lang="ts">
// Drag each of the four corners onto a corner of the card. Works with a finger or a mouse. The card
// can be tilted or photographed at an angle: the server cuts along these four points and straightens it.
// Points are fractions (0-1) of the photo, so they don't depend on how large the photo is shown.
export interface Pt { x: number; y: number }
export interface CropBoxFrac { x: number; y: number; w: number; h: number; corners: [number, number][] }

const props = defineProps<{ modelValue: boolean; src: string | null; busy?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [v: boolean]; apply: [box: CropBoxFrac] }>()

const fresh = (): Pt[] => [{ x: 0.15, y: 0.1 }, { x: 0.85, y: 0.1 }, { x: 0.85, y: 0.9 }, { x: 0.15, y: 0.9 }] // TL TR BR BL
const pts = ref<Pt[]>(fresh())
const stage = ref<HTMLElement | null>(null)
const NAMES = ['Top left', 'Top right', 'Bottom right', 'Bottom left']
let drag: { i: number | 'all'; sx: number; sy: number; start: Pt[] } | null = null
// The spyglass: while a corner is held, a magnified circle shows the spot under it, offset so a finger doesn't hide it.
const LOUPE = 130, ZOOM = 4
const active = ref<number | null>(null)
const size = ref({ w: 1, h: 1 })

watch(() => props.modelValue, (open) => { if (open) pts.value = fresh() })

const clamp = (v: number, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v))

function down(e: PointerEvent, i: number | 'all') {
  e.preventDefault()
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  drag = { i, sx: e.clientX, sy: e.clientY, start: pts.value.map((p) => ({ ...p })) }
  if (stage.value) { const r = stage.value.getBoundingClientRect(); size.value = { w: r.width, h: r.height } }
  active.value = i === 'all' ? null : i
}
function move(e: PointerEvent) {
  if (!drag || !stage.value) return
  const r = stage.value.getBoundingClientRect()
  const dx = (e.clientX - drag.sx) / r.width
  const dy = (e.clientY - drag.sy) / r.height
  if (drag.i === 'all') {
    // keep the shape: stop when any corner would leave the photo
    const sx = clamp(dx, -Math.min(...drag.start.map((p) => p.x)), 1 - Math.max(...drag.start.map((p) => p.x)))
    const sy = clamp(dy, -Math.min(...drag.start.map((p) => p.y)), 1 - Math.max(...drag.start.map((p) => p.y)))
    pts.value = drag.start.map((p) => ({ x: p.x + sx, y: p.y + sy }))
  } else {
    const s = drag.start[drag.i]
    pts.value = pts.value.map((p, k) => (k === drag!.i ? { x: clamp(s.x + dx), y: clamp(s.y + dy) } : p))
  }
}
const up = () => { drag = null; active.value = null }

const loupe = computed(() => {
  const i = active.value
  if (i === null) return null
  const { w, h } = size.value, p = pts.value[i]
  const px = p.x * w, py = p.y * h
  let left = Math.min(Math.max(px - LOUPE / 2, 0), Math.max(0, w - LOUPE))
  let top = py - LOUPE - 36 // above the corner; below it when there is no room
  if (top < 0) top = Math.min(py + 36, Math.max(0, h - LOUPE))
  return {
    left: `${left}px`, top: `${top}px`, width: `${LOUPE}px`, height: `${LOUPE}px`,
    backgroundImage: `url("${props.src}")`, backgroundSize: `${w * ZOOM}px ${h * ZOOM}px`,
    backgroundPosition: `${LOUPE / 2 - px * ZOOM}px ${LOUPE / 2 - py * ZOOM}px`,
  }
})

const pct = (v: number) => `${(v * 100).toFixed(2)}`
const outline = computed(() => pts.value.map((p) => `${pct(p.x)},${pct(p.y)}`).join(' '))
const dim = computed(() => `M0,0H100V100H0Z M${pts.value.map((p) => `${pct(p.x)},${pct(p.y)}`).join(' L')}Z`)

function apply() {
  const xs = pts.value.map((p) => p.x), ys = pts.value.map((p) => p.y)
  const x = Math.min(...xs), y = Math.min(...ys)
  emit('apply', {
    x, y, w: Math.max(0.01, Math.max(...xs) - x), h: Math.max(0.01, Math.max(...ys) - y),
    corners: pts.value.map((p) => [p.x, p.y] as [number, number]),
  })
}
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="640" scrollable @update:model-value="(v) => emit('update:modelValue', v)">
    <v-card>
      <div class="card-head">
        <div>
          <h2><v-icon icon="mdi-crop" size="18" />Crop to the card</h2>
          <p>Drag each corner onto a corner of the card; a magnifier shows the exact spot. A tilted card gets straightened.</p>
        </div>
        <v-btn icon="mdi-close" variant="text" size="small" aria-label="Close" @click="emit('update:modelValue', false)" />
      </div>
      <v-card-text class="d-flex justify-center">
        <div v-if="src" ref="stage" class="stage" @pointermove="move" @pointerup="up" @pointercancel="up">
          <img :src="src" alt="Photo to crop" draggable="false" />
          <svg class="overlay" viewBox="0 0 100 100" preserveAspectRatio="none" @pointerdown="down($event, 'all')">
            <path :d="dim" fill="rgba(0,0,0,0.55)" fill-rule="evenodd" />
            <polygon :points="outline" fill="transparent" stroke="#fff" stroke-width="0.5" vector-effect="non-scaling-stroke" style="stroke-width: 2px" />
          </svg>
          <span v-for="(p, i) in pts" :key="i" class="handle" role="slider" :aria-label="NAMES[i] + ' corner'"
            :style="{ left: p.x * 100 + '%', top: p.y * 100 + '%' }" @pointerdown.stop="down($event, i)" />
          <div v-if="loupe" class="loupe" :style="loupe" aria-hidden="true"><span class="cross-h" /><span class="cross-v" /></div>
        </div>
      </v-card-text>
      <v-divider />
      <div class="d-flex ga-2 pa-3 justify-end">
        <v-btn variant="text" class="mr-auto" @click="pts = fresh()">Reset</v-btn>
        <v-btn variant="text" @click="emit('update:modelValue', false)">Cancel</v-btn>
        <v-btn color="primary" variant="flat" prepend-icon="mdi-crop" :loading="busy" @click="apply">Crop and search again</v-btn>
      </div>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.stage { position: relative; display: inline-block; line-height: 0; touch-action: none; user-select: none; max-width: 100%; }
.stage img { max-width: 100%; max-height: 62vh; display: block; border-radius: 6px; }
.overlay { position: absolute; inset: 0; width: 100%; height: 100%; cursor: move; touch-action: none; }
.handle { position: absolute; width: 28px; height: 28px; margin: -14px 0 0 -14px; background: rgb(var(--v-theme-primary)); border: 2px solid #fff; border-radius: 50%; touch-action: none; cursor: grab; box-shadow: 0 1px 4px rgba(0, 0, 0, 0.4); }
.loupe { position: absolute; border-radius: 50%; border: 3px solid #fff; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.55); background-repeat: no-repeat; background-color: #000; pointer-events: none; overflow: hidden; }
.loupe span { position: absolute; background: rgb(var(--v-theme-primary)); }
.cross-h { left: 0; right: 0; top: 50%; height: 1px; }
.cross-v { top: 0; bottom: 0; left: 50%; width: 1px; }
</style>
