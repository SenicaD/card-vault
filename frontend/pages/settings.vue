<script setup lang="ts">
import { WIDE_SPREAD_ACTIONS, type AppSettings } from '~/types'

const { api } = useApi()
const size = ref('')
const action = ref<AppSettings['wide_spread_action']>('flag')
const windowSize = ref('')
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
// The N the rule hints show: what's in the field, or the saved value while the field is invalid
const shownWindow = computed(() => (windowError.value ? savedWindow.value : Number(windowSize.value.trim())))

async function load() {
  loading.value = true
  try {
    const s = await api<AppSettings>('/api/settings')
    size.value = String(s.cluster_min_size)
    action.value = s.wide_spread_action
    windowSize.value = String(s.price_window)
    savedWindow.value = s.price_window
  } catch (e: any) {
    error.value = `Couldn't load the settings: ${e.message}`
  } finally {
    loading.value = false
  }
}

async function save() {
  if (sizeError.value || windowError.value) return
  saving.value = true
  error.value = ''
  try {
    const s = await api<AppSettings>('/api/settings', {
      method: 'PUT',
      body: { cluster_min_size: Number(size.value.trim()), wide_spread_action: action.value, price_window: Number(windowSize.value.trim()) },
    })
    size.value = String(s.cluster_min_size)
    action.value = s.wide_spread_action
    windowSize.value = String(s.price_window)
    savedWindow.value = s.price_window
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
            <v-alert type="info" variant="tonal" density="compact" icon="mdi-information-outline">
              Changes apply the next time a card is priced: Refresh value, ticking or unticking listings, or the daily and bulk re-price. Saving does not re-price your existing cards. To apply them to every card at once without waiting on eBay, use Re-price → Re-value all on Inventory.
            </v-alert>
            <div>
              <v-btn type="submit" color="primary" variant="flat" prepend-icon="mdi-content-save-outline" :loading="saving" :disabled="loading || !!sizeError || !!windowError">
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
