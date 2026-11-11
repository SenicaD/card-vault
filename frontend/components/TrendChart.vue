<script setup lang="ts">
import type { TrendPoint } from '~/types'

// Asking vs sold price over time. One y-axis, two lines. Hand-rolled SVG: no chart dependency.
const props = defineProps<{ asking: TrendPoint[]; sold: TrendPoint[]; soldAvailable: boolean; loading?: boolean }>()

const wrap = ref<HTMLElement | null>(null)
const width = ref(640)
let ro: ResizeObserver | undefined
onMounted(() => {
  if (!wrap.value) return
  width.value = wrap.value.clientWidth || 640
  ro = new ResizeObserver(() => (width.value = wrap.value?.clientWidth || width.value))
  ro.observe(wrap.value)
})
onBeforeUnmount(() => ro?.disconnect())

const H = computed(() => (width.value < 480 ? 210 : 260))
const M = { top: 12, right: 14, bottom: 28, left: 54 }
const plotW = computed(() => Math.max(10, width.value - M.left - M.right))
const plotH = computed(() => H.value - M.top - M.bottom)

const day = (d: string) => Date.parse(d + 'T00:00:00Z')
const all = computed(() => [...props.asking, ...props.sold])
const xMin = computed(() => Math.min(...all.value.map((p) => day(p.date))))
const xMax = computed(() => Math.max(...all.value.map((p) => day(p.date))))

// Nice y ticks over both series, padded so lines never touch the frame.
const yScale = computed(() => {
  const v = all.value.map((p) => p.median)
  if (!v.length) return { min: 0, max: 1, ticks: [0, 1] }
  let lo = Math.min(...v), hi = Math.max(...v)
  if (lo === hi) { lo *= 0.9; hi = hi * 1.1 || 1 }
  const pad = (hi - lo) * 0.12
  lo = Math.max(0, lo - pad); hi += pad
  const raw = (hi - lo) / 4
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw
  const min = Math.floor(lo / step) * step, max = Math.ceil(hi / step) * step
  const ticks: number[] = []
  for (let t = min; t <= max + step / 2; t += step) ticks.push(+t.toFixed(4))
  return { min, max, ticks }
})

const sx = (d: string) => {
  if (xMax.value === xMin.value) return M.left + plotW.value / 2
  return M.left + ((day(d) - xMin.value) / (xMax.value - xMin.value)) * plotW.value
}
const sy = (v: number) => M.top + plotH.value - ((v - yScale.value.min) / (yScale.value.max - yScale.value.min)) * plotH.value

const path = (pts: TrendPoint[]) => pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p.date).toFixed(1)},${sy(p.median).toFixed(1)}`).join('')

const spanDays = computed(() => (xMax.value - xMin.value) / 86_400_000)
const fmtDate = (d: string, long = false) =>
  new Date(day(d)).toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', timeZone: 'UTC',
    ...(long || spanDays.value > 300 ? { year: 'numeric' } : {}),
  })
const xTicks = computed(() => {
  if (!all.value.length) return []
  const n = width.value < 480 ? 3 : 5
  if (xMax.value === xMin.value) return [{ x: sx(props.asking[0]?.date ?? props.sold[0].date), label: fmtDate((props.asking[0] ?? props.sold[0]).date) }]
  return Array.from({ length: n }, (_, i) => {
    const t = xMin.value + ((xMax.value - xMin.value) * i) / (n - 1)
    const d = new Date(t).toISOString().slice(0, 10)
    return { x: M.left + (plotW.value * i) / (n - 1), label: fmtDate(d) }
  })
})
const yLabel = (v: number) => (yScale.value.max - yScale.value.min > 20 ? '$' + Math.round(v) : '$' + v.toFixed(2))

// Hover / touch: snap to the nearest date that has data.
const dates = computed(() => [...new Set(all.value.map((p) => p.date))].sort())
const hover = ref<string | null>(null)
function onMove(e: PointerEvent) {
  const box = (e.currentTarget as SVGElement).getBoundingClientRect()
  const px = e.clientX - box.left
  let best = dates.value[0], bd = Infinity
  for (const d of dates.value) { const dist = Math.abs(sx(d) - px); if (dist < bd) { bd = dist; best = d } }
  hover.value = best ?? null
}
const at = (pts: TrendPoint[], d: string | null) => pts.find((p) => p.date === d)
const hAsk = computed(() => at(props.asking, hover.value))
const hSold = computed(() => at(props.sold, hover.value))
const tipLeft = computed(() => {
  if (!hover.value) return 0
  const x = sx(hover.value)
  return Math.min(Math.max(x - 80, 4), width.value - 164)
})

const describe = computed(() => {
  const a = props.asking
  if (!a.length) return 'No asking-price history yet.'
  return `Asking price from ${money(a[0].median)} on ${fmtDate(a[0].date, true)} to ${money(a[a.length - 1].median)} on ${fmtDate(a[a.length - 1].date, true)}.`
})
const rows = computed(() => dates.value.map((d) => ({ d, a: at(props.asking, d), s: at(props.sold, d) })))
</script>

<template>
  <div class="trend">
    <div class="legend" aria-hidden="false">
      <span class="key"><svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" class="ln ask" /></svg>Asking <span class="muted">(active listings)</span></span>
      <span class="key" :class="{ off: !soldAvailable }">
        <svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" class="ln sold" stroke-dasharray="5 3" /></svg>Sold
        <span class="muted">{{ soldAvailable ? '(completed sales)' : '(needs eBay Marketplace Insights access)' }}</span>
      </span>
    </div>

    <div ref="wrap" class="plot">
      <div v-if="loading && !all.length" class="state">Loading…</div>
      <div v-else-if="!all.length" class="state">
        No price history in this range yet. Cards are re-priced daily, so the line builds up over time.
      </div>
      <template v-else>
        <svg :width="width" :height="H" role="img" :aria-label="describe" @pointermove="onMove" @pointerdown="onMove" @pointerleave="hover = null">
          <g class="grid">
            <template v-for="t in yScale.ticks" :key="t">
              <line :x1="M.left" :x2="width - M.right" :y1="sy(t)" :y2="sy(t)" />
              <text :x="M.left - 8" :y="sy(t)" class="axis" text-anchor="end" dominant-baseline="middle">{{ yLabel(t) }}</text>
            </template>
          </g>
          <g>
            <text v-for="t in xTicks" :key="t.label + t.x" :x="t.x" :y="H - 8" class="axis" :text-anchor="t.x < M.left + 20 ? 'start' : t.x > width - 40 ? 'end' : 'middle'">{{ t.label }}</text>
          </g>

          <path v-if="sold.length > 1" :d="path(sold)" class="ln sold" stroke-dasharray="6 4" fill="none" />
          <path v-if="asking.length > 1" :d="path(asking)" class="ln ask" fill="none" />
          <!-- markers: only while sparse, so a long history stays a clean line -->
          <template v-if="asking.length <= 40">
            <circle v-for="p in asking" :key="'a' + p.date" :cx="sx(p.date)" :cy="sy(p.median)" r="4" class="dot ask" />
          </template>
          <template v-if="sold.length <= 40">
            <circle v-for="p in sold" :key="'s' + p.date" :cx="sx(p.date)" :cy="sy(p.median)" r="4" class="dot sold" />
          </template>

          <g v-if="hover">
            <line :x1="sx(hover)" :x2="sx(hover)" :y1="M.top" :y2="M.top + plotH" class="cross" />
            <circle v-if="hAsk" :cx="sx(hover)" :cy="sy(hAsk.median)" r="5" class="dot ask hot" />
            <circle v-if="hSold" :cx="sx(hover)" :cy="sy(hSold.median)" r="5" class="dot sold hot" />
          </g>
        </svg>

        <div v-if="hover" class="tip" :style="{ left: tipLeft + 'px' }">
          <div class="tip-date">{{ fmtDate(hover, true) }}</div>
          <div class="tip-row"><span class="sw ask" />Asking <strong class="num">{{ hAsk ? money(hAsk.median) : '—' }}</strong></div>
          <div v-if="hAsk" class="tip-sub num">{{ money(hAsk.low) }} – {{ money(hAsk.high) }} · {{ hAsk.samples }} listings</div>
          <div v-if="soldAvailable" class="tip-row"><span class="sw sold" />Sold <strong class="num">{{ hSold ? money(hSold.median) : '—' }}</strong></div>
        </div>
      </template>
    </div>

    <p v-if="all.length === 1" class="muted hint">Only one day of history so far. Cards are re-priced daily, so a line appears after the next refresh.</p>

    <details v-if="all.length" class="tbl">
      <summary>View as table</summary>
      <table>
        <thead><tr><th>Date (UTC)</th><th class="r">Asking</th><th class="r">Sold</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.d">
            <td class="num">{{ r.d }}</td>
            <td class="r num">{{ r.a ? money(r.a.median) : '—' }}</td>
            <td class="r num">{{ r.s ? money(r.s.median) : '—' }}</td>
          </tr>
        </tbody>
      </table>
    </details>
  </div>
</template>

<style scoped>
.trend { --ask: #2557d6; --sold: #b7770f; --surface: #ffffff; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 18px; font-size: 13px; margin-bottom: 8px; color: rgb(var(--v-theme-text-secondary)); }
.key { display: inline-flex; align-items: center; gap: 8px; }
.key.off { opacity: 0.75; }
.muted { color: rgb(var(--v-theme-text-muted)); font-size: 12px; }
.plot { position: relative; width: 100%; min-height: 120px; touch-action: pan-y; }
.state { display: grid; place-items: center; text-align: center; min-height: 140px; padding: 0 16px; font-size: 14px; color: rgb(var(--v-theme-text-secondary)); }
.grid line { stroke: rgb(var(--v-theme-border)); stroke-width: 1; }
.axis { font-size: 11.5px; fill: rgb(var(--v-theme-text-muted)); font-variant-numeric: tabular-nums; }
.ln { stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.ln.ask { stroke: var(--ask); }
.ln.sold { stroke: var(--sold); }
.dot { stroke: var(--surface); stroke-width: 2; }
.dot.ask { fill: var(--ask); }
.dot.sold { fill: var(--sold); }
.dot.hot { stroke-width: 2.5; }
.cross { stroke: rgb(var(--v-theme-text-muted)); stroke-width: 1; stroke-dasharray: 3 3; }
.tip { position: absolute; top: 4px; width: 160px; pointer-events: none; padding: 8px 10px; border-radius: 8px; font-size: 12.5px;
  background: rgb(var(--v-theme-surface)); border: 1px solid rgb(var(--v-theme-border)); box-shadow: 0 4px 14px rgba(20, 30, 60, 0.12); }
.tip-date { font-weight: 600; margin-bottom: 4px; }
.tip-row { display: flex; align-items: center; gap: 6px; }
.tip-row strong { margin-left: auto; }
.tip-sub { color: rgb(var(--v-theme-text-muted)); font-size: 11.5px; margin: 0 0 4px 16px; }
.sw { width: 10px; height: 10px; border-radius: 3px; flex-shrink: 0; }
.sw.ask { background: var(--ask); }
.sw.sold { background: var(--sold); }
.hint { margin: 8px 0 0; }
.tbl { margin-top: 10px; font-size: 13px; }
.tbl summary { cursor: pointer; color: rgb(var(--v-theme-text-secondary)); }
.tbl table { width: 100%; border-collapse: collapse; margin-top: 6px; }
.tbl th, .tbl td { padding: 5px 8px; border-bottom: 1px solid rgb(var(--v-theme-border)); text-align: left; font-weight: 500; }
.tbl th { font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.04em; color: rgb(var(--v-theme-text-muted)); }
.tbl .r { text-align: right; }
</style>

<!-- Unscoped on purpose: scoped :global() drops the .trend half of the selector. -->
<style>
.v-theme--dark .trend { --ask: #5b86ff; --sold: #c0831f; --surface: #171a1f; }
</style>
