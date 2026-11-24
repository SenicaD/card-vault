<script setup lang="ts">
import { WIDE_SPREAD_ACTIONS, type AppSettings } from '~/types'

const { api } = useApi()
const size = ref('')
const action = ref<AppSettings['wide_spread_action']>('flag')
const windowSize = ref('')
const ceiling = ref('')
const savedWindow = ref<number | null>(null)
const error = ref('')
const notice = ref('')
const loading = ref(true)
const saving = ref(false)

// Shown under the field as you type; a bad value is never sent
const sizeError = computed(() => {
  const v = size.value.trim()
  if (!v) return 'Enter a number from 2 to 20'
  if (!/^\d+$/.test(v)) return 'Use a whole number from 2 to 20'
  const n = Number(v)
  return n < 2 || n > 20 ? 'Must be from 2 to 20' : ''
})
const windowError = computed(() => {
  const v = windowSize.value.trim()
  if (!v) return 'Enter a number from 1 to 50'
  if (!/^\d+$/.test(v)) return 'Use a whole number from 1 to 50'
  const n = Number(v)
  return n < 1 || n > 50 ? 'Must be from 1 to 50' : ''
})
const ceilingError = computed(() => {
  const v = ceiling.value.trim().replace(/[x×]$/i, '')
  if (!v) return ''
  return /^\d+(\.\d{1,2})?$/.test(v) && Number(v) >= 1.5 && Number(v) <= 100 ? '' : 'Enter a number from 1.5 to 100, or leave empty for no limit'
})
const ceilingValue = () => { const v = ceiling.value.trim().replace(/[x×]$/i, ''); return v ? Number(v) : null }

// Target price settings, typed as whole percentages (85 = 0.85); review_below is a 0-100 score
const discount = ref('')
const smoothing = ref('')
const markup = ref('')
const reviewBelow = ref('')
const pctError = (r: Ref<string>, lo: number, hi: number) => computed(() => {
  const v = r.value.trim().replace(/%$/, '')
  return /^\d+$/.test(v) && Number(v) >= lo && Number(v) <= hi ? '' : `Enter a whole number from ${lo} to ${hi}`
})
const discountError = pctError(discount, 30, 100)
const smoothingError = pctError(smoothing, 0, 50)
const markupError = pctError(markup, 100, 200)
const reviewError = pctError(reviewBelow, 0, 100)
const num = (r: Ref<string>) => Number(r.value.trim().replace(/%$/, ''))
const anyError = computed(() => !!(sizeError.value || windowError.value || ceilingError.value || discountError.value
  || smoothingError.value || markupError.value || reviewError.value))
function fill(s: AppSettings) {
  size.value = String(s.cluster_min_size)
  action.value = s.wide_spread_action
  windowSize.value = String(s.price_window)
  savedWindow.value = s.price_window
  ceiling.value = s.outlier_ratio == null ? '' : String(s.outlier_ratio)
  discount.value = String(Math.round(s.asking_discount * 100))
  smoothing.value = String(Math.round(s.smoothing_band * 100))
  markup.value = String(Math.round(s.list_markup * 100))
  reviewBelow.value = String(s.review_below)
}
// The N the rule hints show: what's in the field, or the saved value while the field is invalid
const shownWindow = computed(() => (windowError.value ? savedWindow.value : Number(windowSize.value.trim())))

async function load() {
  loading.value = true
  try {
    fill(await api<AppSettings>('/api/settings'))
  } catch (e: any) {
    error.value = `Couldn't load the settings: ${e.message}`
  } finally {
    loading.value = false
  }
}

async function save() {
  if (anyError.value) return
  saving.value = true
  error.value = ''
  try {
    fill(await api<AppSettings>('/api/settings', {
      method: 'PUT',
      body: {
        cluster_min_size: Number(size.value.trim()), wide_spread_action: action.value, price_window: Number(windowSize.value.trim()), outlier_ratio: ceilingValue(),
        asking_discount: num(discount) / 100, smoothing_band: num(smoothing) / 100, list_markup: num(markup) / 100, review_below: num(reviewBelow),
      },
    }))
    notice.value = 'Settings saved'
  } catch (e: any) {
    error.value = `Couldn't save the settings: ${e.message}`
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader
      title="Settings"
      subtitle="How CardVault turns a card's eBay asking listings into one value."
    />

    <v-alert v-if="error" type="error" variant="tonal" border="start" closable class="mb-4" :text="error" @click:close="error = ''" />

    <v-row>
      <v-col cols="12" lg="8">
        <v-card>
          <div class="card-head">
            <div><h2><v-icon icon="mdi-chart-bell-curve" size="18" />Price groups</h2><p>Listings are split into price groups where prices jump, so each version's price shows on its own.</p></div>
          </div>
          <form class="card-body d-grid" @submit.prevent="save">
            <v-text-field
              v-model="size"
              label="Minimum listings per price group"
              inputmode="numeric"
              :disabled="loading"
              :error-messages="loading ? [] : sizeError"
              hint="A smaller group is left out as a stray. From 2 to 20."
              persistent-hint
              style="max-width: 360px"
            />
            <div>
              <div id="wide-spread-label" class="font-weight-medium mb-1">How to value a card, by default</div>
              <v-radio-group v-model="action" aria-labelledby="wide-spread-label" :disabled="loading" hide-details>
                <v-radio v-for="a in WIDE_SPREAD_ACTIONS" :key="a.value" :value="a.value" class="mb-2">
                  <template #label>
                    <div>
                      <div>{{ a.title }}</div>
                      <div v-if="shownWindow != null" class="text-caption text-secondary-c">{{ a.hint(shownWindow) }}</div>
                    </div>
                  </template>
                </v-radio>
              </v-radio-group>
              <div class="text-caption text-secondary-c mt-1">Highest, Median and Lowest apply to every card. Flag only matters when prices spread too widely, usually because several versions are mixed together. Each card can override this on its own page.</div>
            </div>
            <v-text-field
              v-model="windowSize"
              label="Listings to price from"
              inputmode="numeric"
              :disabled="loading"
              :error-messages="loading ? [] : windowError"
              hint="Every card is valued from this many listings, picked by the rule above. From 1 to 50."
              persistent-hint
              style="max-width: 360px"
            />
            <v-text-field
              v-model="ceiling"
              label="Ignore listings priced above"
              suffix="× the card's median"
              inputmode="decimal"
              :disabled="loading"
              :error-messages="loading ? [] : ceilingError"
              hint="Relative to each card: with 5, a $20 card ignores listings over $100 and a $400 card ignores listings over $2,000. Leave empty for no limit."
              persistent-hint
              style="max-width: 360px"
            />
            <v-alert type="info" variant="tonal" density="compact" icon="mdi-information-outline">
              Changes apply the next time a card is priced: Refresh value, ticking or unticking listings, or the daily and bulk re-price. Saving does not re-price your existing cards. To apply them to every card at once without waiting on eBay, use Re-price → Re-value all on Inventory.
            </v-alert>

            <v-divider class="my-2" />
            <div>
              <h2 class="text-subtitle-1 font-weight-medium"><v-icon icon="mdi-target" size="18" class="mr-1" />Target price</h2>
              <p class="text-caption text-secondary-c mb-0">Every card gets one value: yours, else what it sold for, else its asking price adjusted below. These apply to every card as soon as you save.</p>
            </div>
            <v-row dense>
              <v-col cols="12" sm="6"><v-text-field v-model="discount" label="Value asking-only cards at" suffix="% of asking" inputmode="numeric" :disabled="loading" :error-messages="loading ? [] : discountError" hint="Cards usually sell below their asking price. 85 values a $100 asking card at $85. 100 = no change." persistent-hint /></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model="smoothing" label="Hold price jumps over" suffix="%" inputmode="numeric" :disabled="loading" :error-messages="loading ? [] : smoothingError" hint="A daily lookup that moves a value by more than this waits for the next lookup to agree. 0 = no smoothing." persistent-hint /></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model="markup" label="List on eBay at" suffix="% of the value" inputmode="numeric" :disabled="loading" :error-messages="loading ? [] : markupError" hint="Rounded to end in .99, leaving room for Best Offer. 110 lists a $50 card at $54.99." persistent-hint /></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model="reviewBelow" label="Review cards below" suffix="confidence (0–100)" inputmode="numeric" :disabled="loading" :error-messages="loading ? [] : reviewError" hint="Cards whose price confidence is lower go to the review queue, the most dollars at risk first." persistent-hint /></v-col>
            </v-row>
            <div>
              <v-btn type="submit" color="primary" variant="flat" prepend-icon="mdi-content-save-outline" :loading="saving" :disabled="loading || anyError">
                Save
              </v-btn>
            </div>
          </form>
        </v-card>
      </v-col>
    </v-row>

    <v-snackbar :model-value="!!notice" timeout="3000" color="success" @update:model-value="notice = ''">{{ notice }}</v-snackbar>
  </div>
</template>

<style scoped>
.d-grid { display: grid; gap: 14px; }
</style>
