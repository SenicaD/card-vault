<script setup lang="ts">
import type { TraceFunnel, TraceListing } from '~/types'

// Every listing for one grade as a dot on a price line: counted ones solid, dropped ones hollow.
// Shows the median and the outlier fences, so "why is the price what it is" is visible at a glance.
const props = defineProps<{ listings: TraceListing[]; funnel: TraceFunnel | null }>()

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

const M = { l: 16, r: 16, t: 26, b: 26 }
const H = 150
const prices = computed(() => props.listings.map((l) => l.p))
const dom = computed(() => {
  const lo = Math.min(...prices.value), hi = Math.max(...prices.value)
  const pad = (hi - lo) * 0.04 || 1
  return { lo: Math.max(0, lo - pad), hi: hi + pad }
})
const X = (v: number) => M.l + ((Math.min(dom.value.hi, Math.max(dom.value.lo, v)) - dom.value.lo) / (dom.value.hi - dom.value.lo || 1)) * (width.value - M.l - M.r)
// A fence can lie beyond the listings (or below $0); the line is drawn at the edge, the label keeps the real value.
const anchor = (x: number) => (x < 90 ? 'start' : x > width.value - 90 ? 'end' : 'middle')

// Stack dots that would overlap: each takes the first free row.
const dots = computed(() => {
  const rows: number[][] = []
  return [...props.listings].sort((a, b) => a.p - b.p).map((l) => {
    const x = X(l.p)
    let r = rows.findIndex((xs) => xs.every((o) => Math.abs(o - x) > 9))
    if (r < 0) { rows.push([]); r = rows.length - 1 }
    rows[r].push(x)
    return { l, x, r }
  })
})
const y = (r: number) => H - M.b - 10 - Math.min(r, 6) * 11
const dotClass = (d: string) => (d === 'used' ? 'used' : d === 'outlier' ? 'out' : d === 'excluded' ? 'excl' : 'other')
const ticks = computed(() => {
  const n = 4
  return Array.from({ length: n + 1 }, (_, i) => dom.value.lo + ((dom.value.hi - dom.value.lo) * i) / n)
})
const med = computed(() => props.funnel?.median ?? null)
</script>

<template>
  <div ref="wrap">
    <svg :width="width" :height="H" role="img" aria-label="Listing prices for this grade">
      <rect v-if="funnel?.fences" :x="X(funnel.fences.lo)" :y="M.t" :width="Math.max(0, X(funnel.fences.hi) - X(funnel.fences.lo))" :height="H - M.t - M.b" class="band" />
      <template v-if="funnel?.fences">
        <line :x1="X(funnel.fences.lo)" :x2="X(funnel.fences.lo)" :y1="M.t" :y2="H - M.b" class="fence" />
        <line :x1="X(funnel.fences.hi)" :x2="X(funnel.fences.hi)" :y1="M.t" :y2="H - M.b" class="fence" />
        <text :x="X(funnel.fences.lo)" :y="M.t - 8" :text-anchor="anchor(X(funnel.fences.lo))" class="lab">kept from {{ money(Math.max(0, funnel.fences.lo)) }}</text>
        <text :x="X(funnel.fences.hi)" :y="M.t - 8" :text-anchor="anchor(X(funnel.fences.hi))" class="lab">kept up to {{ money(funnel.fences.hi) }}</text>
      </template>
      <line v-if="med != null" :x1="X(med)" :x2="X(med)" :y1="M.t" :y2="H - M.b" class="median" />
      <text v-if="med != null" :x="X(med)" :y="H - 6" :text-anchor="anchor(X(med))" class="lab strong">median {{ money(med) }}</text>
      <line :x1="M.l" :x2="width - M.r" :y1="H - M.b" :y2="H - M.b" class="axis" />
      <text v-for="(t, i) in ticks" :key="i" :x="X(t)" :y="H - M.b + 12" :text-anchor="i === 0 ? 'start' : i === ticks.length - 1 ? 'end' : 'middle'" class="lab">{{ money(Math.round(t)) }}</text>
      <circle v-for="d in dots" :key="d.l.id" :cx="d.x" :cy="y(d.r)" r="4.5" :class="['dot', dotClass(d.l.d), { ref: d.l.r }]">
        <title>{{ money(d.l.p) }} · {{ DECISION_LABEL[d.l.d] }}{{ d.l.r ? ` (${d.l.r} listing)` : '' }} · {{ d.l.t }}</title>
      </circle>
    </svg>
    <div class="legend">
      <span><i class="sw used" />Counted</span><span><i class="sw out" />Price outlier</span>
      <span><i class="sw excl" />You unticked it</span><span><i class="sw other" />Different card or version</span>
      <span v-if="funnel?.fences"><i class="sw band" />Range kept (1.5× the middle half)</span>
    </div>
  </div>
</template>

<style scoped>
svg { display: block; }
.axis { stroke: rgb(var(--v-theme-border)); }
.lab { font-size: 10.5px; fill: rgb(var(--v-theme-text-muted)); }
.lab.strong { fill: rgb(var(--v-theme-text-primary, var(--v-theme-on-surface))); font-weight: 600; }
.band { fill: rgb(var(--v-theme-primary)); opacity: 0.07; }
.fence { stroke: rgb(var(--v-theme-text-muted)); stroke-dasharray: 3 3; }
.median { stroke: rgb(var(--v-theme-primary)); stroke-width: 2; }
.dot.used { fill: rgb(var(--v-theme-primary)); stroke: rgb(var(--v-theme-surface)); stroke-width: 1.5; }
.dot.out { fill: none; stroke: rgb(var(--v-theme-error)); stroke-width: 2; }
.dot.excl { fill: none; stroke: rgb(var(--v-theme-text-muted)); stroke-width: 2; stroke-dasharray: 2 2; }
.dot.other { fill: none; stroke: rgb(var(--v-theme-warning)); stroke-width: 2; }
.dot.ref { stroke-width: 3; r: 6; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: rgb(var(--v-theme-text-secondary)); margin-top: 2px; }
.sw { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; vertical-align: -1px; box-sizing: border-box; }
.sw.used { background: rgb(var(--v-theme-primary)); } .sw.out { border: 2px solid rgb(var(--v-theme-error)); }
.sw.excl { border: 2px dashed rgb(var(--v-theme-text-muted)); } .sw.other { border: 2px solid rgb(var(--v-theme-warning)); }
.sw.band { background: rgb(var(--v-theme-primary)); opacity: 0.2; border-radius: 2px; }
</style>
