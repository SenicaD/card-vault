<script setup lang="ts">
import { CATEGORY_LABELS, type Portfolio } from '~/types'

const { api } = useApi()
const RANGES = [{ v: '7d', t: '7D' }, { v: '1m', t: '1M' }, { v: '3m', t: '3M' }, { v: '1y', t: '1Y' }, { v: 'all', t: 'All' }]
const range = ref('1m')
const data = ref<Portfolio | null>(null)
const loading = ref(true)
const error = ref('')

async function load() {
  loading.value = true; error.value = ''
  try { data.value = await api<Portfolio>('/api/portfolio', { query: { range: range.value } }) }
  catch (e: any) { error.value = e.message } finally { loading.value = false }
}
onMounted(load)
watch(range, load)

const signed = (v: number) => (v > 0 ? '+' : v < 0 ? '−' : '') + money(Math.abs(v))
const tone = (v: number | null | undefined) => (!v ? '' : v > 0 ? 'up' : 'down')
const t = computed(() => data.value?.totals)
const maxOf = (rows: { value: number }[]) => Math.max(1, ...rows.map((r) => r.value))
</script>

<template>
  <div>
    <PageHeader title="Portfolio" subtitle="What the collection is worth, what it cost, and how the value has moved.">
      <template #actions>
        <v-btn-toggle v-model="range" mandatory density="compact" variant="outlined" divided rounded="lg" color="primary">
          <v-btn v-for="r in RANGES" :key="r.v" :value="r.v" size="small">{{ r.t }}</v-btn>
        </v-btn-toggle>
      </template>
    </PageHeader>
    <v-alert v-if="error" type="error" variant="tonal" border="start" class="mb-4" :text="error" />
    <v-progress-linear v-if="loading && !data" indeterminate />

    <template v-if="data && t">
      <v-row dense class="mb-2">
        <v-col cols="6" lg="3">
          <StatCard label="Collection value" :value="money(t.value)" icon="mdi-currency-usd" color="primary"
            :sub="[data.series.length > 1 ? `${signed(data.change)} over ${range === 'all' ? 'all time' : range}` : 'History builds as prices come in', t.sold_cards ? `${t.sold_cards} cards at sold prices${t.asking_cards ? `, ${t.asking_cards} at asking` : ''}. Asking alone: ${money(t.asking_value)}` : 'All asking prices so far'].join('. ')" />
        </v-col>
        <v-col cols="6" lg="3">
          <StatCard label="Paid" :value="t.costed_cards ? money(t.cost) : '—'" icon="mdi-receipt-text-outline" color="info"
            :sub="t.uncosted_cards ? `${t.uncosted_cards} cards have no cost` : 'All cards have a cost'" />
        </v-col>
        <v-col cols="6" lg="3">
          <StatCard label="Gain / loss" :value="t.gain == null ? '—' : signed(t.gain)" icon="mdi-trending-up" :color="t.gain != null && t.gain < 0 ? 'error' : 'success'"
            :sub="t.gain_pct != null ? `${t.gain_pct > 0 ? '+' : ''}${t.gain_pct}% on cards with a cost` : 'Add costs to see this'" />
        </v-col>
        <v-col cols="6" lg="3">
          <StatCard label="Cards" :value="t.cards" icon="mdi-cards-outline" color="warning"
            :sub="`${t.distinct} entries · ${t.unpriced} unpriced`" />
        </v-col>
      </v-row>

      <v-card class="mb-4">
        <div class="card-head"><div><h2><v-icon icon="mdi-chart-line" size="18" />Value over time</h2>
          <p>Sold prices where there were enough sales (the last 90 days up to each date), otherwise the asking price at the time.</p></div></div>
        <div class="card-body"><PortfolioChart :points="data.series" /></div>
      </v-card>

      <v-row dense class="mb-2">
        <v-col cols="12" md="6">
          <v-card class="h-100">
            <div class="card-head"><div><h2><v-icon icon="mdi-arrow-up-bold-outline" size="18" />Biggest gainers</h2><p>Price change over the last 7 days, times quantity</p></div></div>
            <div class="card-body pt-0">
              <p v-if="!data.gainers.length" class="muted">Nothing has risen yet.</p>
              <NuxtLink v-for="m in data.gainers" :key="m.id" :to="`/cards/${m.id}`" class="row"><span class="truncate">{{ m.name }} <span class="text-muted-c">{{ m.grade }}</span></span><span class="num up text-no-wrap">{{ signed(m.change) }} ({{ m.pct }}%)</span></NuxtLink>
            </div>
          </v-card>
        </v-col>
        <v-col cols="12" md="6">
          <v-card class="h-100">
            <div class="card-head"><div><h2><v-icon icon="mdi-arrow-down-bold-outline" size="18" />Biggest decliners</h2><p>Price change over the last 7 days, times quantity</p></div></div>
            <div class="card-body pt-0">
              <p v-if="!data.losers.length" class="muted">Nothing has fallen yet.</p>
              <NuxtLink v-for="m in data.losers" :key="m.id" :to="`/cards/${m.id}`" class="row"><span class="truncate">{{ m.name }} <span class="text-muted-c">{{ m.grade }}</span></span><span class="num down text-no-wrap">{{ signed(m.change) }} ({{ m.pct }}%)</span></NuxtLink>
            </div>
          </v-card>
        </v-col>
      </v-row>

      <v-row dense>
        <v-col cols="12" md="4">
          <v-card class="h-100">
            <div class="card-head"><div><h2><v-icon icon="mdi-archive-outline" size="18" />Value by location</h2></div></div>
            <div class="card-body pt-0">
              <div v-for="r in data.by_location" :key="r.name" class="bar-row">
                <div class="d-flex justify-space-between"><span class="truncate">{{ r.name }}</span><span class="num">{{ money(r.value) }}</span></div>
                <div class="bar"><div :style="{ width: (r.value / maxOf(data.by_location)) * 100 + '%' }" /></div>
                <div class="text-caption text-muted-c">{{ r.cards }} cards</div>
              </div>
            </div>
          </v-card>
        </v-col>
        <v-col cols="12" md="4">
          <v-card class="h-100">
            <div class="card-head"><div><h2><v-icon icon="mdi-shape-outline" size="18" />Value by type</h2></div></div>
            <div class="card-body pt-0">
              <div v-for="r in data.by_category" :key="r.name" class="bar-row">
                <div class="d-flex justify-space-between"><span class="truncate">{{ CATEGORY_LABELS[r.name as keyof typeof CATEGORY_LABELS] || r.name }}</span><span class="num">{{ money(r.value) }}</span></div>
                <div class="bar"><div :style="{ width: (r.value / maxOf(data.by_category)) * 100 + '%' }" /></div>
                <div class="text-caption text-muted-c">{{ r.cards }} cards</div>
              </div>
            </div>
          </v-card>
        </v-col>
        <v-col cols="12" md="4">
          <v-card class="h-100">
            <div class="card-head"><div><h2><v-icon icon="mdi-star-outline" size="18" />Most valuable</h2></div></div>
            <div class="card-body pt-0">
              <NuxtLink v-for="c in data.top_cards" :key="c.id" :to="`/cards/${c.id}`" class="row">
                <span class="truncate">{{ c.name }} <span class="text-muted-c">{{ c.grade }}<template v-if="c.quantity > 1"> ×{{ c.quantity }}</template></span></span>
                <span class="num text-no-wrap">{{ money((c.value ?? 0) * c.quantity) }}</span>
              </NuxtLink>
            </div>
          </v-card>
        </v-col>
      </v-row>
    </template>
  </div>
</template>

<style scoped>
.row { display: flex; justify-content: space-between; gap: 12px; padding: 7px 0; font-size: 14px; color: inherit; text-decoration: none; border-bottom: 1px solid rgb(var(--v-theme-border)); }
.row:hover { color: rgb(var(--v-theme-primary)); }
.up { color: rgb(var(--v-theme-success)); } .down { color: rgb(var(--v-theme-error)); }
.muted { color: rgb(var(--v-theme-text-secondary)); font-size: 14px; margin: 0; }
.bar-row { margin-bottom: 12px; font-size: 14px; }
.bar { height: 6px; border-radius: 4px; background: rgb(var(--v-theme-surface-sunken)); margin: 4px 0 2px; overflow: hidden; }
.bar > div { height: 100%; background: rgb(var(--v-theme-primary)); border-radius: 4px; }
</style>
