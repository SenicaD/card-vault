<script setup lang="ts">
import { useDisplay } from 'vuetify'
import { CATEGORY_LABELS, STATUS_LABELS, type Card, type Category, type Location, type Stats } from '~/types'

const { api } = useApi()
const debug = useDebug()
const { mdAndUp } = useDisplay()
const route = useRoute()
const router = useRouter()

const q = ref((route.query.q as string) || '')
const category = ref<string | null>(null)
const status = ref<string | null>(null)
const locationId = ref<number | null>(route.query.location ? Number(route.query.location) : null)
const sort = ref<'recent' | 'value' | 'name' | 'oldest_price'>('recent')
const grade = ref<string | null>(null)
const minValue = ref<string>('')
const maxValue = ref<string>('')
// Problem filters stack: a card must match every one that is switched on.
const FLAGS = [
  { key: 'unpriced', label: 'No price' },
  { key: 'stale_days', label: 'Price over 7 days old' },
  { key: 'low_confidence', label: 'Weak match' },
  { key: 'no_photo', label: 'No photo' },
  { key: 'no_cost', label: 'No cost' },
] as const
const flags = ref<string[]>([])
const selected = ref<number[]>([])
const reprice = ref<{ start: (ids: number[] | null) => Promise<void> } | null>(null)
const view = ref<'table' | 'grid'>('table')
// The 7-column table needs desktop width; below md the grid is the only view.
const showTable = computed(() => mdAndUp.value && view.value === 'table')

const cards = ref<Card[]>([])
const locations = ref<Location[]>([])
const stats = ref<Stats | null>(null)
const error = ref('')
const loading = ref(true)

const locName = (id: number | null) => locations.value.find((l) => l.id === id)?.name
const subline = (c: Card) =>
  [c.year, c.set_name, c.card_number && '#' + c.card_number.replace(/^#/, '')].filter(Boolean).join(' ')

const categoryItems = Object.entries(CATEGORY_LABELS).map(([value, title]) => ({ value, title }))
const statusItems = Object.entries(STATUS_LABELS).map(([value, title]) => ({ value, title }))
const locationItems = computed(() => locations.value.map((l) => ({ value: l.id, title: l.name })))
const sortItems = [
  { value: 'recent', title: 'Newest first' },
  { value: 'value', title: 'Highest value' },
  { value: 'name', title: 'Name A–Z' },
  { value: 'oldest_price', title: 'Oldest price first' },
]

const headers = [
  { title: 'Card', key: 'name', sortable: false },
  { title: 'Type', key: 'category', sortable: false },
  { title: 'Location', key: 'location', sortable: false },
  { title: 'Grade', key: 'grade', sortable: false },
  { title: 'Qty', key: 'quantity', sortable: false, align: 'end' as const },
  { title: 'Status', key: 'status', sortable: false },
  { title: 'Value', key: 'value', sortable: false, align: 'end' as const },
  { title: 'Asking', key: 'est_value', sortable: false, align: 'end' as const },
]

// Category mix bar: one segment per category, coloured from a fixed categorical palette.
const CAT_COLORS: Record<Category, string> = {
  sports: '#2557d6', pokemon: '#e0a020', mtg: '#7b5bff', yugioh: '#d2453b', other_tcg: '#1fa38a', other: '#8a94a5',
}
const mix = computed(() => {
  const by = stats.value?.by_category ?? {}
  const total = Object.values(by).reduce((a, b) => a + b, 0)
  return (Object.keys(CATEGORY_LABELS) as Category[])
    .filter((k) => by[k])
    .map((k) => ({ key: k, label: CATEGORY_LABELS[k], count: by[k], pct: total ? (by[k] / total) * 100 : 0, color: CAT_COLORS[k] }))
})

const hasFilters = computed(() => !!(q.value || category.value || status.value || locationId.value || grade.value || minValue.value || maxValue.value || flags.value.length))
function clearFilters() {
  q.value = ''
  category.value = status.value = grade.value = null
  minValue.value = maxValue.value = ''
  flags.value = []
  locationId.value = null
}

// Saved views: a named set of filters, kept in this browser.
interface View { name: string; f: Record<string, any> }
const views = ref<View[]>([])
const viewName = ref('')
const saving = ref(false)
const snapshot = () => ({ q: q.value, category: category.value, status: status.value, locationId: locationId.value, grade: grade.value,
  minValue: minValue.value, maxValue: maxValue.value, flags: [...flags.value], sort: sort.value })
function applyView(v: View) {
  const f = v.f
  q.value = f.q ?? ''; category.value = f.category ?? null; status.value = f.status ?? null; locationId.value = f.locationId ?? null
  grade.value = f.grade ?? null; minValue.value = f.minValue ?? ''; maxValue.value = f.maxValue ?? ''; flags.value = f.flags ?? []; sort.value = f.sort ?? 'recent'
}
function persistViews() { try { localStorage.setItem('cv-views', JSON.stringify(views.value)) } catch {} }
function saveView() {
  const name = viewName.value.trim()
  if (!name) return
  views.value = [...views.value.filter((v) => v.name !== name), { name, f: snapshot() }]
  persistViews(); viewName.value = ''; saving.value = false
}
function deleteView(name: string) { views.value = views.value.filter((v) => v.name !== name); persistViews() }

async function load() {
  loading.value = true
  error.value = ''
  try {
    const query: Record<string, string> = { sort: sort.value }
    if (q.value) query.q = q.value
    if (category.value) query.category = category.value
    if (status.value) query.status = status.value
    if (locationId.value) query.location_id = String(locationId.value)
    if (grade.value) query.grade = grade.value
    if (minValue.value !== '') query.min_value = String(minValue.value)
    if (maxValue.value !== '') query.max_value = String(maxValue.value)
    for (const f of flags.value) query[f] = f === 'stale_days' ? '7' : 'true'
    ;[cards.value, stats.value, locations.value] = await Promise.all([
      api<Card[]>('/api/cards', { query }),
      api<Stats>('/api/cards/stats'),
      api<Location[]>('/api/locations'),
    ])
    const here = new Set(cards.value.map((c) => c.id))
    selected.value = selected.value.filter((id) => here.has(id)) // drop anything that is no longer listed
  } catch (e: any) {
    error.value = `Couldn't load inventory: ${e.message}. Check that the API is running on port 8000.`
  } finally {
    loading.value = false
  }
}

const open = (c: Card) => navigateTo(`/cards/${c.id}`)
const onRow = (_: unknown, row: { item: Card }) => open(row.item)
const toggle = (id: number) => { selected.value = selected.value.includes(id) ? selected.value.filter((x) => x !== id) : [...selected.value, id] }
const gradeItems = GRADE_OPTIONS
const repriceSelected = () => reprice.value?.start(selected.value)
const repriceAll = () => reprice.value?.start(null)
const revaluing = ref(false)
const revalued = ref('')
async function revalueAll() {
  revaluing.value = true; revalued.value = ''
  try {
    const r = await api<{ changed: number; unchanged: number; skipped: number }>('/api/cards/bulk/revalue', { method: 'POST' })
    revalued.value = `Re-valued from saved listings: ${r.changed} changed, ${r.unchanged} the same${r.skipped ? `, ${r.skipped} have no quote yet` : ''}.`
    await load()
  } catch (e: any) { error.value = e.message } finally { revaluing.value = false }
}

let t: ReturnType<typeof setTimeout>
watch(q, () => { clearTimeout(t); t = setTimeout(load, 250) })
watch([category, status, locationId, sort, grade, flags], load)
watch([minValue, maxValue], () => { clearTimeout(t); t = setTimeout(load, 400) })
// Header search navigates here with ?q=
watch(() => route.query.q, (v) => { q.value = (v as string) || '' })
watch(locationId, (v) => router.replace({ query: { ...route.query, location: v ? String(v) : undefined } }))
onMounted(() => {
  // Phones default to the grid; the table needs horizontal room.
  const fallback = window.innerWidth < 760 ? 'grid' : 'table'
  try { view.value = (localStorage.getItem('cv-view') as any) || fallback } catch { view.value = fallback }
  try { views.value = JSON.parse(localStorage.getItem('cv-views') || '[]') } catch {}
  load()
})
watch(view, (v) => { try { localStorage.setItem('cv-view', v) } catch {} })
</script>

<template>
  <div>
    <PageHeader title="Inventory" subtitle="Every card in your collection, where it's stored, and what it's worth.">
      <template #actions>
        <v-btn variant="outlined" prepend-icon="mdi-refresh" :loading="loading" @click="load">Refresh</v-btn>
        <v-menu>
          <template #activator="{ props }">
            <v-btn v-bind="props" variant="outlined" prepend-icon="mdi-cash-sync" append-icon="mdi-menu-down" :loading="revaluing">Re-price</v-btn>
          </template>
          <v-list density="comfortable">
            <v-list-item prepend-icon="mdi-calculator-variant-outline" title="Re-value all, instantly" subtitle="From saved listings. Use after changing price settings." @click="revalueAll" />
            <v-list-item prepend-icon="mdi-cash-sync" title="Re-price all from eBay" subtitle="Fresh searches. Slow with many cards." @click="repriceAll" />
          </v-list>
        </v-menu>
        <v-btn variant="outlined" prepend-icon="mdi-clipboard-check-outline" to="/review" class="d-none d-sm-flex">Review queue</v-btn>
        <v-btn color="primary" variant="flat" prepend-icon="mdi-camera-plus-outline" to="/scan" class="d-none d-md-flex">Scan card</v-btn>
      </template>
    </PageHeader>

    <v-alert v-if="error" type="error" variant="tonal" border="start" class="mb-4" :text="error" />
    <v-alert v-if="revalued" type="success" variant="tonal" border="start" class="mb-4" closable :text="revalued" @click:close="revalued = ''" />
    <RepriceStatus ref="reprice" @finished="load" />

    <!-- KPIs -->
    <v-row dense class="mb-2">
      <v-col cols="6" lg="3">
        <StatCard label="Collection value" :value="stats ? money(stats.total_value) : '—'" icon="mdi-currency-usd" color="primary"
          :sub="stats ? (stats.sold_cards ? `${stats.sold_cards} cards at sold prices` + (stats.asking_cards ? `, ${stats.asking_cards} at asking (too few sales)` : '') + `. Asking alone: ${money(stats.asking_value)}` : 'Asking prices so far: no sold data yet') + (stats.flagged_cards ? `. ${stats.flagged_cards} ${stats.flagged_cards === 1 ? 'card needs' : 'cards need'} a price review` : '') : ''" />
      </v-col>
      <v-col cols="6" lg="3">
        <StatCard label="Total cards" :value="stats?.total_cards ?? '—'" icon="mdi-cards-outline" color="info" :sub="`${locations.length} storage locations`" />
      </v-col>
      <v-col cols="6" lg="3">
        <StatCard label="In stock" :value="stats?.by_status.in_stock ?? 0" icon="mdi-package-variant-closed" color="success" sub="Available to sell" />
      </v-col>
      <v-col cols="6" lg="3">
        <StatCard label="Listed / sold" :value="`${stats?.by_status.listed ?? 0} / ${stats?.by_status.sold ?? 0}`" icon="mdi-tag-outline" color="warning" sub="On eBay / completed" />
      </v-col>
    </v-row>

    <!-- Category mix -->
    <v-card v-if="mix.length" class="mb-4">
      <div class="card-body py-4">
        <div class="d-flex align-center justify-space-between mb-2">
          <span class="text-body-2 font-weight-medium">Collection mix</span>
          <span class="text-caption text-muted-c">by quantity · click to filter</span>
        </div>
        <div class="mix-bar">
          <div v-for="m in mix" :key="m.key" :style="{ width: m.pct + '%', background: m.color }" />
        </div>
        <div class="mix-legend">
          <button
            v-for="m in mix"
            :key="m.key"
            type="button"
            class="mix-item"
            :class="{ on: category === m.key }"
            @click="category = category === m.key ? null : m.key"
          >
            <span class="sw" :style="{ background: m.color }" />
            {{ m.label }} <strong class="num">{{ m.count }}</strong>
            <span class="text-muted-c num">{{ Math.round(m.pct) }}%</span>
          </button>
        </div>
      </div>
    </v-card>

    <!-- Inventory -->
    <v-card>
      <div class="card-head">
        <div>
          <h2><v-icon icon="mdi-format-list-bulleted" size="18" />Cards</h2>
          <p>{{ cards.length }} {{ cards.length === 1 ? 'result' : 'results' }}<template v-if="hasFilters"> · filtered</template></p>
        </div>
        <v-btn-toggle v-if="mdAndUp" v-model="view" mandatory density="compact" variant="outlined" divided rounded="lg" color="primary">
          <v-btn value="table" icon="mdi-table" aria-label="Table view" />
          <v-btn value="grid" icon="mdi-view-grid-outline" aria-label="Grid view" />
        </v-btn-toggle>
      </div>

      <div class="toolbar">
        <v-text-field v-model="q" prepend-inner-icon="mdi-magnify" placeholder="Search name, set, number" clearable class="tb-search" aria-label="Search cards" />
        <v-select v-model="category" :items="categoryItems" placeholder="All types" clearable aria-label="Type" />
        <v-select v-model="status" :items="statusItems" placeholder="Any status" clearable aria-label="Status" />
        <v-select v-model="locationId" :items="locationItems" placeholder="All locations" clearable aria-label="Location" />
        <v-select v-model="sort" :items="sortItems" prepend-inner-icon="mdi-sort" aria-label="Sort" />
        <v-btn v-if="hasFilters" variant="text" size="small" prepend-icon="mdi-filter-remove-outline" @click="clearFilters">Clear</v-btn>
      </div>
      <div class="toolbar2">
        <v-select v-model="grade" :items="gradeItems" placeholder="Any grade" clearable density="compact" class="t2-grade" aria-label="Grade" />
        <v-text-field v-model="minValue" type="number" min="0" prefix="$" placeholder="Min value" density="compact" class="t2-num" aria-label="Minimum value" />
        <v-text-field v-model="maxValue" type="number" min="0" prefix="$" placeholder="Max value" density="compact" class="t2-num" aria-label="Maximum value" />
        <v-chip-group v-model="flags" multiple column class="t2-flags">
          <v-chip v-for="f in FLAGS" :key="f.key" :value="f.key" filter size="small" variant="outlined">{{ f.label }}</v-chip>
        </v-chip-group>
        <v-menu :close-on-content-click="false">
          <template #activator="{ props: p }"><v-btn v-bind="p" size="small" variant="text" prepend-icon="mdi-bookmark-outline">Views<span v-if="views.length" class="ml-1 num">({{ views.length }})</span></v-btn></template>
          <v-card min-width="260">
            <v-list density="compact">
              <v-list-item v-for="v in views" :key="v.name" :title="v.name" @click="applyView(v)">
                <template #append><v-btn icon="mdi-close" size="x-small" variant="text" :aria-label="`Delete view ${v.name}`" @click.stop="deleteView(v.name)" /></template>
              </v-list-item>
              <v-list-item v-if="!views.length" subtitle="No saved views yet" />
            </v-list>
            <v-divider />
            <div class="pa-3 d-flex ga-2">
              <v-text-field v-model="viewName" placeholder="Name this filter set" density="compact" hide-details @keyup.enter="saveView" />
              <v-btn variant="flat" color="primary" :disabled="!viewName.trim()" @click="saveView">Save</v-btn>
            </div>
          </v-card>
        </v-menu>
      </div>
      <BulkBar v-if="selected.length" :ids="selected" :locations="locations" @clear="selected = []" @changed="load" @reprice="repriceSelected" />
      <v-divider />

      <!-- Empty -->
      <div v-if="!loading && !error && cards.length === 0" class="empty">
        <div class="empty-icon"><v-icon :icon="hasFilters ? 'mdi-filter-off-outline' : 'mdi-cards-outline'" size="28" /></div>
        <h3>{{ hasFilters ? 'No cards match these filters' : 'No cards yet' }}</h3>
        <p>{{ hasFilters ? 'Try a different search or clear the filters.' : 'Photograph a card to identify it and add it to a storage location.' }}</p>
        <v-btn v-if="hasFilters" variant="outlined" @click="clearFilters">Clear filters</v-btn>
        <v-btn v-else color="primary" variant="flat" prepend-icon="mdi-camera-plus-outline" to="/scan">Scan your first card</v-btn>
      </div>

      <!-- Table -->
      <v-data-table
        v-else-if="showTable"
        :headers="headers"
        v-model="selected"
        :items="cards"
        item-value="id"
        show-select
        select-strategy="all"
        :loading="loading"
        items-per-page="50"
        :items-per-page-options="[25, 50, 100, 250]"
        class="inv-table"
        @click:row="onRow"
      >
        <template #item.name="{ item }">
          <div class="d-flex align-center ga-3 py-2" style="min-width: 220px">
            <div class="mini-thumb"><CardThumb :src="item.image_url" :alt="item.name" :foil="!!item.value && item.value >= 100" /></div>
            <div class="min-w-0">
              <div class="font-weight-semibold truncate">{{ item.name }}</div>
              <div class="text-caption text-secondary-c truncate">{{ subline(item) || '—' }}</div>
            </div>
          </div>
        </template>
        <template #item.category="{ item }">
          <span class="d-inline-flex align-center ga-2 text-no-wrap">
            <span class="sw" :style="{ background: CAT_COLORS[item.category] }" />{{ CATEGORY_LABELS[item.category] }}
          </span>
        </template>
        <template #item.location="{ item }">
          <div class="text-no-wrap">{{ locName(item.location_id) || 'Unassigned' }}</div>
          <div v-if="item.slot" class="text-caption text-muted-c">{{ item.slot }}</div>
        </template>
        <template #item.grade="{ item }">
          <span class="text-no-wrap">{{ item.grade || '—' }}<span v-if="item.condition" class="text-muted-c"> · {{ conditionName(item.condition) }}</span></span>
        </template>
        <template #item.quantity="{ item }"><span class="num">{{ item.quantity }}</span></template>
        <template #item.status="{ item }"><StatusPill :status="item.status" /></template>
        <template #item.est_value="{ item }"><span class="num text-muted-c text-no-wrap">{{ money(item.est_value) }}</span></template>
        <template #item.value="{ item }">
          <span class="num font-weight-semibold text-no-wrap">{{ money(item.value) }}</span>
          <v-chip v-if="item.value_basis" size="x-small" :color="item.value_basis === 'sold' ? 'success' : item.value_basis === 'manual' ? 'primary' : undefined" variant="tonal" class="ml-1" :title="item.value_basis === 'manual' ? 'A value you set yourself' : item.value_basis === 'sold' ? `Median of ${item.sold_count} recent sales` : 'Too few sales, so this is the asking-price estimate'">{{ item.value_basis === 'manual' ? 'manual' : item.value_basis === 'sold' ? `sold ${item.sold_count}` : 'asking' }}</v-chip>
          <v-chip v-if="item.wide_spread" size="x-small" color="warning" variant="tonal" prepend-icon="mdi-chart-bell-curve" class="ml-1" title="Prices vary widely; pick a version">wide spread</v-chip>
          <v-btn v-if="debug.on" icon="mdi-bug-outline" size="x-small" variant="text" color="warning" :to="`/cards/${item.id}?debug=1`" aria-label="How this price was calculated" title="How this price was calculated" @click.stop />
        </template>
      </v-data-table>

      <!-- Grid -->
      <div v-else class="grid card-body">
        <v-card v-for="c in cards" :key="c.id" class="tile" :class="{ picked: selected.includes(c.id) }" :to="`/cards/${c.id}`">
          <v-checkbox-btn :model-value="selected.includes(c.id)" class="pick" density="compact" :aria-label="`Select ${c.name}`" @click.stop.prevent="toggle(c.id)" />
          <div class="pa-3 pb-0"><CardThumb :src="c.image_url" :alt="c.name" :foil="!!c.value && c.value >= 100" /></div>
          <div class="pa-3">
            <div class="tile-row d-flex justify-space-between align-baseline ga-2">
              <strong class="truncate">{{ c.name }}</strong>
              <span class="num font-weight-semibold text-no-wrap">{{ money(c.value) }}</span>
            </div>
            <div v-if="c.value_basis || c.wide_spread" class="text-caption text-muted-c num"><template v-if="c.value_basis">{{ c.value_basis === 'manual' ? 'your value' : c.value_basis === 'sold' ? `sold, ${c.sold_count} sales` : 'asking price' }}<template v-if="c.value_basis === 'sold' && c.est_value != null"> · asking {{ money(c.est_value) }}</template></template><v-chip v-if="c.wide_spread" size="x-small" color="warning" variant="tonal" prepend-icon="mdi-chart-bell-curve" :class="{ 'ml-1': c.value_basis }" title="Prices vary widely; pick a version">wide spread</v-chip></div>
            <div v-if="debug.on" class="text-caption text-muted-c truncate">#{{ c.id }} · {{ c.priced_at ? isoDate(c.priced_at).slice(0, 10) : 'never priced' }} · {{ c.id_confidence != null ? Math.round(c.id_confidence * 100) + '% match' : 'no match data' }}</div>
            <div class="text-caption text-secondary-c truncate">{{ subline(c) || '—' }}</div>
            <div class="d-flex justify-space-between align-center ga-2 mt-2">
              <span class="text-caption text-muted-c truncate">{{ locName(c.location_id) || 'Unassigned' }}<template v-if="c.slot">, {{ c.slot }}</template></span>
              <StatusPill :status="c.status" size="x-small" />
            </div>
          </div>
        </v-card>
      </div>
    </v-card>
  </div>
</template>

<style scoped>
.min-w-0 { min-width: 0; }
.font-weight-semibold { font-weight: 600; }
.toolbar { display: grid; grid-template-columns: minmax(200px, 2fr) repeat(4, minmax(140px, 1fr)) auto; gap: 10px; padding: 14px 18px; align-items: center; }
@media (max-width: 1100px) { .toolbar { grid-template-columns: 1fr 1fr; } .tb-search { grid-column: 1 / -1; } }
@media (max-width: 599px) { .toolbar { padding: 12px; gap: 8px; } }
.mini-thumb { width: 36px; flex-shrink: 0; }
.mini-thumb :deep(.thumb) { border-radius: 5px; padding: 1px; }
.mini-thumb :deep(img), .mini-thumb :deep(.empty) { border-radius: 4px; }
.mini-thumb :deep(.v-icon) { font-size: 14px; }
.inv-table :deep(td) { border-bottom-color: rgb(var(--v-theme-border)) !important; }
.sw { width: 10px; height: 10px; border-radius: 3px; display: inline-block; flex-shrink: 0; }

.mix-bar { display: flex; height: 10px; border-radius: 6px; overflow: hidden; gap: 2px; background: rgb(var(--v-theme-surface-sunken)); }
.mix-legend { display: flex; flex-wrap: wrap; gap: 4px 6px; margin-top: 10px; }
.mix-item { display: inline-flex; align-items: center; gap: 6px; font: inherit; font-size: 13px; padding: 4px 8px; border-radius: 8px; border: 1px solid transparent; background: none; color: inherit; cursor: pointer; }
.mix-item:hover { background: rgb(var(--v-theme-surface-chrome)); }
.mix-item.on { border-color: rgb(var(--v-theme-primary)); background: rgb(var(--v-theme-brand-muted)); }

.grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fill, minmax(min(180px, 100%), 1fr)); }
@media (max-width: 599px) {
  .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding: 12px; }
  .tile-row { flex-direction: column; align-items: flex-start !important; gap: 0 !important; width: 100%; }
  .tile :deep(.pa-3) { padding: 8px !important; }
  .tile :deep(.pa-3.pb-0) { padding-bottom: 0 !important; }
}
.toolbar2 { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; padding: 0 18px 12px; }
.t2-grade { max-width: 190px; min-width: 150px; } .t2-num { max-width: 150px; min-width: 120px; }
.t2-flags { flex: 1 1 auto; }
.tile { position: relative; }
.pick { position: absolute; top: 6px; left: 6px; z-index: 2; background: rgb(var(--v-theme-surface)); border-radius: 6px; }
.tile.picked { border-color: rgb(var(--v-theme-primary)) !important; }
.tile { transition: border-color 0.15s, box-shadow 0.15s; }
.tile:hover { border-color: rgb(var(--v-theme-primary)) !important; box-shadow: 0 4px 14px rgba(20, 30, 60, 0.08); }

.empty { display: grid; justify-items: center; text-align: center; gap: 6px; padding: 56px 16px; }
.empty-icon { width: 56px; height: 56px; border-radius: 14px; display: grid; place-items: center; background: rgb(var(--v-theme-brand-muted)); color: rgb(var(--v-theme-primary)); margin-bottom: 6px; }
.empty h3 { font-size: 16px; font-weight: 600; margin: 0; }
.empty p { margin: 0 0 10px; color: rgb(var(--v-theme-text-secondary)); max-width: 44ch; font-size: 14px; }
</style>
