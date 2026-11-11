<script setup lang="ts">
import { useDisplay } from 'vuetify'
import type { Location } from '~/types'
import type { ScanItem } from '~/composables/useScanQueue'

const { mdAndUp } = useDisplay()
const props = defineProps<{ item: ScanItem | null; locations: Location[] }>()
const emit = defineEmits<{ close: []; reprice: [key: number]; save: [key: number] }>()

const pct = computed(() => Math.round((props.item?.scan?.identification.confidence ?? 0) * 100))
const IDENTIFIERS: Record<string, string> = { 'ebay-image': 'eBay image match', claude: 'Claude', mock: 'demo identifier' }
const identifiedBy = computed(() => IDENTIFIERS[props.item?.scan?.identifier ?? ''] ?? props.item?.scan?.identifier)
const visual = computed(() => props.item?.quote?.source === 'ebay-image')
const stale = computed(() => !!props.item?.quote && props.item.quote.query !== props.item.form?.search_query)
</script>

<template>
  <v-dialog :model-value="!!item" max-width="1100" :fullscreen="!mdAndUp" scrollable @update:model-value="(v) => !v && emit('close')">
    <v-card v-if="item && item.form">
      <div class="card-head">
        <div>
          <h2><v-icon icon="mdi-form-select" size="18" />Review card</h2>
          <p>{{ item.fileName }} · identified by {{ identifiedBy }} at {{ pct }}% confidence</p>
        </div>
        <v-btn icon="mdi-close" variant="text" size="small" aria-label="Close" @click="emit('close')" />
      </div>

      <v-card-text class="pa-0">
        <div class="layout">
          <div class="photo"><CardThumb :src="item.preview" :alt="item.form.name" /></div>

          <div class="form"><CardForm v-model="item.form" :locations="locations" /></div>

          <div class="price">
            <div class="section-label">eBay value</div>
            <div v-if="item.quote" class="quote">
              <div class="median num">{{ money(item.quote.median) }}</div>
              <div class="text-caption text-secondary-c num">
                {{ money(item.quote.low) }} – {{ money(item.quote.high) }} · {{ item.quote.sample_size }} {{ visual ? 'look-alike listings' : 'listings' }}
              </div>
            </div>
            <div v-else class="text-body-2 text-secondary-c">Not priced yet.</div>

            <v-alert v-if="item.priceError" type="error" variant="tonal" density="compact" class="mt-3" :text="item.priceError" />
            <v-alert v-else-if="stale" type="warning" variant="tonal" density="compact" class="mt-3">
              Search changed since this price was fetched. Re-price to update it.
            </v-alert>

            <v-btn block variant="outlined" class="mt-3" prepend-icon="mdi-refresh" :loading="item.repricing" @click="emit('reprice', item.key)">
              Re-price with current search
            </v-btn>

            <template v-if="item.comps.length">
              <div class="section-label mt-5">{{ visual ? 'Look-alike listings, cheapest first' : 'Cheapest comparable listings' }}</div>
              <div class="comps">
                <a v-for="c in item.comps.slice(0, 8)" :key="c.item_id" :href="c.url || undefined" target="_blank" rel="noopener" class="comp">
                  <img v-if="c.image_url" :src="c.image_url" alt="" class="comp-img" loading="lazy" />
                  <span class="truncate">{{ c.title }}</span>
                  <span class="num text-no-wrap">{{ money(c.price) }}</span>
                </a>
              </div>
            </template>
          </div>
        </div>
      </v-card-text>

      <v-divider />
      <div class="footer-actions">
        <v-spacer />
        <v-btn variant="text" @click="emit('close')">Done</v-btn>
        <v-btn
          v-if="item.status === 'ready'"
          color="primary"
          variant="flat"
          prepend-icon="mdi-plus"
          :disabled="!item.form.name"
          @click="emit('save', item.key); emit('close')"
        >
          Add to inventory
        </v-btn>
      </div>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.layout { display: grid; grid-template-columns: 200px minmax(0, 1fr) 280px; gap: 20px; padding: 18px; }
@media (max-width: 960px) {
  .layout { grid-template-columns: minmax(0, 1fr); }
  .photo { max-width: 180px; margin: 0 auto; }
}
.price { border-left: 1px solid rgb(var(--v-theme-border)); padding-left: 20px; min-width: 0; }
.comp > span.truncate { flex: 1; }
.comp { align-items: center; }
.comp-img { width: 28px; height: 28px; object-fit: cover; border-radius: 4px; flex-shrink: 0; }
@media (max-width: 960px) { .price { border-left: 0; padding-left: 0; } }
.section-label { font-size: 11.5px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: rgb(var(--v-theme-text-muted)); margin-bottom: 8px; }
.median { font-size: 28px; font-weight: 700; letter-spacing: -0.02em; }
.comps { display: block; min-width: 0; }
.comp { display: flex; justify-content: space-between; gap: 10px; font-size: 13px; padding: 6px 0; border-top: 1px solid rgb(var(--v-theme-border)); color: inherit; text-decoration: none; }
.comp:hover { color: rgb(var(--v-theme-primary)); }
.footer-actions { display: flex; gap: 8px; padding: 12px 18px calc(12px + env(safe-area-inset-bottom, 0px)); background: rgb(var(--v-theme-surface-chrome)); }
</style>
