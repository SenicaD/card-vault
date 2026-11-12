<script setup lang="ts">
import type { Decision, Trace, TraceFunnel, TraceSummary } from '~/types'

// "How was this price calculated": the searches made, what happened to every listing, and the result.
const props = defineProps<{ cardId: number; version?: string | null }>()
const { api } = useApi()

const list = ref<TraceSummary[]>([])
const pick = ref<number | null>(null)
const trace = ref<Trace | null>(null)
const grade = ref<string | null>(null)
const only = ref<Decision | 'all'>('all')
const error = ref('')
const loading = ref(false)
const tab = ref('price')
const compareIds = ref<[number, number] | null>(null)

async function loadList() {
  try {
    list.value = await api<TraceSummary[]>(`/api/cards/${props.cardId}/debug/traces`)
    if (!list.value.some((t) => t.id === pick.value)) pick.value = list.value[0]?.id ?? null
    else await loadTrace()
  } catch (e: any) { error.value = e.message }
}
async function loadTrace() {
  if (pick.value == null) { trace.value = null; return }
  loading.value = true
  try {
    trace.value = await api<Trace>(`/api/cards/${props.cardId}/debug/traces/${pick.value}`)
    if (!trace.value.funnel?.some((f) => f.grade === grade.value)) grade.value = trace.value.funnel?.[0]?.grade ?? null
  } catch (e: any) { error.value = e.message } finally { loading.value = false }
}
onMounted(loadList)
watch(() => props.version, loadList)
watch(pick, () => { loadTrace(); compareIds.value = null })

const items = computed(() => list.value.map((t) => ({
  value: t.id, title: `${stamp(t.created_at)} · ${TRIGGER_LABEL[t.trigger ?? ''] ?? t.trigger ?? ''}${t.error ? ' · FAILED' : ''}`,
})))
const fun = computed<TraceFunnel | null>(() => trace.value?.funnel?.find((f) => f.grade === grade.value) ?? null)
const gradeListings = computed(() => (trace.value?.listings ?? []).filter((l) => l.g === grade.value))
const rows = computed(() => gradeListings.value.filter((l) => only.value === 'all' || l.d === only.value))
const headers = [
  { title: 'Treated as', key: 'd', width: 170 }, { title: 'Version', key: 'v' }, { title: 'Listing', key: 't', sortable: false },
  { title: 'Price', key: 'p', align: 'end' as const },
]
const decisionItems = computed(() => [{ value: 'all', title: 'All listings' },
  ...Object.entries(DECISION_LABEL).map(([value, title]) => ({ value, title }))])
const idx = computed(() => list.value.findIndex((t) => t.id === pick.value))
const previous = computed(() => list.value.slice(idx.value + 1).find((t) => t.has_listings))
function compareWithPrevious() {
  if (previous.value && pick.value != null) { compareIds.value = [previous.value.id, pick.value]; tab.value = 'compare' }
}
const modeText = (m: string | null) => (m === 'ebay-image' ? 'the card photo' : m === 'ebay-active' ? 'the search words' : m ?? '—')
const total = computed(() => trace.value?.summary?.after_top_up ?? trace.value?.summary?.returned ?? 0)
</script>

<template>
  <v-card id="debug-panel" class="debug-card">
    <div class="card-head">
      <div>
        <h2><v-icon icon="mdi-bug-outline" size="18" />How this price was calculated</h2>
        <p>Every search, and what happened to every listing, behind the numbers on this page.</p>
      </div>
      <v-select v-if="list.length" v-model="pick" :items="items" density="compact" hide-details style="max-width: 320px" aria-label="Which lookup" />
    </div>
    <div class="card-body">
      <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-3" :text="error" />
      <p v-if="!list.length && !error" class="text-body-2 text-muted-c mb-0">No lookups recorded for this card yet. Press "Look up value" and the steps will appear here.</p>

      <template v-if="trace">
        <v-alert v-if="trace.error" type="error" variant="tonal" density="compact" class="mb-3">
          This lookup failed: {{ trace.error }}
        </v-alert>
        <p class="text-body-2 mb-3">
          {{ stamp(trace.created_at) }} · {{ TRIGGER_LABEL[trace.trigger ?? ''] ?? trace.trigger }} · searched by <strong>{{ modeText(trace.mode) }}</strong>
          <template v-if="trace.query"> (words: "{{ trace.query }}")</template>
          <template v-if="trace.parallel"> · version <strong>{{ trace.parallel }}</strong> only</template>
          <template v-if="trace.duration_ms != null"> · {{ trace.duration_ms }} ms</template>
        </p>
        <v-alert v-for="n in trace.notes ?? []" :key="n" type="info" variant="tonal" density="compact" class="mb-2" :text="n" />

        <v-tabs v-model="tab" density="compact" class="mb-3">
          <v-tab value="price">Price steps</v-tab>
          <v-tab value="calls">Searches ({{ trace.calls?.length ?? 0 }})</v-tab>
          <v-tab value="compare" :disabled="!previous && !compareIds">Compare</v-tab>
          <v-tab value="ident">Photo &amp; identification</v-tab>
        </v-tabs>

        <v-window v-model="tab">
          <v-window-item value="price">
            <div v-if="trace.funnel?.length">
              <div class="text-body-2 mb-1">From <strong class="num">{{ total }}</strong> listings eBay returned, to the price:</div>
              <v-table density="compact" class="funnel mb-3">
                <thead><tr><th>Grade</th><th class="text-right">Found</th><th class="text-right">You unticked</th><th class="text-right">Other versions</th><th class="text-right">Outliers</th><th class="text-right">Used</th><th class="text-right">Low</th><th class="text-right">Median</th><th class="text-right">High</th></tr></thead>
                <tbody>
                  <tr v-for="f in trace.funnel" :key="f.grade" :class="{ on: f.grade === grade }" @click="grade = f.grade">
                    <td>{{ f.grade }}</td><td class="text-right num">{{ f.found }}</td><td class="text-right num">{{ f.excluded || '' }}</td>
                    <td class="text-right num">{{ f.other_version || '' }}</td><td class="text-right num">{{ f.outliers || '' }}</td>
                    <td class="text-right num font-weight-bold">{{ f.used }}</td><td class="text-right num">{{ money(f.low) }}</td>
                    <td class="text-right num font-weight-bold">{{ money(f.median) }}</td><td class="text-right num">{{ money(f.high) }}</td>
                  </tr>
                </tbody>
              </v-table>
              <p class="text-caption text-muted-c">
                Also set aside before grading: {{ trace.summary?.junk ?? 0 }} lots, bundles or reprints, and {{ trace.summary?.unknown_grade ?? 0 }} whose title doesn't say a grade.
                The price is the <strong>median</strong> of the "Used" listings. Click a grade to see its listings.
              </p>
            </div>

            <template v-if="trace.listings && fun">
              <div class="text-body-2 font-weight-medium mt-3 mb-1">{{ grade }}: where each listing sits</div>
              <PriceStrip :listings="gradeListings" :funnel="fun" />
              <p v-if="fun.spread != null" class="text-caption text-muted-c mt-1 mb-2">
                Spread {{ Math.round(fun.spread * 100) }}% of the median (the width of the middle half of the prices).
                <template v-if="fun.spread > 0.6">That is wide: the listings are probably several different cards or versions.</template>
              </p>
              <div class="d-flex align-center ga-2 mb-1 mt-2 flex-wrap">
                <span class="text-body-2 font-weight-medium">Listings ({{ rows.length }})</span>
                <v-select v-model="only" :items="decisionItems" density="compact" hide-details style="max-width: 220px" aria-label="Show" />
              </div>
              <v-data-table :headers="headers" :items="rows" density="compact" :items-per-page="10" :sort-by="[{ key: 'p', order: 'asc' }]" class="no-pointer">
                <template #item.d="{ item }">
                  <v-chip size="x-small" :color="DECISION_COLOR[item.d]" variant="tonal">{{ DECISION_LABEL[item.d] }}</v-chip>
                  <v-chip v-if="item.r" size="x-small" color="primary" variant="flat" class="ml-1">{{ item.r === 'median' ? 'median' : item.r === 'low' ? 'lowest' : 'highest' }}</v-chip>
                </template>
                <template #item.v="{ item }"><span class="text-caption">{{ item.v }}</span></template>
                <template #item.t="{ item }"><a :href="item.u || undefined" target="_blank" rel="noopener" class="tlink">{{ item.t }}</a></template>
                <template #item.p="{ item }"><span class="num">{{ money(item.p) }}</span></template>
              </v-data-table>
            </template>
            <p v-else-if="trace.funnel?.length" class="text-caption text-muted-c mt-2">The listing-by-listing table is only kept for each card's 3 newest lookups.</p>
            <div class="mt-3 d-flex ga-2 flex-wrap">
              <v-btn v-if="trace.has_raw" size="small" variant="outlined" prepend-icon="mdi-download-outline" :href="`/api/cards/${cardId}/debug/traces/${trace.id}/raw`">Download the raw eBay data</v-btn>
              <v-btn v-if="previous" size="small" variant="outlined" prepend-icon="mdi-compare-horizontal" @click="compareWithPrevious">Compare with the previous lookup</v-btn>
            </div>
          </v-window-item>

          <v-window-item value="calls">
            <v-table density="compact">
              <thead><tr><th>Why</th><th>Search</th><th>What was searched</th><th class="text-right">Results</th><th class="text-right">Time</th></tr></thead>
              <tbody>
                <tr v-for="(c, i) in trace.calls ?? []" :key="i">
                  <td>{{ c.purpose }}</td><td>{{ c.api }}</td><td class="detail">{{ c.detail }}<div v-if="c.error" class="text-error text-caption">{{ c.error }}</div></td>
                  <td class="text-right num">{{ c.returned ?? '—' }}</td><td class="text-right num">{{ c.ms }} ms</td>
                </tr>
              </tbody>
            </v-table>
          </v-window-item>

          <v-window-item value="compare"><TraceCompare :card-id="cardId" :trace-ids="compareIds" /></v-window-item>
          <v-window-item value="ident"><IdentTrace :card-id="cardId" :version="version" /></v-window-item>
        </v-window>
      </template>
    </div>
  </v-card>
</template>

<style scoped>
.debug-card { border-style: dashed !important; }
.funnel tbody tr { cursor: pointer; }
.funnel tbody tr.on { background: rgb(var(--v-theme-brand-muted)); }
.detail { max-width: 340px; overflow-wrap: anywhere; font-size: 13px; }
.tlink { color: inherit; text-decoration: none; display: block; max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tlink:hover { color: rgb(var(--v-theme-primary)); }
</style>
