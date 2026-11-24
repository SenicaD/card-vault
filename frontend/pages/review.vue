<script setup lang="ts">
import type { Card, CardFields, Location, PriceResult } from '~/types'

// Step through cards that need a look, the most dollars at risk first: weak matches, unpriced cards, and cards whose
// price confidence is under the Settings page's "Review below", that you haven't marked reviewed.
const { api } = useApi()
const queue = ref<Card[]>([])
const pos = ref(0)
const locations = ref<Location[]>([])
const loading = ref(true)
const busy = ref('')
const error = ref('')
const cropOpen = ref(false)
const result = ref<PriceResult | null>(null)
const done = ref(0)

const card = computed(() => queue.value[pos.value] ?? null)
const f = reactive({ name: '', set_name: '' as string | null, year: '' as string | null, card_number: '' as string | null,
  grade: null as string | null, parallel: '' as string | null, cost: '' as string | number | null, search_query: '' as string | null,
  manual_value: '' as string | number | null })

function load(c: Card | null) {
  result.value = null
  if (!c) return
  Object.assign(f, { name: c.name, set_name: c.set_name, year: c.year, card_number: c.card_number, grade: c.grade,
    parallel: c.parallel ?? '', cost: c.cost ?? '', search_query: c.search_query, manual_value: c.manual_value ?? '' })
}

// Why the card is here: a weak identification, plus every reason its price confidence lost points
const why = computed(() => {
  const c = card.value
  if (!c) return []
  return [(c.id_confidence ?? 1) < 0.3 ? `Weak match (${Math.round((c.id_confidence ?? 0) * 100)}%)` : '',
    ...(c.confidence_reasons ?? [])].filter((w, i, all) => w && all.indexOf(w) === i)
})

onMounted(async () => {
  try {
    ;[queue.value, locations.value] = await Promise.all([
      api<Card[]>('/api/cards', { query: { needs_review: 'true', sort: 'risk' } }),
      api<Location[]>('/api/locations'),
    ])
    load(card.value)
  } catch (e: any) { error.value = e.message } finally { loading.value = false }
})

async function run(name: string, fn: () => Promise<void>) {
  busy.value = name; error.value = ''
  try { await fn() } catch (e: any) { error.value = e.message } finally { busy.value = '' }
}
const body = (): Partial<CardFields> & { parallel?: string | null } => ({
  name: f.name, set_name: f.set_name || null, year: f.year || null, card_number: f.card_number || null,
  grade: f.grade || null, parallel: f.parallel?.trim() || null, search_query: f.search_query || null,
  cost: f.cost === '' || f.cost == null ? null : Number(f.cost),
  manual_value: f.manual_value === '' || f.manual_value == null ? null : Number(f.manual_value),
})
function replaceCurrent(c: Card) { queue.value = queue.value.map((x) => (x.id === c.id ? c : x)); load(c) }

const lookUp = () => run('price', async () => {
  const c = card.value!
  replaceCurrent(await api<Card>(`/api/cards/${c.id}`, { method: 'PATCH', body: body() })) // price what you just edited
  result.value = await api<PriceResult>(`/api/cards/${c.id}/price`)
  const fresh = await api<Card>(`/api/cards/${c.id}`)
  const keep = result.value
  replaceCurrent(fresh)
  result.value = keep
})
const crop = (box: { x: number; y: number; w: number; h: number }) => run('crop', async () => {
  replaceCurrent(await api<Card>(`/api/cards/${card.value!.id}/crop`, { method: 'POST', body: box }))
  cropOpen.value = false
})
function advance() { if (pos.value < queue.value.length) pos.value++; load(card.value) }
const saveNext = () => run('save', async () => {
  await api<Card>(`/api/cards/${card.value!.id}`, { method: 'PATCH', body: { ...body(), reviewed: true } })
  done.value++
  advance()
})
</script>

<template>
  <div>
    <PageHeader title="Review queue" subtitle="Cards whose identification or price is doubtful, the most dollars at risk first. Fix what's wrong or type your own value, then mark each one reviewed." :crumbs="[{ title: 'Inventory', to: '/' }, { title: 'Review' }]" />
    <v-alert v-if="error" type="error" variant="tonal" border="start" class="mb-4" :text="error" />
    <v-progress-linear v-if="loading" indeterminate />

    <v-card v-else-if="!card" class="empty">
      <v-icon icon="mdi-check-circle-outline" size="40" color="success" />
      <h3>{{ queue.length ? 'You reached the end of the queue' : 'Nothing needs review' }}</h3>
      <p>{{ done }} reviewed this session.</p>
      <v-btn color="primary" variant="flat" to="/">Back to inventory</v-btn>
    </v-card>

    <v-row v-else>
      <v-col cols="12" md="5">
        <v-card>
          <div class="card-body">
            <div class="d-flex justify-space-between align-center mb-3">
              <span class="text-body-2 text-muted-c num">{{ pos + 1 }} of {{ queue.length }}</span>
            </div>
            <div class="d-flex ga-1 flex-wrap mb-3"><v-chip v-for="w in why" :key="w" size="small" color="warning" variant="tonal">{{ w }}</v-chip></div>
            <div class="photo"><CardThumb :src="card.image_url" :alt="card.name" /></div>
            <div class="d-flex ga-2 mt-3 flex-wrap">
              <v-btn v-if="card.image_url" variant="outlined" size="small" prepend-icon="mdi-crop" @click="cropOpen = true">Crop to the card</v-btn>
              <v-btn variant="text" size="small" :to="`/cards/${card.id}`">Open full page</v-btn>
            </div>
          </div>
        </v-card>
      </v-col>

      <v-col cols="12" md="7">
        <v-card>
          <div class="card-body">
            <v-row dense>
              <v-col cols="12"><v-text-field v-model="f.name" label="Name" /></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model="f.set_name" label="Set" /></v-col>
              <v-col cols="6" sm="3"><v-text-field v-model="f.year" label="Year" /></v-col>
              <v-col cols="6" sm="3"><v-text-field v-model="f.card_number" label="Number" /></v-col>
              <v-col cols="12" sm="4"><v-combobox v-model="f.grade" :items="GRADE_OPTIONS" label="Grade" clearable /></v-col>
              <v-col cols="12" sm="4"><v-text-field v-model="f.parallel" label="Version" placeholder="Green /99, Base…" /></v-col>
              <v-col cols="6" sm="4"><v-text-field v-model="f.cost" label="Paid per card" type="number" min="0" step="0.01" prefix="$" /></v-col>
              <v-col cols="6" sm="4"><v-text-field v-model="f.manual_value" label="Your value" type="number" min="0" step="0.01" prefix="$" clearable hint="Overrides lookups" persistent-hint /></v-col>
              <v-col cols="12"><v-text-field v-model="f.search_query" label="eBay search" prepend-inner-icon="mdi-magnify" /></v-col>
            </v-row>

            <div class="d-flex ga-2 flex-wrap mt-1">
              <v-btn variant="outlined" prepend-icon="mdi-magnify" :loading="busy === 'price'" @click="lookUp">Look up price</v-btn>
              <span v-if="card.value != null" class="align-self-center text-body-2">Current value <strong class="num">{{ money(card.value) }}</strong> <span class="text-muted-c">({{ card.value_basis }}<template v-if="card.est_value != null && card.value_basis !== 'asking'"> · asking {{ money(card.est_value) }}</template><template v-if="card.sold_value != null && card.value_basis !== 'sold'"> · sold {{ money(card.sold_value) }}</template>)</span><ConfidenceDot :confidence="card.confidence" :reasons="card.confidence_reasons" :pending="card.value_pending" show-label /></span>
            </div>

            <div v-if="result?.snapshot.top_listings?.length" class="mt-4">
              <div class="text-caption text-muted-c mb-1">Closest eBay listings. Do they look like your card?</div>
              <a v-for="l in result.snapshot.top_listings.slice(0, 5)" :key="l.item_id" :href="l.url || undefined" target="_blank" rel="noopener" class="listing">
                <span class="truncate">{{ l.title }}</span><span class="num text-no-wrap">{{ money(l.price) }}</span>
              </a>
            </div>
          </div>
          <v-divider />
          <div class="d-flex justify-space-between pa-4 ga-2 flex-wrap">
            <v-btn variant="text" @click="advance">Skip</v-btn>
            <v-btn color="primary" variant="flat" append-icon="mdi-arrow-right" :loading="busy === 'save'" @click="saveNext">Mark reviewed &amp; next</v-btn>
          </div>
        </v-card>
      </v-col>
    </v-row>

    <CropDialog v-model="cropOpen" :src="card?.image_url ?? null" :busy="busy === 'crop'" @apply="crop" />
  </div>
</template>

<style scoped>
.photo { max-width: 320px; margin: 0 auto; }
.empty { display: grid; justify-items: center; gap: 8px; text-align: center; padding: 56px 16px; }
.empty h3 { margin: 0; font-size: 16px; }
.empty p { margin: 0 0 8px; color: rgb(var(--v-theme-text-secondary)); }
.listing { display: flex; justify-content: space-between; gap: 12px; padding: 6px 0; font-size: 13.5px; color: inherit; text-decoration: none; border-bottom: 1px solid rgb(var(--v-theme-border)); }
.listing:hover { color: rgb(var(--v-theme-primary)); }
</style>
