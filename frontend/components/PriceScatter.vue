<script setup lang="ts">
import type { PricePoints } from '~/types'

// Every price on record for one grade on a single chart: each sold sale as a dot, each asking listing as a
// bar from when it was first seen to when it was last seen, and the median line through each.
const props = defineProps<{ data: PricePoints | null }>()

const show = reactive({ sold: true, asking: true, lines: true })
const wrap = ref<HTMLElement | null>(null)
const width = ref(700)
let ro: ResizeObserver | undefined
onMounted(() => {
  if (!wrap.value) return
  width.value = wrap.value.clientWidth || 700
  ro = new ResizeObserver(() => (width.value = wrap.value?.clientWidth || width.value))
  ro.observe(wrap.value)
})
onBeforeUnmount(() => ro?.disconnect())

const H = computed(() => (width.value < 520 ? 260 : 340))
const M = { t: 12, r: 14, b: 28, l: 56 }
const day = (d: string) => Date.parse(d + 'T00:00:00Z')

const empty = computed(() => !props.data || (!props.data.sales.length && !props.data.listings.length && !props.data.asking_line.length))
// The scale follows only what is switched on, so hiding the one expensive series zooms in on the rest.
const visible = computed(() => {
  const d = props.data
  const t: number[] = [], v: number[] = []
  if (!d) return { t, v }
  if (show.sold) d.sales.forEach((x) => { t.push(day(x.date)); v.push(x.price) })
  if (show.asking) d.listings.forEach((l) => { t.push(day(l.first), day(l.last)); v.push(l.price) })
  if (show.lines) {
    d.asking_line.forEach((p) => { t.push(day(p.date)); v.push(p.median, p.low ?? p.median, p.high ?? p.median) })
    d.sold_line.forEach((p) => { t.push(day(p.date)); v.push(p.median) })
  }
  return { t, v }
})
const nothingOn = computed(() => !visible.value.v.length)
const target = computed(() => {
  const { t, v } = visible.value
  if (!v.length) return null
  let tl = Math.min(...t), th = Math.max(...t)
  if (th === tl) th = tl + 86400000
  let lo = Math.min(...v), hi = Math.max(...v)
  const pad = (hi - lo) * 0.08 || hi * 0.1 || 1
  lo = Math.max(0, lo - pad); hi += pad
  const raw = (hi - lo) / 4
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw) || raw
  const min = Math.floor(lo / step) * step, max = Math.ceil(hi / step) * step
  const ticks: number[] = []
  for (let k = min; k <= max + step / 2; k += step) ticks.push(+k.toFixed(4))
  return { tl, th, min, max, ticks }
})

// The drawn scale glides to the new one instead of jumping (skipped when the viewer prefers less motion).
const view = reactive({ tl: 0, th: 1, min: 0, max: 1 })
let raf = 0
watch(target, (to) => {
  if (!to) return
  cancelAnimationFrame(raf)
  const reduce = typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches
  const fresh = view.th - view.tl <= 1 && view.max <= 1  // first draw: nothing to glide from
  if (reduce || fresh) { Object.assign(view, { tl: to.tl, th: to.th, min: to.min, max: to.max }); return }
  const from = { ...view }, t0 = performance.now(), ms = 320
  const step = (now: number) => {
    const k = Math.min(1, (now - t0) / ms), e = 1 - (1 - k) ** 3
    for (const key of ['tl', 'th', 'min', 'max'] as const) view[key] = from[key] + (to[key] - from[key]) * e
    if (k < 1) raf = requestAnimationFrame(step)
  }
  raf = requestAnimationFrame(step)
}, { immediate: true })
onBeforeUnmount(() => cancelAnimationFrame(raf))
const ticks = computed(() => target.value?.ticks ?? [])
const pw = computed(() => Math.max(10, width.value - M.l - M.r))
const ph = computed(() => H.value - M.t - M.b)
const X = (d: string) => M.l + ((day(d) - view.tl) / (view.th - view.tl || 1)) * pw.value
const Y = (v: number) => M.t + ph.value - ((v - view.min) / (view.max - view.min || 1)) * ph.value
const fmt = (v: number) => (v >= 1000 ? '$' + +(v / 1000).toFixed(1) + 'k' : '$' + Math.round(v))
const path = (pts: { date: string; median: number }[]) => pts.map((p, i) => `${i ? 'L' : 'M'}${X(p.date).toFixed(1)},${Y(p.median).toFixed(1)}`).join('')
const band = computed(() => {
  const a = props.data?.asking_line.filter((p) => p.low != null && p.high != null) ?? []
  if (a.length < 2) return ''
  return a.map((p, i) => `${i ? 'L' : 'M'}${X(p.date)},${Y(p.high!)}`).join('') + [...a].reverse().map((p) => `L${X(p.date)},${Y(p.low!)}`).join('') + 'Z'
})
const xTicks = computed(() => {
  const n = 4
  return Array.from({ length: n + 1 }, (_, i) => new Date(view.tl + ((view.th - view.tl) * i) / n).toISOString().slice(0, 10))
})
const gap = computed(() => {
  const d = props.data
  return d && d.asking_now && d.sold_median != null ? Math.round(((d.sold_median - d.asking_now) / d.asking_now) * 100) : null
})
</script>

<template>
  <div ref="wrap">
    <div v-if="data" class="sum">
      <div><span class="k"><i class="sw ask" />Asking now</span><strong class="num">{{ money(data.asking_now) }}</strong><span class="text-caption text-muted-c"> ({{ data.asking_count }} listings)</span></div>
      <div><span class="k"><i class="sw sold" />Sold median</span><strong class="num">{{ money(data.sold_median) }}</strong><span class="text-caption text-muted-c"> ({{ data.sold_count }} sales)</span></div>
      <div v-if="gap != null"><span class="k">Sold vs asking</span><strong class="num" :class="gap < 0 ? 'text-error' : 'text-success'">{{ gap > 0 ? '+' : '' }}{{ gap }}%</strong></div>
    </div>
    <div class="chips">
      <v-chip size="small" :variant="show.sold ? 'flat' : 'outlined'" color="warning" @click="show.sold = !show.sold"><i class="sw sold mr-1" />Sold sales</v-chip>
      <v-chip size="small" :variant="show.asking ? 'flat' : 'outlined'" color="primary" @click="show.asking = !show.asking"><i class="sw ask mr-1" />Asking listings</v-chip>
      <v-chip size="small" :variant="show.lines ? 'flat' : 'outlined'" @click="show.lines = !show.lines">Median lines</v-chip>
    </div>
    <p v-if="empty" class="thin">Nothing on record for this grade and range yet. Look up the price, and press "Check sold prices" to add real sales.</p>
    <p v-else-if="nothingOn" class="thin">Everything is switched off. Turn a series back on above.</p>
    <svg v-else :width="width" :height="H" role="img" aria-label="Asking and sold prices over time">
      <g v-for="t in ticks" :key="t">
        <line :x1="M.l" :x2="width - M.r" :y1="Y(t)" :y2="Y(t)" class="grid" />
        <text :x="M.l - 8" :y="Y(t) + 4" text-anchor="end" class="axis">{{ fmt(t) }}</text>
      </g>
      <text v-for="(d, i) in xTicks" :key="d" :x="M.l + (pw * i) / 4" :y="H - 8" :text-anchor="i === 0 ? 'start' : i === 4 ? 'end' : 'middle'" class="axis">{{ d.slice(5) }}</text>
      <path v-if="show.lines && band" :d="band" class="band" />
      <template v-if="show.asking">
        <g v-for="(l, i) in data!.listings" :key="'l' + i">
          <line :x1="X(l.first)" :x2="Math.max(X(l.last), X(l.first) + 3)" :y1="Y(l.price)" :y2="Y(l.price)" :class="['seg', l.status]">
            <title>Asking {{ money(l.price) }} · {{ l.status === 'active' ? 'still listed' : 'gone' }} · seen {{ l.first }} to {{ l.last }} · {{ l.title }}</title>
          </line>
        </g>
      </template>
      <path v-if="show.lines && data!.asking_line.length > 1" :d="path(data!.asking_line)" class="ln ask" />
      <path v-if="show.lines && data!.sold_line.length > 1" :d="path(data!.sold_line)" class="ln sold" />
      <template v-if="show.sold">
        <circle v-for="(s, i) in data!.sales" :key="'s' + i" :cx="X(s.date)" :cy="Y(s.price)" r="4.5" :class="['dot', { bo: s.best_offer }]">
          <title>Sold {{ money(s.price) }}{{ s.best_offer ? ' (best offer: asking price, real sale was probably lower)' : '' }} · {{ s.date }} · {{ s.title }}</title>
        </circle>
      </template>
    </svg>
    <p v-if="!empty" class="text-caption text-muted-c mt-1 mb-0">
      Each orange dot is one sale; hollow dots are Best Offer sales, shown at their asking price. Blue bars are asking listings, from the first day seen to the last (faded when the listing is gone).
      Hover a dot or bar for the listing. The scale zooms to whatever is switched on.
    </p>
  </div>
</template>

<style scoped>
.sum { display: flex; flex-wrap: wrap; gap: 6px 24px; margin-bottom: 8px; font-size: 14px; }
.k { color: rgb(var(--v-theme-text-secondary)); margin-right: 8px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.sw { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; }
.sw.ask { background: rgb(var(--v-theme-primary)); } .sw.sold { background: #d9731a; }
svg { display: block; touch-action: pan-y; }
.grid { stroke: rgb(var(--v-theme-border)); } .axis { font-size: 11px; fill: rgb(var(--v-theme-text-muted)); }
.band { fill: rgb(var(--v-theme-primary)); opacity: 0.08; }
.seg { stroke: rgb(var(--v-theme-primary)); stroke-width: 3; stroke-linecap: round; }
.seg.gone { opacity: 0.3; } .seg.active { opacity: 0.8; }
.ln { fill: none; stroke-width: 2; stroke-linejoin: round; }
.ln.ask { stroke: rgb(var(--v-theme-primary)); } .ln.sold { stroke: #d9731a; stroke-dasharray: 6 4; }
.dot { fill: #d9731a; stroke: rgb(var(--v-theme-surface)); stroke-width: 1.5; }
.dot.bo { fill: none; stroke: #d9731a; stroke-width: 2; }
.thin { padding: 36px 12px; text-align: center; color: rgb(var(--v-theme-text-secondary)); font-size: 14px; }
</style>
