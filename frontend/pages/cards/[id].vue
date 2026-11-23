<script setup lang="ts">
import { useDisplay } from 'vuetify'
import { CATEGORY_LABELS, WIDE_SPREAD_ACTIONS, windowText, type AppSettings, type Card, type CardFields, type Listing, type Location, type MatchRow, type PriceCluster, type SimilarityResult, type PricePoints, type SoldCompare, type PriceResult, type PriceSnapshot, type Sale, type SoldRow, type Trend, type TrendRange } from '~/types'
import type { TableHeader } from '~/composables/tables'

const { api } = useApi()
const { xs } = useDisplay()
const route = useRoute()
const id = Number(route.params.id)
const debug = useDebug()
if (route.query.debug) debug.on.value = true // links from the Inventory markers
function showTrace() {
  debug.on.value = true
  nextTick(() => document.getElementById('debug-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}
onMounted(() => { if (route.query.debug) setTimeout(showTrace, 600) })

const card = ref<Card | null>(null)
const form = ref<CardFields | null>(null)
const locations = ref<Location[]>([])
const history = ref<PriceSnapshot[]>([])
const matches = ref<MatchRow[]>([])
const scores = ref<Record<string, number>>({})
const suggestion = ref<SimilarityResult | null>(null)
const showAllVersions = ref(false)
const listings = ref<Listing[]>([])
const editing = ref(false)
const busy = ref('')
const error = ref('')
const confirmDelete = ref(false)
const tab = ref('value')

const listTitle = ref('')
const listPrice = ref<number | null>(null)

const ownGrade = computed(() => normalizeGrade(card.value?.grade))
// The estimate and the KPI cards follow the card's own grade; older pooled snapshots are the fallback.
// Never fall back to another grade's snapshot: that would show, say, PSA 10 figures on a PSA 9 card.
// Only snapshots from before grades existed (no grade) stand in.
// Prices only count for the version of the card you picked (all versions together when none is picked).
const mine = (s: PriceSnapshot) => (s.parallel ?? null) === (card.value?.parallel ?? null)
const latest = computed(() => history.value.find((s) => s.grade === ownGrade.value && mine(s)) ?? history.value.find((s) => !s.grade) ?? null)

// Grades to price and plot. Colour slots follow the grade, never its position, so removing one
// grade doesn't repaint the others (the chart plots at most 4).
const MAX_PLOTTED = 4
const selectedGrades = ref<string[]>([])
const gradeCounts = ref<Record<string, number>>({})
const slots = reactive<Record<string, number>>({})
watch(selectedGrades, (sel) => {
  const plotted = sel.slice(0, MAX_PLOTTED)
  for (const g of Object.keys(slots)) if (!plotted.includes(g)) delete slots[g]
  for (const g of plotted) {
    if (slots[g] != null) continue
    const used = new Set(Object.values(slots))
    slots[g] = [0, 1, 2, 3].find((n) => !used.has(n)) ?? 0
  }
}, { immediate: true, deep: true })

const gradeOptions = computed(() => sortGrades([...new Set([
  ...STANDARD_GRADES,
  ...Object.keys(gradeCounts.value).filter((g) => g !== 'Unknown'),
  ...(history.value.map((s) => s.grade).filter(Boolean) as string[]),
  ...selectedGrades.value,
])]))
// history is newest first, so the first match is the latest asking price for that grade
const byGrade = computed(() => selectedGrades.value.map((grade) => ({
  grade, snap: history.value.find((s) => (s.source === 'ebay-active' || s.source === 'ebay-image') && s.grade === grade && mine(s)) ?? null,
})))
const lowConfidence = (n: number) => n > 0 && n < 5

// Which grades eBay has listings for, remembered on the card from the last search or daily refresh.
const searched = computed(() => !!card.value?.grade_counts_at || Object.keys(gradeCounts.value).length > 0)
const emptySelected = computed(() => (searched.value ? selectedGrades.value.filter((g) => !(gradeCounts.value[g] > 0)) : []))
const suggestions = computed(() => sortGrades(Object.keys(gradeCounts.value)
  .filter((g) => g !== 'Unknown' && gradeCounts.value[g] > 0 && !selectedGrades.value.includes(g))))

function priceGrade(g: string) {
  // swap the empty grades for this one, then price it
  selectedGrades.value = [...selectedGrades.value.filter((x) => !emptySelected.value.includes(x)), g]
  lookUp()
}

const gradeDraft = ref<string | null>(null)
const setGrade = () => run('grade', async () => {
  card.value = await api<Card>(`/api/cards/${id}`, { method: 'PATCH', body: { grade: gradeDraft.value } })
  form.value = fieldsOf(card.value)
  selectedGrades.value = [normalizeGrade(card.value.grade)]
})
async function setGradeAndPrice() {
  await setGrade()
  if (!error.value) await lookUp()
}

const RANGES: { value: TrendRange; label: string }[] = [
  { value: '7d', label: '7D' }, { value: '1m', label: '1M' }, { value: '3m', label: '3M' },
  { value: '1y', label: '1Y' }, { value: 'all', label: 'All' },
]
const sales = ref<Sale[]>([])
const soldDays = ref(90)
const sold = ref<SoldCompare | null>(null)
const pctText = (v: number | null | undefined) => (v == null ? '' : `${v > 0 ? '+' : ''}${v}%`)
const soldHeaders: TableHeader<SoldRow>[] = [
  { title: '', key: 'image_url', sortable: false, width: 64 },
  { title: 'Sold / gone', key: 'gone_at', text: (r) => (r.gone_at ? isoDate(r.gone_at).slice(0, 10) : '') },
  { title: 'Grade', key: 'grade' }, { title: 'Version', key: 'parallel' }, { title: 'Listing', key: 'title' },
  { title: 'Days up', key: 'days_listed', align: 'end' },
  { title: 'Price', key: 'price', align: 'end', text: (r) => money(r.price) },
  { title: 'vs asking now', key: 'vs_asking_pct', align: 'end', text: (r) => pctText(r.vs_asking_pct) },
]
const SOLD_SORT = [{ key: 'gone_at', order: 'desc' as const }]
const points = ref<PricePoints | null>(null)
const pointsGrade = ref<string | null>(null)
const loadPoints = async () => {
  if (!card.value) return
  points.value = await api<PricePoints>(`/api/cards/${id}/price-points`, {
    query: { grade: pointsGrade.value || normalizeGrade(card.value.grade), range: range.value, all_versions: showAllVersions.value },
  }).catch(() => null)
}
const checkingSold = ref(false)
const soldMsg = ref('')
async function checkSold() {
  checkingSold.value = true; soldMsg.value = ''
  try {
    const r = await api<{ found: number; new: number; used: number; median: number | null; best_offers: number; on_record: number }>(`/api/cards/${id}/sold/refresh`, { method: 'POST' })
    soldMsg.value = r.used ? `Found ${r.found} sales (${r.new} new to your record); ${r.used} match this card. Median ${money(r.median)}.` : `Found ${r.found} sales, but none match this card's grade and version.`
    await loadGone()
    loadTrend()
  } catch (e: any) { soldMsg.value = e.message } finally { checkingSold.value = false }
}
// loadGone keeps its old name: it is called after every lookup and refresh
const loadGone = async () => {
  loadPoints()
  sold.value = await api<SoldCompare>(`/api/cards/${id}/sold-compare`, {
    query: { days: soldDays.value, grades: selectedGrades.value, all_versions: showAllVersions.value },
  }).catch(() => null)
}

// "Mark sold" asks what you actually got, so the sale lands on the chart and in your sales list
const sellOpen = ref(false)
const sellPrice = ref<number | null>(null)
const sellDate = ref('')
const sellNote = ref('')
const todayISO = () => new Date().toISOString().slice(0, 10)
function openSell() {
  sellPrice.value = card.value?.value ?? card.value?.est_value ?? (card.value?.wide_spread ? null : latest.value?.median) ?? listPrice.value ?? null
  sellDate.value = todayISO()
  sellNote.value = ''
  sellOpen.value = true
}
const recordSale = () => run('sale', async () => {
  await api<Sale>(`/api/cards/${id}/sales`, {
    method: 'POST',
    body: { price: sellPrice.value, note: sellNote.value || null, sold_at: sellDate.value === todayISO() ? undefined : `${sellDate.value}T12:00:00Z` },
  })
  sellOpen.value = false
  ;[card.value, sales.value] = await Promise.all([api<Card>(`/api/cards/${id}`), api<Sale[]>(`/api/cards/${id}/sales`)])
  loadTrend()
})
const removeSale = (sid: number) => run('sale', async () => {
  await api(`/api/cards/${id}/sales/${sid}`, { method: 'DELETE' })
  sales.value = sales.value.filter((x) => x.id !== sid)
  loadTrend()
})

const range = ref<TrendRange>('1m')
const trend = ref<Trend | null>(null)
const trendLoading = ref(false)
async function loadTrend() {
  trendLoading.value = true
  if (!selectedGrades.value.length) return
  try { trend.value = await api<Trend>(`/api/cards/${id}/trend`, { query: { range: range.value, grades: selectedGrades.value } }) }
  catch { trend.value = null } finally { trendLoading.value = false }
}
watch([range, selectedGrades], () => {
  loadTrend()
  try { localStorage.setItem('cv-trend-range', range.value) } catch {}
}, { deep: true })
const locName = computed(() => locations.value.find((l) => l.id === card.value?.location_id)?.name)
const subline = computed(() => {
  const c = card.value
  return c ? [c.year, c.set_name, c.card_number && '#' + c.card_number.replace(/^#/, ''), c.variant].filter(Boolean).join(' · ') : ''
})

function fieldsOf(c: Card): CardFields {
  const { category, name, set_name, year, card_number, variant, grade, condition, quantity, location_id, slot, notes, search_query, cost, manual_value, is_redemption, is_auto } = c
  return { category, name, set_name, year, card_number, variant, grade, condition, quantity, location_id, slot, notes, search_query, cost, manual_value, is_redemption, is_auto }
}

function defaultTitle(c: Card) {
  return [c.year, c.set_name, c.name, c.card_number && '#' + c.card_number.replace(/^#/, ''), c.variant,
    c.grade && c.grade.toLowerCase() !== 'raw' ? c.grade : null].filter(Boolean).join(' ').slice(0, 80)
}

async function load() {
  try {
    ;[card.value, locations.value, history.value, listings.value, sales.value] = await Promise.all([
      api<Card>(`/api/cards/${id}`),
      api<Location[]>('/api/locations'),
      api<PriceSnapshot[]>(`/api/cards/${id}/prices`),
      api<Listing[]>(`/api/cards/${id}/listings`),
      api<Sale[]>(`/api/cards/${id}/sales`),
    ])
    loadGone()
    form.value = fieldsOf(card.value)
    listTitle.value = defaultTitle(card.value)
    listPrice.value = card.value.est_value
    gradeCounts.value = card.value.grade_counts ?? {}
    if (!selectedGrades.value.length) selectedGrades.value = [normalizeGrade(card.value.grade)]
    loadMatches()
  } catch (e: any) {
    error.value = e.message
  }
}

async function run(label: string, fn: () => Promise<void>) {
  busy.value = label
  error.value = ''
  try { await fn() } catch (e: any) { error.value = e.message } finally { busy.value = '' }
}

const save = () => run('save', async () => {
  const before = ownGrade.value
  card.value = await api<Card>(`/api/cards/${id}`, { method: 'PATCH', body: { ...form.value, cost: form.value!.cost === '' || form.value!.cost == null ? null : Number(form.value!.cost),
    manual_value: form.value!.manual_value === '' || form.value!.manual_value == null ? null : Number(form.value!.manual_value) } })
  form.value = fieldsOf(card.value)
  // The card's grade changed: move the grade selection (chips, price table, trend) over to the new grade.
  const after = normalizeGrade(card.value.grade)
  if (after !== before) {
    const swapped = selectedGrades.value.map((g) => (g === before ? after : g))
    selectedGrades.value = [...new Set(swapped.includes(after) ? swapped : [after, ...swapped])]
  }
  listTitle.value = defaultTitle(card.value)
  editing.value = false
})

function cancelEdit() {
  if (card.value) form.value = fieldsOf(card.value)
  editing.value = false
}

const cropping = ref(false)
const cropCard = (box: { x: number; y: number; w: number; h: number }) => run('crop', async () => {
  card.value = await api<Card>(`/api/cards/${id}/crop`, { method: 'POST', body: box })
  cropping.value = false
  await lookUp() // the new photo drives a fresh eBay image search
})

const lookUp = () => run('price', async () => {
  const res = await api<PriceResult>(`/api/cards/${id}/price`, {
    method: 'POST', body: { query: form.value?.search_query || null, grades: selectedGrades.value },
  })
  gradeCounts.value = res.grade_counts
  history.value = [...res.snapshots, ...history.value]
  loadGone()
  card.value = await api<Card>(`/api/cards/${id}`)
  if (!editing.value) form.value = fieldsOf(card.value) // a poor first identification may have been replaced
  loadMatches()
  scores.value = {}
  suggestion.value = null
  if (!listPrice.value && !card.value.wide_spread) listPrice.value = card.value.spread_rule ? card.value.est_value : res.snapshot.median
  loadTrend()
})

const list = () => run('list', async () => {
  const l = await api<Listing>(`/api/cards/${id}/listings`, {
    method: 'POST', body: { title: listTitle.value, price: listPrice.value, publish: true },
  })
  listings.value = [l, ...listings.value]
  card.value = await api<Card>(`/api/cards/${id}`)
})

const setStatus = (status: Card['status']) => run('status', async () => {
  card.value = await api<Card>(`/api/cards/${id}`, { method: 'PATCH', body: { status } })
})

const remove = () => run('delete', async () => {
  await api(`/api/cards/${id}`, { method: 'DELETE' })
  confirmDelete.value = false
  await navigateTo('/')
})

// ----- Matched listings: kept between visits, with your own include/exclude choices -----
const compSearch = ref('')
async function loadMatches() {
  matches.value = await api<MatchRow[]>(`/api/cards/${id}/matches`, { query: { grades: selectedGrades.value } }).catch(() => [])
}
watch(selectedGrades, () => { if (card.value) loadMatches() }, { deep: true })
watch([selectedGrades, showAllVersions], () => { if (card.value) loadGone() }, { deep: true })
watch([range, pointsGrade], () => { if (card.value) loadPoints() })

// The versions ("parallels") seen among the listings of the selected grades, most common first.
const parallelCounts = computed(() => {
  const n: Record<string, number> = {}
  for (const m of matches.value) n[m.parallel] = (n[m.parallel] ?? 0) + 1
  const mineP = card.value?.parallel
  if (mineP && !(mineP in n)) n[mineP] = 0
  return Object.entries(n).sort((a, b) => b[1] - a[1]).slice(0, 12)
})
const setParallel = (p: string | null) => run('parallel', async () => {
  card.value = await api<Card>(`/api/cards/${id}`, { method: 'PATCH', body: { parallel: p } })
  suggestion.value = null
  await lookUp() // price the version you picked
})

// The table's items: the listings of the version shown. Its search narrows them to shownComps.
type CompRow = MatchRow & { look: number | null; included: boolean }
const versionComps = computed<CompRow[]>(() => {
  const want = showAllVersions.value ? null : card.value?.parallel
  return matches.value
    .filter((m) => !want || m.parallel === want)
    .map((m) => ({ ...m, look: scores.value[m.item_id] ?? null, included: !m.excluded }))
})
const shownComps = computed(() => searchRows(versionComps.value, compHeaders.value, compSearch.value))
const hasScores = computed(() => Object.keys(scores.value).length > 0)

// The table's own sort and page, so reloading the listings after a tick doesn't reset them.
const compSort = ref<{ key: string; order?: boolean | 'asc' | 'desc' }[]>([])
const compPage = ref(1)
const compPerPage = ref(10)
// Scores cleared: the Look column is gone, so don't keep sorting by it.
watch(hasScores, (has) => { if (!has) compSort.value = compSort.value.filter((s) => s.key !== 'look') })

// Leave listings out of (or put them back into) the prices, without a new eBay search.
// The backend takes at most 200 ids per call; the card and listings reload once, after the last batch
// (or after a failed one, since the batches before it are already saved).
const setExcluded = (itemIds: string[], excluded: boolean) => run('exclude', async () => {
  try {
    for (let i = 0; i < itemIds.length; i += 200) {
      const res = await api<PriceResult>(`/api/cards/${id}/matches/exclude`, {
        method: 'POST', body: { item_ids: itemIds.slice(i, i + 200), excluded, grades: selectedGrades.value },
      })
      history.value = [...res.snapshots, ...history.value]
    }
  } finally {
    card.value = await api<Card>(`/api/cards/${id}`)
    await loadMatches()
  }
})
const toggleInclude = (m: MatchRow) => setExcluded([m.item_id], !m.excluded)
// The box at the top of the Use column: unticks every shown listing when any is ticked, else ticks them all.
// It shows as ticked while any is (a dash when only some are), so each click changes its value.
const shownTicked = computed(() => shownComps.value.filter((m) => m.included).length)
function toggleAllShown() {
  const excluded = shownTicked.value > 0
  setExcluded(shownComps.value.filter((m) => m.excluded !== excluded).map((m) => m.item_id), excluded)
}

// The price groups behind a "prices vary too widely" flag or a value one of the wide-spread rules set, refetched
// whenever the card is reloaded or changed.
const clusters = ref<PriceCluster[]>([])
watch(card, async (c) => {
  const got = c?.wide_spread || c?.spread_rule ? await api<PriceCluster[]>(`/api/cards/${id}/price-clusters`).catch(() => []) : []
  if (card.value === c) clusters.value = got // a newer reload wins
})

// What this card gets when its prices spread too widely: its own choice, or the Settings page default
const defaultRule = ref<AppSettings['wide_spread_action'] | null>(null)
const priceWindow = ref<number | null>(null)
const ruleTitle = (v: string | null | undefined) => WIDE_SPREAD_ACTIONS.find((a) => a.value === v)?.title
const ruleItems = computed(() => [
  { value: 'default', title: `Use the default${defaultRule.value ? ` (${ruleTitle(defaultRule.value)})` : ''}` },
  ...WIDE_SPREAD_ACTIONS.map((a) => ({ value: a.value, title: a.title })),
])
// The window the rule takes, with the current Settings N (the median of all when there were N or fewer)
const ruleText = computed(() => {
  const rule = card.value?.spread_rule, n = priceWindow.value, k = latest.value?.sample_size
  if (!rule || n == null) return ''
  if (k != null && k <= n) return k === 1 ? 'the price of its only listing' : `the median of all ${k} listings`
  return windowText(rule, n)
})
// How many of the listings in the price count (own grade and version, not unticked) the window actually values the card from
const windowCounts = computed(() => {
  const own = matches.value.filter((m) => m.grade === ownGrade.value && !m.excluded && (!card.value?.parallel || m.parallel === card.value.parallel))
  return { used: matches.value.filter((m) => m.in_window).length, all: own.length }
})
const setRule = (v: string) => run('rule', async () => {
  card.value = await api<Card>(`/api/cards/${id}`, { method: 'PATCH', body: { wide_spread_action: v === 'default' ? null : v } })
})

const rankByLook = () => run('rank', async () => {
  const res = await api<SimilarityResult>(`/api/cards/${id}/matches/similarity`, { method: 'POST', body: { grades: selectedGrades.value } })
  scores.value = res.scores
  if (Object.keys(res.scores).length) compSort.value = [{ key: 'look', order: 'desc' }]
  suggestion.value = res.suggested_parallel && res.suggested_parallel !== card.value?.parallel ? res : null
  if (!Object.keys(res.scores).length) error.value = "Couldn't load the listing pictures to compare."
})

// How far a listing's price sits from the median for its own grade, in percent.
function vsMedian(c: { price: number; grade?: string | null }): number | null {
  const m = history.value.find((s) => s.grade === c.grade && s.median && mine(s))?.median
  return m ? Math.round(((c.price - m) / m) * 100) : null
}
const compHeaders = computed<TableHeader<CompRow>[]>(() => [
  { title: 'Use', key: 'included', sortable: false, width: 48 },
  ...(xs.value ? [] : [{ title: '', key: 'image_url', sortable: false, width: 64 }]),
  ...(card.value?.spread_rule ? [{ title: 'Prices card', key: 'in_window', text: (m: CompRow) => (m.in_window ? 'Yes' : '') }] : []),
  // On phones the cell also shows the grade and version
  { title: 'Listing', key: 'title', text: (m) => [m.title, ...(xs.value ? [m.grade, m.parallel] : []), m.condition].filter(Boolean).join(' ') },
  ...(xs.value ? [] : [{ title: 'Grade', key: 'grade' }, { title: 'Version', key: 'parallel' }]),
  ...(hasScores.value ? [{ title: 'Look', key: 'look', align: 'end' as const, text: (m: CompRow) => (m.look == null ? '' : `${m.look}%`) }] : []),
  { title: 'Price', key: 'price', align: 'end', text: (m) => money(m.price) },
  ...(xs.value ? [] : [{ title: 'vs median', key: 'vs', align: 'end' as const, nowrap: true, value: (m: CompRow) => vsMedian(m), text: (m: CompRow) => pctText(vsMedian(m)) }]),
])
// When the last refresh found the same numbers as the ones before it, say since when.
const unchangedSince = computed(() => {
  const rows = history.value.filter((s) => s.grade === ownGrade.value && mine(s))
  const first = rows[0]
  if (!first || rows.length < 2) return null
  let since = first
  for (const r of rows.slice(1)) {
    if (r.median === first.median && r.sample_size === first.sample_size) since = r
    else break
  }
  return since === first ? null : since.created_at
})
const lastChecked = computed(() => latest.value?.created_at ?? null)
const when = (s: string | null | undefined) => isoDate(s).replace('T', ' ').replace(/:\d\dZ$/, 'Z')
const historyHeaders: TableHeader<PriceSnapshot>[] = [
  { title: 'Date (UTC)', key: 'created_at', text: (s) => isoDate(s.created_at) },
  { title: 'Grade', key: 'grade', text: (s) => s.grade || 'All' },
  { title: 'Source', key: 'source' },
  { title: 'Samples', key: 'sample_size', align: 'end' },
  { title: 'Low', key: 'low', align: 'end', text: (s) => money(s.low) },
  { title: 'Median', key: 'median', align: 'end', text: (s) => money(s.median) },
  { title: 'High', key: 'high', align: 'end', text: (s) => money(s.high) },
]

onMounted(() => {
  try {
    const saved = localStorage.getItem('cv-trend-range') as TrendRange | null
    if (saved && RANGES.some((r) => r.value === saved)) range.value = saved
  } catch {}
  load() // selects the card's own grade, which loads the trend
  api<AppSettings>('/api/settings').then((s) => {
    defaultRule.value = s.wide_spread_action
    priceWindow.value = s.price_window
  }).catch(() => {})
})
</script>

<template>
  <div v-if="card && form">
    <PageHeader :title="card.name" :subtitle="subline" :crumbs="[{ title: 'Inventory', to: '/' }, { title: card.name }]">
      <template #actions>
        <StatusPill :status="card.status" size="default" />
        <v-btn v-if="card.status !== 'sold'" variant="outlined" prepend-icon="mdi-check-circle-outline" @click="openSell">Mark sold</v-btn>
        <v-btn v-else variant="outlined" prepend-icon="mdi-undo" :loading="busy === 'status'" @click="setStatus('in_stock')">Return to stock</v-btn>
        <v-btn variant="outlined" color="error" prepend-icon="mdi-delete-outline" @click="confirmDelete = true">Delete</v-btn>
      </template>
    </PageHeader>

    <v-alert v-if="error" type="error" variant="tonal" border="start" closable class="mb-4" :text="error" @click:close="error = ''" />

    <v-row>
      <!-- Left: photo + details -->
      <v-col cols="12" md="4" lg="3">
        <div class="side">
          <v-card>
            <div class="card-body photo">
              <CardThumb :src="card.image_url" :alt="card.name" :foil="!!card.value && card.value >= 100" />
              <v-btn v-if="card.image_url" block size="small" variant="outlined" class="mt-2" prepend-icon="mdi-crop" @click="cropping = true">Crop photo</v-btn>
            </div>
          </v-card>
          <v-alert v-if="card.id_confidence != null && card.id_confidence < 0.3" type="warning" variant="tonal" density="compact">
            This photo didn't match clearly ({{ Math.round(card.id_confidence * 100) }}%), so the listings may be for the wrong card.
            Crop the photo to just the card, then press Refresh value.
          </v-alert>
          <CropDialog v-model="cropping" :src="card.image_url" :busy="busy === 'crop'" @apply="cropCard" />

          <v-card>
            <div class="card-head">
              <h2><v-icon icon="mdi-information-outline" size="18" />Details</h2>
              <v-btn v-if="!editing" variant="text" size="small" prepend-icon="mdi-pencil-outline" @click="editing = true">Edit</v-btn>
            </div>
            <dl class="facts">
              <div><dt>Type</dt><dd>{{ CATEGORY_LABELS[card.category] }}</dd></div>
              <div><dt>Stored in</dt><dd>{{ locName || 'Unassigned' }}<span v-if="card.slot" class="text-muted-c">, {{ card.slot }}</span></dd></div>
              <div><dt>Grade</dt><dd>{{ card.grade || '—' }}<span v-if="card.condition" class="text-muted-c" :title="conditionInfo(card.condition)?.description"> · {{ conditionName(card.condition) }}</span></dd></div>
              <div><dt>Quantity</dt><dd class="num">{{ card.quantity }}</dd></div>
              <div v-if="card.id_confidence != null"><dt>ID confidence</dt><dd class="num">{{ Math.round(card.id_confidence * 100) }}%</dd></div>
              <div><dt>Added</dt><dd class="num">{{ isoDate(card.created_at) }}</dd></div>
              <div><dt>Updated</dt><dd class="num">{{ isoDate(card.updated_at) }}</dd></div>
              <div v-if="card.notes" class="wide"><dt>Notes</dt><dd>{{ card.notes }}</dd></div>
              <template v-if="debug.on">
                <div class="wide"><dt>Search words</dt><dd>{{ card.search_query || '—' }}</dd></div>
                <div><dt>Priced grade</dt><dd>{{ normalizeGrade(card.grade) }}<span v-if="card.parallel" class="text-muted-c"> · {{ card.parallel }}</span></dd></div>
                <div><dt>Priced at</dt><dd class="num">{{ card.priced_at ? isoDate(card.priced_at) : 'never' }}</dd></div>
                <div><dt>Card id / photo</dt><dd class="num">{{ card.id }} · {{ card.image_url?.split('/').pop() || 'none' }}</dd></div>
              </template>
            </dl>
          </v-card>
        </div>
      </v-col>

      <!-- Right -->
      <v-col cols="12" md="8" lg="9">
        <!-- Edit -->
        <v-card v-if="editing" class="mb-4">
          <div class="card-head"><div><h2><v-icon icon="mdi-pencil-outline" size="18" />Edit card</h2><p>Changes apply to the inventory record.</p></div></div>
          <div class="card-body"><CardForm v-model="form" :locations="locations" /></div>
          <v-divider />
          <div class="footer-actions">
            <v-spacer />
            <v-btn variant="text" @click="cancelEdit">Cancel</v-btn>
            <v-btn color="primary" variant="flat" :loading="busy === 'save'" prepend-icon="mdi-content-save-outline" @click="save">Save changes</v-btn>
          </div>
        </v-card>

        <!-- Value KPIs -->
        <v-row dense class="mb-2">
          <v-col cols="6" sm="3" class="kpi-wrap">
            <StatCard :label="card.value_basis === 'manual' ? 'Your value' : card.value_basis === 'sold' ? 'Sold value' : 'Value (asking)'" :value="money(card.value)" icon="mdi-currency-usd" color="primary"
              :sub="card.value_basis === 'manual' ? `Set by you · ${card.sold_value != null ? 'sold ' + money(card.sold_value) : 'no sold value'} · asking ${money(card.est_value)}` : card.value_basis === 'sold' ? `Median of ${card.sold_count} sales, last 90 days · asking ${money(card.est_value)}` : card.value_basis === 'asking' ? `Asking price${card.sold_count ? `: only ${card.sold_count} sales, too few` : ': no sold data yet'}${card.priced_at ? ` · priced ${isoDate(card.priced_at).slice(0, 10)}` : ''}` : card.wide_spread ? 'Prices vary too widely' : 'Not priced'" />
            <v-btn v-if="debug.on" icon="mdi-bug-outline" size="x-small" variant="tonal" color="warning" class="kpi-bug" aria-label="Show how this price was calculated" title="Show how this price was calculated" @click="showTrace" />
          </v-col>
          <v-col cols="6" sm="3"><StatCard :label="card.value_basis === 'sold' ? 'Lowest sale' : 'Low'" :value="money(card.value_basis === 'sold' ? card.sold_low : latest?.low)" icon="mdi-arrow-down" color="info" /></v-col>
          <v-col cols="6" sm="3"><StatCard :label="card.value_basis === 'sold' ? 'Highest sale' : 'High'" :value="money(card.value_basis === 'sold' ? card.sold_high : latest?.high)" icon="mdi-arrow-up" color="warning" /></v-col>
          <v-col cols="6" sm="3"><StatCard :label="card.value_basis === 'sold' ? 'Sales counted' : 'Sample size'" :value="card.value_basis === 'sold' ? card.sold_count : (latest?.sample_size ?? '—')" icon="mdi-chart-box-outline" color="success" :sub="card.value_basis === 'sold' ? `Last 90 days · ${latest?.sample_size ?? 0} asking listings` : latest?.source === 'ebay-image' ? 'Image matches' : 'Active listings'" /></v-col>
        </v-row>

        <v-card>
          <v-tabs v-model="tab" color="primary" density="comfortable" class="tabs">
            <v-tab value="value" :prepend-icon="xs ? undefined : 'mdi-cash-multiple'">{{ xs ? 'Value' : 'Valuation' }}</v-tab>
            <v-tab value="sell" :prepend-icon="xs ? undefined : 'mdi-storefront-outline'">{{ xs ? 'Sell' : 'Sell on eBay' }}</v-tab>
            <v-tab value="history" :prepend-icon="xs ? undefined : 'mdi-history'">
              {{ xs ? 'History' : 'Price history' }} <v-chip size="x-small" class="ml-2" variant="tonal">{{ history.length }}</v-chip>
            </v-tab>
          </v-tabs>
          <v-divider />

          <v-window v-model="tab">
            <!-- Valuation -->
            <v-window-item value="value">
              <div class="card-body">
                <div class="d-flex ga-3 align-center flex-wrap">
                  <v-text-field v-model="form.search_query" label="eBay search query" prepend-inner-icon="mdi-magnify" class="flex-grow-1" style="min-width: 240px" />
                  <v-btn color="primary" variant="flat" :loading="busy === 'price'" prepend-icon="mdi-refresh" @click="lookUp">
                    {{ latest ? 'Refresh value' : 'Look up value' }}
                  </v-btn>
                </div>

                <div class="text-body-2 font-weight-medium mt-4 mb-1">Grades to price</div>
                <v-chip-group v-model="selectedGrades" multiple mandatory column filter color="primary" aria-label="Grades to price">
                  <v-chip v-for="g in gradeOptions" :key="g" :value="g" variant="outlined" size="small">
                    <v-icon v-if="g === ownGrade" icon="mdi-star" size="12" class="mr-1" :title="`This card's grade`" />{{ g }}<span v-if="gradeCounts[g]" class="text-muted-c ml-1">· {{ gradeCounts[g] }}</span>
                  </v-chip>
                </v-chip-group>
                <p class="text-caption text-muted-c mt-1 mb-0">
                  <v-icon icon="mdi-star" size="11" /> is this card's grade. The estimate follows it; other grades are for comparison. Each grade you price is re-priced daily.
                </p>

                <!-- Which version (colour / print run) of the card is yours -->
                <template v-if="parallelCounts.length">
                  <div class="text-body-2 font-weight-medium mt-4 mb-1">Your version</div>
                  <div class="d-flex flex-wrap ga-2">
                    <v-chip :variant="!card.parallel ? 'flat' : 'outlined'" :color="!card.parallel ? 'primary' : undefined" size="small" :disabled="busy === 'parallel'" @click="card.parallel && setParallel(null)">All versions</v-chip>
                    <v-chip v-for="[p, n] in parallelCounts" :key="p" :variant="card.parallel === p ? 'flat' : 'outlined'" :color="card.parallel === p ? 'primary' : undefined" size="small" :disabled="busy === 'parallel'" @click="card.parallel !== p && setParallel(p)">
                      {{ p }}<span class="ml-1 opacity-70">· {{ n }}</span>
                    </v-chip>
                  </div>
                  <p class="text-caption text-muted-c mt-1 mb-0">Colours and print runs sell for very different prices. Pick yours and prices, the trend and the daily refresh use only that version.</p>
                  <v-alert v-if="suggestion" type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-palette-outline">
                    Your photo looks most like <strong>{{ suggestion.suggested_parallel }}</strong> ({{ suggestion.votes }} of the {{ suggestion.of }} closest listings).
                    <template #append><v-btn size="small" variant="flat" :loading="busy === 'parallel'" @click="setParallel(suggestion.suggested_parallel)">Use it</v-btn></template>
                  </v-alert>
                </template>

                <!-- No grade set: it is being priced as Raw -->
                <v-alert v-if="!card.grade" type="warning" variant="tonal" density="compact" class="mt-4" icon="mdi-alert-outline">
                  <div>This card has no grade, so it's priced as <strong>Raw</strong>. If it's in a graded slab, set its grade so the estimate uses the right listings.</div>
                  <div class="d-flex ga-2 align-center flex-wrap mt-3">
                    <v-combobox v-model="gradeDraft" :items="GRADE_OPTIONS" label="This card's grade" placeholder="PSA 9, BGS 9.5…" style="max-width: 240px" bg-color="surface" />
                    <v-btn color="primary" variant="flat" size="small" :disabled="!gradeDraft" :loading="busy === 'grade'" @click="setGradeAndPrice">Set grade and price</v-btn>
                  </div>
                </v-alert>

                <!-- The selected grade has no listings -->
                <v-alert v-if="emptySelected.length" type="warning" variant="tonal" density="compact" class="mt-4" icon="mdi-magnify-close">
                  <template v-if="suggestions.length">
                    <div>No <strong>{{ emptySelected.join(', ') }}</strong> listings on eBay right now for this search. These grades do have listings:</div>
                    <div class="d-flex flex-wrap ga-2 mt-3">
                      <v-btn v-for="g in suggestions.slice(0, 8)" :key="g" size="small" variant="outlined" :loading="busy === 'price'" @click="priceGrade(g)">
                        Price {{ g }} <span class="text-muted-c ml-1">· {{ gradeCounts[g] }}</span>
                      </v-btn>
                    </div>
                  </template>
                  <div v-else>No <strong>{{ emptySelected.join(', ') }}</strong> listings, and nothing at all for this search. Try a shorter or different search query.</div>
                </v-alert>

                <v-alert v-if="latest" type="info" variant="tonal" density="compact" class="mt-4" icon="mdi-information-outline">
                  {{ latest.grade || 'All grades' }}<template v-if="latest.parallel"> · {{ latest.parallel }}</template>: {{ latest.sample_size }} {{ latest.source === 'ebay-image' ? 'listings matched by photo' : 'active listings' }}, checked {{ when(lastChecked) }}.
                  <template v-if="unchangedSince"> Unchanged since {{ when(unchangedSince) }}.</template>
                  <template v-if="latest.trimmed"> {{ latest.trimmed }} outlier {{ latest.trimmed === 1 ? 'price was' : 'prices were' }} left out.</template>
                  Asking prices run higher than sold prices.
                </v-alert>
                <v-alert v-if="card.wide_spread" type="warning" variant="tonal" density="compact" class="mt-3" icon="mdi-chart-bell-curve">
                  No asking value is shown because these listings vary too much<template v-if="latest?.spread != null"> (the middle half spans {{ Math.round(latest.spread * 100) }}% of the median)</template>. Picking your version above, or unticking listings that aren't this card in the listings table, clears this and brings the asking value back.
                  <template v-if="clusters.length">
                    <div v-for="(g, i) in clusters" :key="i" class="mt-1">
                      ~{{ money(g.median) }} · {{ money(g.low) }}–{{ money(g.high) }} · {{ g.count }} {{ g.count === 1 ? 'listing' : 'listings' }}<template v-if="g.versions.length"> · {{ g.versions[0].name }}</template>
                    </div>
                    <div class="mt-1">Pick your version above, or untick the listings that aren't yours in the listings table.</div>
                  </template>
                </v-alert>
                <v-alert v-else-if="card.spread_rule" type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-chart-bell-curve">
                  The asking value of {{ money(card.est_value) }}<template v-if="ruleText"> is {{ ruleText }}</template>, {{ card.wide_spread_action ? 'the rule chosen for this card below' : 'the default on the Settings page' }}.<template v-if="latest?.spread != null"> The middle half of the prices spans {{ Math.round(latest.spread * 100) }}% of the median.</template>
                  <div v-if="windowCounts.used" class="mt-1 font-weight-medium">{{ windowCounts.used }} of the {{ windowCounts.all }} listings counted set this value (outliers and the rest are left out); they're marked in the listings table.</div>
                  <template v-if="ruleText"> This uses the current "Listings to price from" setting; if you changed it after this card was priced, the value updates the next time it's priced.</template>
                  <template v-if="(latest?.spread ?? 0) >= 0.8"><div v-for="(g, i) in clusters" :key="i" class="mt-1">
                    ~{{ money(g.median) }} · {{ money(g.low) }}–{{ money(g.high) }} · {{ g.count }} {{ g.count === 1 ? 'listing' : 'listings' }}<template v-if="g.versions.length"> · {{ g.versions[0].name }}</template>
                  </div></template>
                  <div class="mt-1">If that's not your card, pick your version above, or untick the listings that aren't yours in the listings table.</div>
                </v-alert>
                <v-alert v-else-if="latest && !card.parallel && (latest.spread ?? 0) >= 0.8" type="warning" variant="tonal" density="compact" class="mt-3" icon="mdi-chart-bell-curve">
                  These prices vary a lot (the middle half spans {{ Math.round((latest.spread ?? 0) * 100) }}% of the median), which usually means several versions of this card are mixed together. Pick your version above, or use the listings table to leave out ones that aren't yours.
                </v-alert>
                <v-select
                  v-if="card.wide_spread || card.spread_rule || card.wide_spread_action"
                  :model-value="card.wide_spread_action ?? 'default'"
                  :items="ruleItems"
                  label="When this card's prices spread too widely"
                  density="compact"
                  hide-details
                  class="mt-3"
                  style="max-width: 420px"
                  :loading="busy === 'rule'"
                  :disabled="busy === 'rule'"
                  @update:model-value="setRule"
                />
                <p v-if="!latest" class="text-body-2 text-secondary-c mt-4 mb-0">No value yet. Look it up to see what similar cards are listed for.</p>
              </div>

              <!-- Asking price per selected grade -->
              <template v-if="byGrade.some((r) => r.snap)">
                <v-divider />
                <div class="px-5 pt-4 pb-2 text-body-2 font-weight-medium">Asking prices by grade</div>
                <v-table density="compact" class="grade-table">
                  <thead><tr><th>Grade</th><th class="text-right">Median</th><th class="text-right d-none d-sm-table-cell">Range</th><th class="text-right">Listings</th></tr></thead>
                  <tbody>
                    <tr v-for="r in byGrade" :key="r.grade">
                      <td class="text-no-wrap"><v-icon v-if="r.grade === ownGrade" icon="mdi-star" size="12" class="mr-1" />{{ r.grade }}</td>
                      <template v-if="r.snap">
                        <td class="text-right num font-weight-semibold"><PriceLink :value="r.snap.median" :listing="r.snap.price_refs?.median" label="Closest to the median" /></td>
                        <td class="text-right num text-no-wrap d-none d-sm-table-cell"><template v-if="r.snap.sample_size"><PriceLink :value="r.snap.low" :listing="r.snap.price_refs?.low" label="Lowest" /> – <PriceLink :value="r.snap.high" :listing="r.snap.price_refs?.high" label="Highest" /></template><template v-else>—</template></td>
                        <td class="text-right text-no-wrap">
                          <v-chip v-if="r.snap.sample_size === 0" size="x-small" variant="tonal">No listings</v-chip>
                          <template v-else>
                            <v-chip v-if="lowConfidence(r.snap.sample_size)" size="x-small" color="warning" variant="tonal" class="mr-2">Low confidence</v-chip>
                            <span class="num">{{ r.snap.sample_size }}</span>
                          </template>
                        </td>
                      </template>
                      <td v-else colspan="3" class="text-right text-muted-c text-caption">Not priced yet. Press Look up value.</td>
                    </tr>
                  </tbody>
                </v-table>
              </template>

              <!-- Top 4 eBay listings per selected grade -->
              <template v-for="r in byGrade.filter((x) => x.snap?.top_listings?.length)" :key="'top-' + r.grade">
                <v-divider />
                <div class="px-5 pt-4 pb-2 d-flex flex-wrap align-baseline justify-space-between ga-x-2">
                  <span class="text-body-2 font-weight-medium">Top eBay listings<template v-if="selectedGrades.length > 1"> · {{ r.grade }}</template></span>
                  <span class="text-caption text-muted-c truncate" style="max-width: 100%">
                    <template v-if="r.snap?.source === 'ebay-image'">Closest to your photo<template v-if="r.snap?.parallel"> · {{ r.snap.parallel }}</template></template>
                    <template v-else>Best matches for “{{ r.snap?.query }}”</template>
                  </span>
                </div>
                <div class="top-grid px-5 pb-4">
                  <a v-for="l in (r.snap?.top_listings ?? []).slice(0, 4)" :key="l.item_id" :href="l.url || undefined" target="_blank" rel="noopener" class="top-item">
                    <div class="top-img">
                      <img v-if="l.image_url" :src="l.image_url" alt="" loading="lazy" />
                      <v-icon v-else icon="mdi-image-off-outline" />
                    </div>
                    <div class="top-title">{{ l.title }}</div>
                    <div class="d-flex align-baseline justify-space-between ga-2">
                      <strong class="num">{{ money(l.price) }}</strong>
                      <span class="text-caption text-muted-c truncate">{{ l.condition || '' }}</span>
                    </div>
                  </a>
                </div>
              </template>

              <!-- Asking vs sold trend -->
              <v-divider />
              <div class="px-5 pt-4 pb-2 d-flex align-center justify-space-between flex-wrap ga-2">
                <span class="text-body-2 font-weight-medium">Price trend</span>
                <v-btn-toggle v-model="range" mandatory density="compact" variant="outlined" divided rounded="lg" color="primary" aria-label="Time range">
                  <v-btn v-for="r in RANGES" :key="r.value" :value="r.value" size="small">{{ r.label }}</v-btn>
                </v-btn-toggle>
              </div>
              <div class="px-5 pb-4">
                <TrendChart :asking="trend?.asking ?? []" :sold="trend?.sold ?? []" :sold-source="trend?.sold_source ?? null" :sales="trend?.sales ?? []" :slots="slots" :loading="trendLoading" />
                <p v-if="selectedGrades.length > MAX_PLOTTED" class="text-caption text-muted-c mt-2 mb-0">The chart shows the first {{ MAX_PLOTTED }} grades you selected.</p>
                <p v-if="trend?.first_recorded" class="text-caption text-muted-c mt-2 mb-0">
                  History starts {{ trend.first_recorded }}. Cards are re-priced about once a day.
                </p>
              </div>

              <!-- Every price, asking vs sold -->
              <v-divider />
              <div class="px-5 pt-4 pb-2 d-flex align-center justify-space-between flex-wrap ga-2">
                <span class="text-body-2 font-weight-medium">Every price: asking vs sold</span>
                <v-select v-model="pointsGrade" :items="selectedGrades" :placeholder="normalizeGrade(card.grade)" density="compact" hide-details clearable style="max-width: 180px" aria-label="Grade for this chart" />
              </div>
              <div class="px-5 pb-4"><PriceScatter :data="points" /></div>

              <!-- Asking vs sold -->
              <template v-if="sold && (sold.provider.configured || sold.grades.some((g) => g.sold_count || g.yours_count) || sold.rows.length)">
                <v-divider />
                <div class="px-5 pt-4 pb-1 d-flex align-center justify-space-between flex-wrap ga-2">
                  <span class="text-body-2 font-weight-medium">Asking vs sold</span>
                  <div class="d-flex align-center ga-2 flex-wrap">
                    <v-btn v-if="sold.provider.configured" size="small" variant="outlined" prepend-icon="mdi-cash-check" :loading="checkingSold" @click="checkSold">Check sold prices</v-btn>
                    <v-btn-toggle v-model="soldDays" mandatory density="compact" variant="outlined" divided rounded="lg" color="primary" aria-label="Sold window" @update:model-value="loadGone">
                      <v-btn :value="30" size="small">30D</v-btn><v-btn :value="90" size="small">90D</v-btn>
                      <v-btn :value="365" size="small">1Y</v-btn><v-btn :value="0" size="small">All</v-btn>
                    </v-btn-toggle>
                  </div>
                </div>
                <p v-if="soldMsg" class="px-5 text-body-2 mb-1">{{ soldMsg }}</p>
                <p v-if="sold.provider.configured" class="px-5 text-caption text-muted-c mb-1">
                  <template v-if="sold.provider.last_checked">Sold prices last checked {{ stamp(sold.provider.last_checked) }}. </template><template v-else>Not checked yet. </template>
                  <template v-if="sold.provider.on_record">{{ sold.provider.on_record }} sales on record<template v-if="sold.provider.since"> since {{ isoDate(sold.provider.since).slice(0, 10) }}</template>, kept for your history. </template>{{ sold.provider.used }} of {{ sold.provider.budget }} monthly lookups used.
                </p>
                <v-alert v-if="sold.source === 'sold'" type="success" variant="tonal" density="compact" class="mx-5 my-2">
                  "Sold" figures are real sales reported by the sold-prices service. For Best Offer sales the price shown is the asking price, so the real sale was probably lower.
                </v-alert>
                <v-alert v-else type="info" variant="tonal" density="compact" class="mx-5 my-2">
                  <template v-if="sold.provider.configured">No real sales checked for this card yet: press "Check sold prices". Until then, </template>"Likely sold" is the last asking price of a listing that has since disappeared:
                  usually a sale, but a seller can also end or relist it. Only "Your sales" are confirmed.
                </v-alert>
                <v-table density="compact" class="grade-table">
                  <thead><tr><th>Grade</th><th class="text-right">Asking now</th><th class="text-right">Sold</th><th class="text-right">Sold vs asking</th><th class="text-right">Days up</th><th class="text-right">Your sales</th></tr></thead>
                  <tbody>
                    <tr v-for="g in sold.grades" :key="g.grade">
                      <td class="text-no-wrap">{{ g.grade }}</td>
                      <td class="text-right num">{{ money(g.asking_median) }}<span class="text-muted-c text-caption"> ({{ g.asking_count }})</span></td>
                      <td class="text-right num">{{ money(g.sold_median) }}<span v-if="g.sold_count" class="text-muted-c text-caption"> ({{ g.sold_count }}{{ g.sold_left_out > 0 ? `, ${g.sold_left_out} left out` : g.sold_source === 'estimated' ? ', likely' : '' }})</span></td>
                      <td class="text-right num text-no-wrap" :class="g.gap == null ? '' : g.gap < 0 ? 'text-error' : 'text-success'">
                        <template v-if="g.gap != null">{{ g.gap < 0 ? '−' : '+' }}{{ money(Math.abs(g.gap)) }} ({{ pctText(g.gap_pct) }})</template><template v-else>—</template>
                      </td>
                      <td class="text-right num">{{ g.days_median ?? '—' }}</td>
                      <td class="text-right num">{{ money(g.yours_median) }}<span v-if="g.yours_count" class="text-muted-c text-caption"> ({{ g.yours_count }})</span></td>
                    </tr>
                  </tbody>
                </v-table>
                <div v-if="sold.rows.length" class="sold-scroll"><DataTable :headers="soldHeaders" :items="sold.rows" :sort-by="SOLD_SORT" search-placeholder="Search sold listings">
                  <template #item.image_url="{ item }"><ListingThumb :src="item.image_url" :url="item.url" :title="item.title" /></template>
                  <template #item.gone_at="{ item }"><span class="num text-no-wrap">{{ item.gone_at ? isoDate(item.gone_at).slice(0, 10) : '' }}</span></template>
                  <template #item.parallel="{ item }"><v-chip size="x-small" variant="tonal">{{ item.parallel }}</v-chip></template>
                  <template #item.title="{ item }"><span class="truncate d-block gone-title">{{ item.title }}</span></template>
                  <template #item.days_listed="{ item }"><span class="num">{{ item.days_listed ?? '—' }}</span></template>
                  <template #item.price="{ item }">
                    <PriceLink :value="item.price" :listing="{ item_id: item.item_id, title: item.title, price: item.price, currency: item.currency, condition: null, url: item.url }" :label="item.source === 'sold' ? 'Sold' : item.verdict === 'out_of_stock' ? 'Sold out' : 'No longer on eBay'" />
                    <v-chip v-if="item.best_offer" size="x-small" variant="tonal" color="warning" class="ml-1" title="Best Offer sale: this is the asking price, so the sale was probably lower">best offer</v-chip>
                  </template>
                  <template #item.vs_asking_pct="{ item }"><span class="num" :class="(item.vs_asking_pct ?? 0) < 0 ? 'text-error' : 'text-success'">{{ pctText(item.vs_asking_pct) || '—' }}</span></template>
                </DataTable></div>
                <div class="pb-3" />
              </template>

              <template v-if="matches.length">
                <v-divider />
                <div class="px-5 pt-4 pb-2 d-flex flex-wrap align-center justify-space-between ga-2">
                  <span class="text-body-2 font-weight-medium">Matching listings</span>
                  <v-btn size="small" variant="outlined" prepend-icon="mdi-palette-swatch-outline" :loading="busy === 'rank'" :disabled="!card.image_url" @click="rankByLook">Rank by look-alike</v-btn>
                </div>
                <p class="text-caption text-muted-c px-5 mb-2">
                  Untick a listing that isn't your card and the prices update straight away, and stay that way after each refresh.
                  The box at the top of the Use column ticks or unticks every listing shown, including after searching.
                  <template v-if="card.parallel"><v-switch v-model="showAllVersions" density="compact" hide-details color="primary" :label="`Show other versions too (not just ${card.parallel})`" class="mt-1" /></template>
                </p>
                <DataTable
                  v-model:search="compSearch" v-model:sort-by="compSort" v-model:page="compPage" v-model:items-per-page="compPerPage"
                  :headers="compHeaders" :items="versionComps" :total="matches.length"
                  search-placeholder="Search listings" empty-text="No listings for this version." class="comp-table"
                >
                  <!-- The empty listener keeps the dash until the listings reload -->
                  <template #header.included>
                    <v-checkbox-btn :model-value="shownTicked > 0" class="use-all" :indeterminate="shownTicked > 0 && shownTicked < shownComps.length" density="compact" aria-label="Use all shown listings in the prices" :disabled="busy === 'exclude' || !shownComps.length" @update:model-value="toggleAllShown" @update:indeterminate="() => {}" />
                  </template>
                  <template #item.included="{ item }">
                    <v-checkbox-btn :model-value="item.included" density="compact" :aria-label="`Use this listing in the prices: ${item.title}`" :disabled="busy === 'exclude'" @update:model-value="toggleInclude(item)" />
                  </template>
                  <template #item.image_url="{ item }">
                    <ListingThumb :src="item.image_url" :url="item.url" :title="item.title" :dim="!item.included" />
                  </template>
                  <template #item.title="{ item }">
                    <a v-if="item.url" :href="item.url" target="_blank" rel="noopener" class="comp-link comp-title" :class="{ dim: !item.included }">{{ item.title }}</a>
                    <span v-else class="comp-title" :class="{ dim: !item.included }">{{ item.title }}</span>
                    <div v-if="xs" class="text-caption text-muted-c">{{ [item.grade, item.parallel !== 'Base' ? item.parallel : null, item.condition].filter(Boolean).join(' · ') }}</div>
                  </template>
                  <template #item.in_window="{ item }"><v-chip v-if="item.in_window" size="x-small" color="primary" variant="tonal">In the price</v-chip></template>
                  <template #item.grade="{ item }"><v-chip size="x-small" variant="tonal">{{ item.grade }}</v-chip></template>
                  <template #item.parallel="{ item }"><v-chip size="x-small" variant="outlined" :color="item.parallel === card.parallel ? 'primary' : undefined">{{ item.parallel }}</v-chip></template>
                  <template #item.look="{ item }"><span v-if="item.look != null" class="num text-caption">{{ item.look }}%</span><span v-else>—</span></template>
                  <template #item.price="{ item }"><span class="num text-no-wrap font-weight-semibold" :class="{ dim: !item.included }">{{ money(item.price) }}</span></template>
                  <template #item.vs="{ item }">
                    <span v-if="vsMedian(item) !== null" class="num text-no-wrap text-caption" :class="vsMedian(item)! > 0 ? 'text-muted-c' : 'text-success'">{{ vsMedian(item)! > 0 ? '+' : '' }}{{ vsMedian(item) }}%</span>
                    <span v-else>—</span>
                  </template>
                </DataTable>
              </template>
            </v-window-item>

            <!-- Sell -->
            <v-window-item value="sell">
              <div class="card-body">
                <template v-if="card.status === 'in_stock'">
                  <v-row dense>
                    <v-col cols="12">
                      <v-text-field v-model="listTitle" label="Listing title" maxlength="80" counter="80" hide-details="auto" />
                    </v-col>
                    <v-col cols="12" sm="4">
                      <v-text-field v-model.number="listPrice" label="Price" prefix="$" type="number" min="0.99" step="0.01" inputmode="decimal" />
                    </v-col>
                    <v-col cols="12" sm="8" class="d-flex align-center">
                      <v-btn color="primary" variant="flat" prepend-icon="mdi-upload-outline" :loading="busy === 'list'" :disabled="!listPrice || !listTitle" @click="list">
                        List on eBay
                      </v-btn>
                    </v-col>
                  </v-row>
                </template>
                <v-alert v-else type="info" variant="tonal" density="compact" :text="card.status === 'listed' ? 'This card is listed. Mark it sold once it ships.' : 'This card is sold. Return it to stock to list it again.'" />
              </div>
              <template v-if="listings.length">
                <v-divider />
                <div class="px-5 pt-4 pb-2 text-body-2 font-weight-medium">Listings</div>
                <v-list density="comfortable" class="py-0">
                  <v-list-item v-for="l in listings" :key="l.id" :href="l.url || undefined" :target="l.url ? '_blank' : undefined" prepend-icon="mdi-tag-outline">
                    <v-list-item-title>{{ l.title }}</v-list-item-title>
                    <v-list-item-subtitle class="num">{{ isoDate(l.created_at) }} · SKU {{ l.sku }}</v-list-item-subtitle>
                    <template #append>
                      <span class="num font-weight-semibold mr-3">{{ money(l.price) }}</span>
                      <v-chip size="small" variant="tonal" class="text-capitalize">{{ l.status }}</v-chip>
                    </template>
                  </v-list-item>
                </v-list>
              </template>
            </v-window-item>

            <!-- History -->
            <v-window-item value="history">
              <template v-if="sales.length">
                <div class="px-5 pt-4 pb-2 text-body-2 font-weight-medium">Your sales</div>
                <v-table density="compact" class="grade-table">
                  <thead><tr><th>Date</th><th>Grade</th><th>Note</th><th class="text-right">Price</th><th /></tr></thead>
                  <tbody>
                    <tr v-for="x in sales" :key="x.id">
                      <td class="num text-no-wrap">{{ isoDate(x.sold_at).slice(0, 10) }}</td>
                      <td class="text-no-wrap">{{ x.grade || '—' }}</td>
                      <td>{{ x.note || '' }}</td>
                      <td class="text-right num font-weight-semibold">{{ money(x.price) }}</td>
                      <td class="text-right"><v-btn icon="mdi-delete-outline" variant="text" size="small" color="error" :aria-label="`Delete the ${money(x.price)} sale`" @click="removeSale(x.id)" /></td>
                    </tr>
                  </tbody>
                </v-table>
                <v-divider class="mt-2" />
                <div class="px-5 pt-4 pb-2 text-body-2 font-weight-medium">Price lookups</div>
              </template>
              <DataTable :headers="historyHeaders" :items="history" search-placeholder="Search price lookups" empty-text="No price lookups yet.">
                <template #item.created_at="{ item }"><span class="num">{{ isoDate(item.created_at) }}</span></template>
                <template #item.grade="{ item }">{{ item.grade || 'All' }}</template>
                <template #item.low="{ item }"><span class="num"><PriceLink :value="item.low" :listing="item.price_refs?.low" label="Lowest" /></span></template>
                <template #item.median="{ item }"><span class="num font-weight-semibold"><PriceLink :value="item.median" :listing="item.price_refs?.median" label="Closest to the median" /></span></template>
                <template #item.high="{ item }"><span class="num"><PriceLink :value="item.high" :listing="item.price_refs?.high" label="Highest" /></span></template>
              </DataTable>
            </v-window-item>
          </v-window>
        </v-card>
        <PriceTrace v-if="debug.on" :card-id="id" :version="String(card.priced_at) + card.updated_at" class="mt-4" />
      </v-col>
    </v-row>

    <v-dialog v-model="sellOpen" max-width="440">
      <v-card>
        <div class="card-head"><div><h2><v-icon icon="mdi-check-circle-outline" size="18" />Mark as sold</h2><p>What did you actually get? It goes on the price chart.</p></div></div>
        <form class="card-body d-grid" @submit.prevent="recordSale">
          <v-text-field v-model.number="sellPrice" label="Sale price" prefix="$" type="number" min="0.01" step="0.01" inputmode="decimal" autofocus />
          <v-text-field v-model="sellDate" label="Sold on" type="date" :max="todayISO()" />
          <v-text-field v-model="sellNote" label="Note (optional)" placeholder="eBay auction, trade show, local sale…" maxlength="500" />
          <v-card-actions class="px-0 pb-0">
            <v-spacer />
            <v-btn variant="text" @click="sellOpen = false">Cancel</v-btn>
            <v-btn type="submit" color="primary" variant="flat" :disabled="!sellPrice || sellPrice <= 0 || !sellDate" :loading="busy === 'sale'">Mark sold</v-btn>
          </v-card-actions>
        </form>
      </v-card>
    </v-dialog>

    <v-dialog v-model="confirmDelete" max-width="420">
      <v-card>
        <div class="card-head"><h2><v-icon icon="mdi-alert-outline" color="error" size="18" />Delete card</h2></div>
        <div class="card-body">Delete <strong>{{ card.name }}</strong> from inventory? This can't be undone.</div>
        <v-card-actions class="px-4 pb-4">
          <v-spacer />
          <v-btn variant="text" @click="confirmDelete = false">Cancel</v-btn>
          <v-btn color="error" variant="flat" :loading="busy === 'delete'" @click="remove">Delete</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>

  <div v-else>
    <v-alert v-if="error" type="error" variant="tonal" border="start" :text="error" />
    <v-row v-else>
      <v-col cols="12" md="4" lg="3"><v-skeleton-loader type="image, list-item-two-line@3" /></v-col>
      <v-col cols="12" md="8" lg="9"><v-skeleton-loader type="heading, table" /></v-col>
    </v-row>
  </div>
</template>

<style scoped>
.side { display: grid; gap: 16px; position: sticky; top: 84px; }
@media (max-width: 959px) { .side { position: static; } }
@media (max-width: 959px) { .photo { max-width: 240px; margin: 0 auto; } }
.facts { margin: 0; padding: 6px 18px 14px; display: grid; }
.facts > div { display: flex; justify-content: space-between; gap: 12px; padding: 9px 0; border-bottom: 1px solid rgb(var(--v-theme-border)); font-size: 13.5px; }
.facts > div:last-child { border-bottom: 0; }
.facts dt { color: rgb(var(--v-theme-text-secondary)); flex-shrink: 0; }
.facts dd { margin: 0; font-weight: 500; text-align: right; overflow-wrap: anywhere; }
.facts .wide { flex-direction: column; }
.facts .wide dd { text-align: left; font-weight: 400; }
.tabs { background: rgb(var(--v-theme-surface-chrome)); }
.footer-actions { display: flex; gap: 8px; padding: 12px 18px; background: rgb(var(--v-theme-surface-chrome)); }
.font-weight-semibold { font-weight: 600; }
.comp-link { color: rgb(var(--v-theme-primary)); text-decoration: none; }
.comp-title { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; max-width: 460px; white-space: normal; }
.dim { opacity: 0.4; text-decoration: line-through; }
.comp-link:hover { text-decoration: underline; }
/* The header cell is grey; the tick-all box matches the row boxes. */
.use-all { color: rgba(var(--v-theme-on-surface), var(--v-high-emphasis-opacity)); }
.grade-table { background: transparent !important; }
.d-grid { display: grid; gap: 14px; }
.gone-title { max-width: 320px; }
.kpi-wrap { position: relative; }
.kpi-bug { position: absolute; top: 10px; right: 14px; }
.sold-scroll { overflow-x: auto; }
.sold-scroll .gone-title { max-width: 190px; }
.grade-table :deep(th) { font-size: 11.5px !important; text-transform: uppercase; letter-spacing: 0.04em; color: rgb(var(--v-theme-text-muted)) !important; }
.top-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 700px) { .top-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.top-item { display: grid; gap: 6px; align-content: start; color: inherit; text-decoration: none; padding: 8px; border-radius: 10px; border: 1px solid rgb(var(--v-theme-border)); transition: border-color 0.15s; }
.top-item:hover, .top-item:focus-visible { border-color: rgb(var(--v-theme-primary)); }
.top-img { aspect-ratio: 1; border-radius: 6px; overflow: hidden; display: grid; place-items: center; background: rgb(var(--v-theme-surface-sunken)); color: rgb(var(--v-theme-text-muted)); }
.top-img img { width: 100%; height: 100%; object-fit: cover; }
.top-title { font-size: 12.5px; line-height: 1.35; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; min-height: 2.7em; }
</style>
