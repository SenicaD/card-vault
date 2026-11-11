<script setup lang="ts">
import { useDisplay } from 'vuetify'
import { CATEGORY_LABELS, type Card, type CardFields, type EbayItem, type Listing, type Location, type PriceResult, type PriceSnapshot, type Trend, type TrendRange } from '~/types'

const { api } = useApi()
const { xs } = useDisplay()
const route = useRoute()
const id = Number(route.params.id)

const card = ref<Card | null>(null)
const form = ref<CardFields | null>(null)
const locations = ref<Location[]>([])
const history = ref<PriceSnapshot[]>([])
const comps = ref<EbayItem[]>([])
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
const latest = computed(() => history.value.find((s) => s.grade === ownGrade.value) ?? history.value[0] ?? null)

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
  grade, snap: history.value.find((s) => s.source === 'ebay-active' && s.grade === grade) ?? null,
})))
const lowConfidence = (n: number) => n > 0 && n < 5

const RANGES: { value: TrendRange; label: string }[] = [
  { value: '7d', label: '7D' }, { value: '1m', label: '1M' }, { value: '3m', label: '3M' },
  { value: '1y', label: '1Y' }, { value: 'all', label: 'All' },
]
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
  const { category, name, set_name, year, card_number, variant, grade, condition, quantity, location_id, slot, notes, search_query } = c
  return { category, name, set_name, year, card_number, variant, grade, condition, quantity, location_id, slot, notes, search_query }
}

function defaultTitle(c: Card) {
  return [c.year, c.set_name, c.name, c.card_number && '#' + c.card_number.replace(/^#/, ''), c.variant,
    c.grade && c.grade.toLowerCase() !== 'raw' ? c.grade : null].filter(Boolean).join(' ').slice(0, 80)
}

async function load() {
  try {
    ;[card.value, locations.value, history.value, listings.value] = await Promise.all([
      api<Card>(`/api/cards/${id}`),
      api<Location[]>('/api/locations'),
      api<PriceSnapshot[]>(`/api/cards/${id}/prices`),
      api<Listing[]>(`/api/cards/${id}/listings`),
    ])
    form.value = fieldsOf(card.value)
    listTitle.value = defaultTitle(card.value)
    listPrice.value = card.value.est_value
    if (!selectedGrades.value.length) selectedGrades.value = [normalizeGrade(card.value.grade)]
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
  card.value = await api<Card>(`/api/cards/${id}`, { method: 'PATCH', body: form.value })
  form.value = fieldsOf(card.value)
  listTitle.value = defaultTitle(card.value)
  editing.value = false
})

function cancelEdit() {
  if (card.value) form.value = fieldsOf(card.value)
  editing.value = false
}

const lookUp = () => run('price', async () => {
  const res = await api<PriceResult>(`/api/cards/${id}/price`, {
    method: 'POST', body: { query: form.value?.search_query || null, grades: selectedGrades.value },
  })
  comps.value = res.comps
  gradeCounts.value = res.grade_counts
  history.value = [...res.snapshots, ...history.value]
  card.value = await api<Card>(`/api/cards/${id}`)
  if (!listPrice.value) listPrice.value = res.snapshot.median
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

const compHeaders = computed(() => [
  { title: 'Listing', key: 'title' },
  ...(xs.value ? [] : [{ title: 'Condition', key: 'condition' }]),
  { title: 'Price', key: 'price', align: 'end' as const },
])
const historyHeaders = [
  { title: 'Date (UTC)', key: 'created_at' },
  { title: 'Grade', key: 'grade' },
  { title: 'Source', key: 'source' },
  { title: 'Samples', key: 'sample_size', align: 'end' as const },
  { title: 'Low', key: 'low', align: 'end' as const },
  { title: 'Median', key: 'median', align: 'end' as const },
  { title: 'High', key: 'high', align: 'end' as const },
]

onMounted(() => {
  try {
    const saved = localStorage.getItem('cv-trend-range') as TrendRange | null
    if (saved && RANGES.some((r) => r.value === saved)) range.value = saved
  } catch {}
  load() // selects the card's own grade, which loads the trend
})
</script>

<template>
  <div v-if="card && form">
    <PageHeader :title="card.name" :subtitle="subline" :crumbs="[{ title: 'Inventory', to: '/' }, { title: card.name }]">
      <template #actions>
        <StatusPill :status="card.status" size="default" />
        <v-btn v-if="card.status !== 'sold'" variant="outlined" prepend-icon="mdi-check-circle-outline" :loading="busy === 'status'" @click="setStatus('sold')">Mark sold</v-btn>
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
            <div class="card-body photo"><CardThumb :src="card.image_url" :alt="card.name" :foil="!!card.est_value && card.est_value >= 100" /></div>
          </v-card>

          <v-card>
            <div class="card-head">
              <h2><v-icon icon="mdi-information-outline" size="18" />Details</h2>
              <v-btn v-if="!editing" variant="text" size="small" prepend-icon="mdi-pencil-outline" @click="editing = true">Edit</v-btn>
            </div>
            <dl class="facts">
              <div><dt>Type</dt><dd>{{ CATEGORY_LABELS[card.category] }}</dd></div>
              <div><dt>Stored in</dt><dd>{{ locName || 'Unassigned' }}<span v-if="card.slot" class="text-muted-c">, {{ card.slot }}</span></dd></div>
              <div><dt>Grade</dt><dd>{{ card.grade || '—' }}<span v-if="card.condition" class="text-muted-c"> · {{ card.condition }}</span></dd></div>
              <div><dt>Quantity</dt><dd class="num">{{ card.quantity }}</dd></div>
              <div v-if="card.id_confidence != null"><dt>ID confidence</dt><dd class="num">{{ Math.round(card.id_confidence * 100) }}%</dd></div>
              <div><dt>Added</dt><dd class="num">{{ isoDate(card.created_at) }}</dd></div>
              <div><dt>Updated</dt><dd class="num">{{ isoDate(card.updated_at) }}</dd></div>
              <div v-if="card.notes" class="wide"><dt>Notes</dt><dd>{{ card.notes }}</dd></div>
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
          <v-col cols="6" sm="3"><StatCard label="Estimate" :value="money(card.est_value)" icon="mdi-currency-usd" color="primary" :sub="card.priced_at ? `Priced ${isoDate(card.priced_at).slice(0, 10)}` : 'Not priced'" /></v-col>
          <v-col cols="6" sm="3"><StatCard label="Low" :value="money(latest?.low)" icon="mdi-arrow-down" color="info" /></v-col>
          <v-col cols="6" sm="3"><StatCard label="High" :value="money(latest?.high)" icon="mdi-arrow-up" color="warning" /></v-col>
          <v-col cols="6" sm="3"><StatCard label="Sample size" :value="latest?.sample_size ?? '—'" icon="mdi-chart-box-outline" color="success" :sub="latest?.source === 'ebay-image' ? 'Image matches' : 'Active listings'" /></v-col>
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

                <v-alert v-if="latest" type="info" variant="tonal" density="compact" class="mt-4" icon="mdi-information-outline">
                  {{ latest.grade || 'All grades' }}: {{ latest.sample_size }} {{ latest.source === 'ebay-image' ? 'listings matched by photo' : 'active listings' }} at {{ isoDate(latest.created_at) }}. Asking prices run higher than sold prices.
                </v-alert>
                <p v-else class="text-body-2 text-secondary-c mt-4 mb-0">No value yet. Look it up to see what similar cards are listed for.</p>
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
                  <span class="text-caption text-muted-c truncate" style="max-width: 100%">Best matches for “{{ r.snap?.query }}”</span>
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
                <TrendChart :asking="trend?.asking ?? []" :sold="trend?.sold ?? []" :sold-available="!!trend?.sold_available" :slots="slots" :loading="trendLoading" />
                <p v-if="selectedGrades.length > MAX_PLOTTED" class="text-caption text-muted-c mt-2 mb-0">The chart shows the first {{ MAX_PLOTTED }} grades you selected.</p>
                <p v-if="trend?.first_recorded" class="text-caption text-muted-c mt-2 mb-0">
                  History starts {{ trend.first_recorded }}. Cards are re-priced about once a day.
                </p>
              </div>

              <template v-if="comps.length">
                <v-divider />
                <div class="px-5 pt-4 pb-2 text-body-2 font-weight-medium">Comparable listings ({{ comps.length }})</div>
                <v-data-table :headers="compHeaders" :items="comps" density="compact" items-per-page="10" class="no-pointer">
                  <template #item.title="{ item }">
                    <a v-if="item.url" :href="item.url" target="_blank" rel="noopener" class="comp-link">{{ item.title }}</a>
                    <span v-else>{{ item.title }}</span>
                  </template>
                  <template #item.condition="{ item }">{{ item.condition || '—' }}</template>
                  <template #item.price="{ item }"><span class="num text-no-wrap">{{ money(item.price) }}</span></template>
                </v-data-table>
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
              <v-data-table :headers="historyHeaders" :items="history" density="comfortable" items-per-page="10" class="no-pointer">
                <template #item.created_at="{ item }"><span class="num">{{ isoDate(item.created_at) }}</span></template>
                <template #item.grade="{ item }">{{ item.grade || 'All' }}</template>
                <template #item.low="{ item }"><span class="num"><PriceLink :value="item.low" :listing="item.price_refs?.low" label="Lowest" /></span></template>
                <template #item.median="{ item }"><span class="num font-weight-semibold"><PriceLink :value="item.median" :listing="item.price_refs?.median" label="Closest to the median" /></span></template>
                <template #item.high="{ item }"><span class="num"><PriceLink :value="item.high" :listing="item.price_refs?.high" label="Highest" /></span></template>
                <template #no-data><div class="py-6 text-secondary-c">No price lookups yet.</div></template>
              </v-data-table>
            </v-window-item>
          </v-window>
        </v-card>
      </v-col>
    </v-row>

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
.comp-link:hover { text-decoration: underline; }
.no-pointer :deep(tbody tr) { cursor: default; }
.grade-table { background: transparent !important; }
.grade-table :deep(th) { font-size: 11.5px !important; text-transform: uppercase; letter-spacing: 0.04em; color: rgb(var(--v-theme-text-muted)) !important; }
.top-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 700px) { .top-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.top-item { display: grid; gap: 6px; align-content: start; color: inherit; text-decoration: none; padding: 8px; border-radius: 10px; border: 1px solid rgb(var(--v-theme-border)); transition: border-color 0.15s; }
.top-item:hover, .top-item:focus-visible { border-color: rgb(var(--v-theme-primary)); }
.top-img { aspect-ratio: 1; border-radius: 6px; overflow: hidden; display: grid; place-items: center; background: rgb(var(--v-theme-surface-sunken)); color: rgb(var(--v-theme-text-muted)); }
.top-img img { width: 100%; height: 100%; object-fit: cover; }
.top-title { font-size: 12.5px; line-height: 1.35; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; min-height: 2.7em; }
</style>
