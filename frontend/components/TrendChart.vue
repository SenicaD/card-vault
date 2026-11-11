<script setup lang="ts">
import type { SalePoint, TrendPoint, TrendSeries } from '~/types'

// Asking vs sold price over time, one colour per grade: solid = asking, dashed = sold.
// One y-axis. Hand-rolled SVG, so there is no chart dependency.
const props = defineProps<{
  asking: TrendSeries[]
  sold: TrendSeries[]
  soldSource: 'sold' | 'estimated' | null // where the dashed line comes from
  sales?: SalePoint[] // sales you recorded yourself
  slots: Record<string, number> // grade -> colour slot 0-3; colour follows the grade, never its position
  loading?: boolean
}>()

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

const slot = (g: string) => props.slots[g] ?? 0
const day = (d: string) => Date.parse(d + 'T00:00:00Z')
const salesList = computed(() => props.sales ?? [])
const saleAsPoint = (s: SalePoint): TrendPoint => ({ date: s.date, median: s.price, low: null, high: null, samples: 1 })
// the axes have to make room for your own sales as well as the market lines
const allPoints = computed<TrendPoint[]>(() => [...props.asking, ...props.sold].flatMap((s) => s.points).concat(salesList.value.map(saleAsPoint)))
const marketPoints = computed(() => [...props.asking, ...props.sold].flatMap((s) => s.points))
const soldLabel = computed(() => (props.soldSource === 'sold' ? 'Sold' : props.soldSource === 'estimated' ? 'Likely sold (estimate)' : 'Sold'))
const saleD = (s: SalePoint) => { const x = sx(s.date), y = sy(s.price); return `M${x},${y - 7}L${x + 7},${y}L${x},${y + 7}L${x - 7},${y}Z` }
const xMin = computed(() => Math.min(...allPoints.value.map((p) => day(p.date))))
const xMax = computed(() => Math.max(...allPoints.value.map((p) => day(p.date))))

// Nice y ticks across every series, padded so lines never touch the frame.
const yScale = computed(() => {
  const v = allPoints.value.map((p) => p.median)
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
  if (!allPoints.value.length) return []
  const n = width.value < 480 ? 3 : 5
  if (xMax.value === xMin.value) return [{ x: sx(allPoints.value[0].date), label: fmtDate(allPoints.value[0].date) }]
  return Array.from({ length: n }, (_, i) => {
    const t = xMin.value + ((xMax.value - xMin.value) * i) / (n - 1)
    return { x: M.left + (plotW.value * i) / (n - 1), label: fmtDate(new Date(t).toISOString().slice(0, 10)) }
  })
})
const yLabel = (v: number) => (yScale.value.max - yScale.value.min > 20 ? '$' + Math.round(v) : '$' + v.toFixed(2))

const LOW = 5 // fewer listings than this makes a point provisional
const grades = computed(() => props.asking.map((s) => s.grade))
const anyLow = computed(() => marketPoints.value.some((p) => p.samples < LOW))
const lines = computed(() => [
  ...props.sold.filter((s) => s.points.length).map((s) => ({ key: 'sold-' + s.grade, grade: s.grade, kind: 'sold' as const, points: s.points })),
  ...props.asking.filter((s) => s.points.length).map((s) => ({ key: 'ask-' + s.grade, grade: s.grade, kind: 'ask' as const, points: s.points })),
])

// Hover / touch: snap to the nearest date that has data.
const dates = computed(() => [...new Set(allPoints.value.map((p) => p.date))].sort())
const hover = ref<string | null>(null)
function onMove(e: PointerEvent) {
  const px = e.clientX - (e.currentTarget as SVGElement).getBoundingClientRect().left
  let best = dates.value[0], bd = Infinity
  for (const d of dates.value) { const dist = Math.abs(sx(d) - px); if (dist < bd) { bd = dist; best = d } }
  hover.value = best ?? null
}
const at = (series: TrendSeries[], g: string, d: string | null) => series.find((s) => s.grade === g)?.points.find((p) => p.date === d)
const hoverSales = computed(() => salesList.value.filter((s) => s.date === hover.value))
const hoverRows = computed(() => grades.value.map((g) => ({ g, a: at(props.asking, g, hover.value), s: at(props.sold, g, hover.value) })))
const tipLeft = computed(() => (hover.value ? Math.min(Math.max(sx(hover.value) - 90, 4), width.value - 184) : 0))

const describe = computed(() => {
  const parts = props.asking.filter((s) => s.points.length).map((s) => {
    const a = s.points[0], b = s.points[s.points.length - 1]
    return `${s.grade} asking ${money(a.median)} on ${fmtDate(a.date, true)} to ${money(b.median)} on ${fmtDate(b.date, true)}`
  })
  return parts.length ? parts.join('. ') + '.' : 'No asking-price history yet.'
})
const rows = computed(() => dates.value.map((d) => ({ d, cells: grades.value.map((g) => ({ a: at(props.asking, g, d), s: at(props.sold, g, d) })) })))
const noData = computed(() => grades.value.filter((g) => !props.asking.find((s) => s.grade === g)?.points.length))
</script>

<template>
  <div class="trend">
    <div class="legend">
      <span v-for="g in grades" :key="g" class="key">
        <svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" :class="['ln', 's' + slot(g)]" /></svg>{{ g }}
      </span>
      <span class="key style">
        <svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" class="ln plain" /></svg>Asking
        <svg width="22" height="8" aria-hidden="true" class="ml"><line x1="0" y1="4" x2="22" y2="4" class="ln plain" stroke-dasharray="5 3" /></svg>{{ soldLabel }}
        <span v-if="!soldSource" class="muted">(fills in as listings disappear)</span>
      </span>
      <span v-if="salesList.length" class="key style"><svg width="14" height="14" aria-hidden="true"><path d="M7,1L13,7L7,13L1,7Z" class="plain-diamond" /></svg>Your sale</span>
      <span v-if="anyLow" class="key style"><svg width="12" height="12" aria-hidden="true"><circle cx="6" cy="6" r="4" class="dot lowc plain" /></svg>Fewer than {{ LOW }} listings</span>
    </div>

    <div ref="wrap" class="plot">
      <div v-if="loading && !allPoints.length" class="state">Loading…</div>
      <div v-else-if="!allPoints.length" class="state">
        No price history for {{ grades.length > 1 ? 'these grades' : (grades[0] || 'this grade') }} in this range yet. Cards are re-priced daily, so the line builds up over time.
      </div>
      <template v-else>
        <svg :width="width" :height="H" role="img" :aria-label="describe" @pointermove="onMove" @pointerdown="onMove" @pointerleave="hover = null">
          <g class="grid">
            <template v-for="t in yScale.ticks" :key="t">
              <line :x1="M.left" :x2="width - M.right" :y1="sy(t)" :y2="sy(t)" />
              <text :x="M.left - 8" :y="sy(t)" class="axis" text-anchor="end" dominant-baseline="middle">{{ yLabel(t) }}</text>
            </template>
          </g>
          <text v-for="t in xTicks" :key="t.label + t.x" :x="t.x" :y="H - 8" class="axis"
            :text-anchor="t.x < M.left + 20 ? 'start' : t.x > width - 40 ? 'end' : 'middle'">{{ t.label }}</text>

          <template v-for="l in lines" :key="l.key">
            <path v-if="l.points.length > 1" :d="path(l.points)" :class="['ln', 's' + slot(l.grade)]" :stroke-dasharray="l.kind === 'sold' ? '6 4' : undefined" fill="none" />
            <!-- markers only while sparse, so a long history stays a clean line -->
            <template v-if="l.points.length <= 40">
              <circle v-for="p in l.points" :key="p.date" :cx="sx(p.date)" :cy="sy(p.median)" r="4" :class="['dot', 'f' + slot(l.grade), { lowc: p.samples < LOW }]" />
            </template>
          </template>

          <path v-for="s in salesList" :key="'sale' + s.id" :d="saleD(s)" :class="['diamond', 'f' + slot(s.grade ?? grades[0] ?? '')]" />

          <g v-if="hover">
            <line :x1="sx(hover)" :x2="sx(hover)" :y1="M.top" :y2="M.top + plotH" class="cross" />
            <template v-for="r in hoverRows" :key="r.g">
              <circle v-if="r.a" :cx="sx(hover)" :cy="sy(r.a.median)" r="5" :class="['dot hot', 'f' + slot(r.g)]" />
              <circle v-if="r.s" :cx="sx(hover)" :cy="sy(r.s.median)" r="5" :class="['dot hot', 'f' + slot(r.g)]" />
            </template>
          </g>
        </svg>

        <div v-if="hover" class="tip" :style="{ left: tipLeft + 'px' }">
          <div class="tip-date">{{ fmtDate(hover, true) }}</div>
          <template v-for="r in hoverRows" :key="r.g">
            <div class="tip-row"><span :class="['sw', 'f' + slot(r.g)]" />{{ r.g }} <strong class="num">{{ r.a ? money(r.a.median) : '—' }}</strong></div>
            <div v-if="r.a" class="tip-sub num">{{ money(r.a.low) }} – {{ money(r.a.high) }} · {{ r.a.samples }} {{ r.a.samples === 1 ? 'listing' : 'listings' }}<template v-if="r.a.samples < LOW"> · low confidence</template></div>
            <div v-if="soldSource && r.s" class="tip-sub num">{{ soldLabel }} {{ money(r.s.median) }}<template v-if="soldSource === 'estimated'"> · {{ r.s.samples }} {{ r.s.samples === 1 ? 'listing' : 'listings' }}</template></div>
          </template>
          <div v-for="s in hoverSales" :key="'ts' + s.id" class="tip-row"><span class="sw diamond-sw" />Your sale{{ s.grade ? ' · ' + s.grade : '' }} <strong class="num">{{ money(s.price) }}</strong></div>
        </div>
      </template>
    </div>

    <p v-if="allPoints.length && noData.length" class="muted hint">No history yet for {{ noData.join(', ') }}. It appears after the next lookup or daily refresh.</p>
    <p v-else-if="dates.length === 1" class="muted hint">Only one day of history so far. Cards are re-priced daily, so a line appears after the next refresh.</p>

    <details v-if="allPoints.length" class="tbl">
      <summary>View as table</summary>
      <div class="tbl-scroll">
        <table>
          <thead>
            <tr><th>Date (UTC)</th><th v-for="g in grades" :key="g" class="r">{{ g }}</th><th v-if="salesList.length" class="r">Your sale</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.d">
              <td class="num">{{ r.d }}</td>
              <td v-for="(c, i) in r.cells" :key="i" class="r num">
                {{ c.a ? money(c.a.median) : '—' }}<span v-if="soldSource && c.s" class="muted"> · {{ soldSource === 'estimated' ? 'likely sold' : 'sold' }} {{ money(c.s.median) }}</span>
              </td>
              <td v-if="salesList.length" class="r num">{{ salesList.filter((s) => s.date === r.d).map((s) => money(s.price)).join(', ') || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>
  </div>
</template>

<style scoped>
.trend { --s0: #2a78d6; --s1: #eb6834; --s2: #1baf7a; --s3: #eda100; --surface: #ffffff; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 13px; margin-bottom: 8px; color: rgb(var(--v-theme-text-secondary)); }
.key { display: inline-flex; align-items: center; gap: 7px; }
.key.style { color: rgb(var(--v-theme-text-muted)); font-size: 12px; }
.key .ml { margin-left: 8px; }
.muted { color: rgb(var(--v-theme-text-muted)); font-size: 12px; }
.hint { margin: 8px 0 0; }
.plot { position: relative; width: 100%; min-height: 120px; touch-action: pan-y; }
.state { display: grid; place-items: center; text-align: center; min-height: 140px; padding: 0 16px; font-size: 14px; color: rgb(var(--v-theme-text-secondary)); }
.grid line { stroke: rgb(var(--v-theme-border)); stroke-width: 1; }
.axis { font-size: 11.5px; fill: rgb(var(--v-theme-text-muted)); font-variant-numeric: tabular-nums; }
.ln { stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.ln.plain { stroke: rgb(var(--v-theme-text-muted)); }
.ln.s0 { stroke: var(--s0); } .ln.s1 { stroke: var(--s1); } .ln.s2 { stroke: var(--s2); } .ln.s3 { stroke: var(--s3); }
.dot { stroke: var(--surface); stroke-width: 2; }
.dot.hot { stroke-width: 2.5; }
.diamond { stroke: var(--surface); stroke-width: 2; }
.plain-diamond { fill: rgb(var(--v-theme-text-muted)); }
.sw.diamond-sw { background: rgb(var(--v-theme-text-muted)); transform: rotate(45deg) scale(0.8); border-radius: 1px; }
.dot.lowc { fill: var(--surface); stroke-width: 2; }
.dot.lowc.f0 { stroke: var(--s0); } .dot.lowc.f1 { stroke: var(--s1); } .dot.lowc.f2 { stroke: var(--s2); } .dot.lowc.f3 { stroke: var(--s3); }
.dot.lowc.plain { stroke: rgb(var(--v-theme-text-muted)); }
.f0 { fill: var(--s0); } .f1 { fill: var(--s1); } .f2 { fill: var(--s2); } .f3 { fill: var(--s3); }
.cross { stroke: rgb(var(--v-theme-text-muted)); stroke-width: 1; stroke-dasharray: 3 3; }
.tip { position: absolute; top: 4px; width: 180px; pointer-events: none; padding: 8px 10px; border-radius: 8px; font-size: 12.5px;
  background: rgb(var(--v-theme-surface)); border: 1px solid rgb(var(--v-theme-border)); box-shadow: 0 4px 14px rgba(20, 30, 60, 0.12); }
.tip-date { font-weight: 600; margin-bottom: 4px; }
.tip-row { display: flex; align-items: center; gap: 6px; }
.tip-row strong { margin-left: auto; }
.tip-sub { color: rgb(var(--v-theme-text-muted)); font-size: 11.5px; margin: 0 0 4px 16px; }
.sw { width: 10px; height: 10px; border-radius: 3px; flex-shrink: 0; }
.tbl { margin-top: 10px; font-size: 13px; }
.tbl summary { cursor: pointer; color: rgb(var(--v-theme-text-secondary)); }
.tbl-scroll { overflow-x: auto; }
.tbl table { width: 100%; border-collapse: collapse; margin-top: 6px; }
.tbl th, .tbl td { padding: 5px 8px; border-bottom: 1px solid rgb(var(--v-theme-border)); text-align: left; font-weight: 500; white-space: nowrap; }
.tbl th { font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.04em; color: rgb(var(--v-theme-text-muted)); }
.tbl .r { text-align: right; }
</style>

<!-- Unscoped on purpose: scoped :global() drops the .trend half of the selector. -->
<style>
.v-theme--dark .trend { --s0: #3987e5; --s1: #d95926; --s2: #199e70; --s3: #c98500; --surface: #171a1f; }
</style>
