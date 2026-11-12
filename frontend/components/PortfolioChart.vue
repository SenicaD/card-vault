<script setup lang="ts">
// Total collection value over time. One series, one y-axis. Hover or touch for the exact value on a day.
const props = defineProps<{ points: { date: string; value: number }[] }>()

const wrap = ref<HTMLElement | null>(null)
const width = ref(640)
const hover = ref<number | null>(null)
let ro: ResizeObserver | undefined
onMounted(() => {
  if (!wrap.value) return
  width.value = wrap.value.clientWidth || 640
  ro = new ResizeObserver(() => (width.value = wrap.value?.clientWidth || width.value))
  ro.observe(wrap.value)
})
onBeforeUnmount(() => ro?.disconnect())

const H = computed(() => (width.value < 480 ? 200 : 260))
const M = { top: 12, right: 14, bottom: 26, left: 58 }
const pw = computed(() => Math.max(10, width.value - M.left - M.right))
const ph = computed(() => H.value - M.top - M.bottom)

const ys = computed(() => {
  const v = props.points.map((p) => p.value)
  let lo = Math.min(...v, 0), hi = Math.max(...v, 1)
  lo = lo === hi ? 0 : Math.min(lo, Math.min(...v) * 0.95)
  const raw = (hi - lo) / 4 || 1
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw
  const min = Math.floor(lo / step) * step, max = Math.ceil(hi / step) * step
  const ticks: number[] = []
  for (let t = min; t <= max + step / 2; t += step) ticks.push(t)
  return { min, max, ticks }
})
const X = (i: number) => M.left + (props.points.length > 1 ? (i / (props.points.length - 1)) * pw.value : pw.value / 2)
const Y = (v: number) => M.top + ph.value - ((v - ys.value.min) / (ys.value.max - ys.value.min || 1)) * ph.value
const line = computed(() => props.points.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(p.value).toFixed(1)}`).join(''))
const area = computed(() => props.points.length ? `${line.value}L${X(props.points.length - 1)},${M.top + ph.value}L${X(0)},${M.top + ph.value}Z` : '')
const fmt = (v: number) => (v >= 1000 ? '$' + +(v / 1000).toFixed(1) + 'k' : '$' + Math.round(v))
const xLabels = computed(() => {
  const n = props.points.length
  if (!n) return []
  const idx = [...new Set([0, Math.floor((n - 1) / 2), n - 1])]
  return idx.map((i) => ({ i, text: props.points[i].date }))
})
function move(e: PointerEvent) {
  const r = (e.currentTarget as SVGElement).getBoundingClientRect()
  const n = props.points.length
  if (n < 2) { hover.value = 0; return }
  hover.value = Math.min(n - 1, Math.max(0, Math.round(((e.clientX - r.left - M.left) / pw.value) * (n - 1))))
}
const tip = computed(() => (hover.value == null ? null : props.points[hover.value]))
const showTable = ref(false)
</script>

<template>
  <div ref="wrap" class="pc">
    <div v-if="points.length < 2" class="thin">Not enough price history yet. The chart fills in as cards are re-priced over the next days.</div>
    <svg v-else :width="width" :height="H" role="img" aria-label="Collection value over time" @pointermove="move" @pointerleave="hover = null">
      <g v-for="t in ys.ticks" :key="t">
        <line :x1="M.left" :x2="width - M.right" :y1="Y(t)" :y2="Y(t)" class="grid" />
        <text :x="M.left - 8" :y="Y(t) + 4" text-anchor="end" class="axis">{{ fmt(t) }}</text>
      </g>
      <text v-for="l in xLabels" :key="l.i" :x="X(l.i)" :y="H - 6" :text-anchor="l.i === 0 ? 'start' : l.i === points.length - 1 ? 'end' : 'middle'" class="axis">{{ l.text }}</text>
      <path :d="area" class="area" />
      <path :d="line" class="ln" />
      <g v-if="tip && hover != null">
        <line :x1="X(hover)" :x2="X(hover)" :y1="M.top" :y2="M.top + ph" class="cross" />
        <circle :cx="X(hover)" :cy="Y(tip.value)" r="5" class="dot" />
      </g>
    </svg>
    <div v-if="tip && hover != null" class="tip" :style="{ left: Math.min(Math.max(X(hover), 70), width - 70) + 'px' }">
      <div class="text-caption">{{ tip.date }}</div><strong class="num">{{ money(tip.value) }}</strong>
    </div>
    <v-btn v-if="points.length" size="x-small" variant="text" class="mt-1" @click="showTable = !showTable">{{ showTable ? 'Hide' : 'Show' }} data table</v-btn>
    <table v-if="showTable" class="dt"><thead><tr><th>Date</th><th>Value</th></tr></thead>
      <tbody><tr v-for="p in [...points].reverse()" :key="p.date"><td>{{ p.date }}</td><td class="num">{{ money(p.value) }}</td></tr></tbody></table>
  </div>
</template>

<style scoped>
.pc { position: relative; }
svg { display: block; touch-action: pan-y; }
.grid { stroke: rgb(var(--v-theme-border)); stroke-width: 1; }
.axis { font-size: 11px; fill: rgb(var(--v-theme-text-muted)); }
.ln { fill: none; stroke: rgb(var(--v-theme-primary)); stroke-width: 2; stroke-linejoin: round; }
.area { fill: rgb(var(--v-theme-primary)); opacity: 0.1; }
.cross { stroke: rgb(var(--v-theme-text-muted)); stroke-dasharray: 3 3; }
.dot { fill: rgb(var(--v-theme-primary)); stroke: rgb(var(--v-theme-surface)); stroke-width: 2; }
.tip { position: absolute; top: 0; transform: translateX(-50%); background: rgb(var(--v-theme-surface)); border: 1px solid rgb(var(--v-theme-border)); border-radius: 8px; padding: 4px 10px; pointer-events: none; box-shadow: 0 2px 8px rgba(0,0,0,.12); text-align: center; }
.thin { padding: 40px 12px; text-align: center; color: rgb(var(--v-theme-text-secondary)); font-size: 14px; }
.dt { width: 100%; font-size: 13px; margin-top: 6px; max-height: 240px; display: block; overflow: auto; }
.dt td, .dt th { padding: 3px 12px 3px 0; text-align: left; }
</style>
