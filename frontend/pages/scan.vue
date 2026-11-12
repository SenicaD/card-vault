<script setup lang="ts">
import { useDisplay } from 'vuetify'
import type { Location } from '~/types'
import { AUTO_ADD_THRESHOLD, type ScanItem } from '~/composables/useScanQueue'

const { api } = useApi()
const { mdAndUp } = useDisplay()
const q = useScanQueue()
const { items, counts } = q

const locations = ref<Location[]>([])
const camera = ref<HTMLInputElement>()
const upload = ref<HTMLInputElement>()
const dragging = ref(false)
const editingKey = ref<number | null>(null)
const editing = computed(() => items.value.find((i) => i.key === editingKey.value) ?? null)
const savingAll = ref(false)

const locationItems = computed(() => [
  { value: null, title: 'Unassigned' },
  ...locations.value.map((l) => ({ value: l.id, title: l.name })),
])
const locName = (id: number | null) => locations.value.find((l) => l.id === id)?.name || 'Unassigned'

onMounted(async () => {
  locations.value = await api<Location[]>('/api/locations').catch(() => [])
  try { q.autoAdd.value = localStorage.getItem('cv-auto-add') === '1' } catch {}
  window.addEventListener('beforeunload', guard)
})
onBeforeUnmount(() => window.removeEventListener('beforeunload', guard))
watch(q.autoAdd, (v) => { try { localStorage.setItem('cv-auto-add', v ? '1' : '0') } catch {} })

// The queue survives in-app navigation, but not a reload or closed tab.
function guard(e: BeforeUnloadEvent) {
  if (counts.value.pending || counts.value.ready) e.preventDefault()
}

function onPick(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files) q.addFiles(input.files)
  input.value = ''
}
function onDrop(e: DragEvent) {
  dragging.value = false
  if (e.dataTransfer?.files) q.addFiles(e.dataTransfer.files)
}

async function addAll() {
  savingAll.value = true
  try { await q.saveAll() } finally { savingAll.value = false }
}

const subline = (i: ScanItem) =>
  i.form ? [i.form.year, i.form.set_name, i.form.card_number && '#' + i.form.card_number.replace(/^#/, '')].filter(Boolean).join(' ') : ''
const conf = (i: ScanItem) => i.scan?.identification.confidence ?? 0
const confColor = (c: number) => (c >= AUTO_ADD_THRESHOLD ? 'success' : c >= 0.5 ? 'warning' : 'error')

const STATUS: Record<ScanItem['status'], { label: string; color: string; icon?: string }> = {
  queued: { label: 'Queued', color: 'sold', icon: 'mdi-clock-outline' },
  processing: { label: 'Reading & pricing', color: 'info' },
  ready: { label: 'Ready to add', color: 'primary', icon: 'mdi-eye-check-outline' },
  saving: { label: 'Adding', color: 'info' },
  added: { label: 'In inventory', color: 'success', icon: 'mdi-check-circle-outline' },
  error: { label: 'Failed', color: 'error', icon: 'mdi-alert-circle-outline' },
}

const headers = [
  { title: 'Card', key: 'card', sortable: false },
  { title: 'Confidence', key: 'conf', sortable: false },
  { title: 'Stored in', key: 'loc', sortable: false },
  { title: 'eBay value', key: 'value', sortable: false, align: 'end' as const },
  { title: 'Status', key: 'status', sortable: false },
  { title: '', key: 'actions', sortable: false, align: 'end' as const, width: 150 },
]

function openRow(_: unknown, row: { item: ScanItem }) {
  if (row.item.form && row.item.status !== 'added') editingKey.value = row.item.key
  else if (row.item.card) navigateTo(`/cards/${row.item.card.id}`)
}
</script>

<template>
  <div
    @dragover.prevent="dragging = true"
    @dragleave.self="dragging = false"
    @drop.prevent="onDrop"
  >
    <input ref="camera" type="file" accept="image/*" capture="environment" hidden @change="onPick" />
    <input ref="upload" type="file" accept="image/*" multiple hidden @change="onPick" />

    <PageHeader
      title="Scan & price"
      subtitle="Snap cards one after another or upload a batch. Each photo is identified and priced against eBay as it comes in."
      :crumbs="[{ title: 'Inventory', to: '/' }, { title: 'Scan' }]"
    >
      <template #actions>
        <v-btn variant="outlined" prepend-icon="mdi-upload-multiple-outline" @click="upload?.click()">Upload photos</v-btn>
        <v-btn color="primary" variant="flat" prepend-icon="mdi-camera-plus-outline" @click="camera?.click()">Take photo</v-btn>
      </template>
    </PageHeader>

    <!-- Batch settings -->
    <v-card class="mb-4">
      <div class="settings">
        <v-select v-model="q.defaultLocation.value" :items="locationItems" label="File new cards in" prepend-inner-icon="mdi-archive-outline" class="set-loc" />
        <v-switch v-model="q.autoAdd.value" color="primary" hide-details density="compact" inset>
          <template #label>
            <span class="text-body-2">Auto-add cards identified at ≥ {{ AUTO_ADD_THRESHOLD * 100 }}% confidence</span>
          </template>
        </v-switch>
        <v-spacer />
        <div class="text-caption text-muted-c">Lower-confidence cards wait for your review.</div>
      </div>
    </v-card>

    <!-- Drop zone -->
    <v-card v-if="!items.length || dragging" class="drop mb-4" :class="{ over: dragging }" @click="upload?.click()">
      <div class="drop-inner">
        <div class="drop-icon"><v-icon icon="mdi-image-multiple-outline" size="34" /></div>
        <h2>{{ dragging ? 'Drop to scan' : 'Drop card photos here' }}</h2>
        <p>Or use <strong>Take photo</strong> to shoot cards one after another, or <strong>Upload photos</strong> to pick a batch. JPEG, PNG or WebP.</p>
        <div class="d-flex ga-2 mt-2 flex-wrap justify-center">
          <v-btn color="primary" variant="flat" prepend-icon="mdi-camera-plus-outline" @click.stop="camera?.click()">Take photo</v-btn>
          <v-btn variant="outlined" prepend-icon="mdi-upload-multiple-outline" @click.stop="upload?.click()">Upload photos</v-btn>
        </div>
      </div>
    </v-card>

    <template v-if="items.length">
      <!-- Totals -->
      <v-row dense class="mb-2">
        <v-col cols="6" md="3"><StatCard label="In progress" :value="counts.pending" icon="mdi-progress-clock" color="info" :sub="`${counts.total} photos this session`" /></v-col>
        <v-col cols="6" md="3"><StatCard label="Ready for review" :value="counts.ready" icon="mdi-eye-check-outline" color="primary" :sub="`${money(q.readyValue.value)} estimated`" /></v-col>
        <v-col cols="6" md="3"><StatCard label="Added" :value="counts.added" icon="mdi-check-circle-outline" color="success" :sub="`${money(q.addedValue.value)} estimated`" /></v-col>
        <v-col cols="6" md="3"><StatCard label="Failed" :value="counts.error" icon="mdi-alert-circle-outline" color="error" sub="Retry or remove" /></v-col>
      </v-row>

      <!-- Queue -->
      <v-card>
        <div class="card-head">
          <div>
            <h2><v-icon icon="mdi-tray-full" size="18" />Scan queue</h2>
            <p>Click a card to review its details and price.</p>
          </div>
          <div class="d-flex ga-2 flex-wrap justify-end">
            <v-btn v-if="counts.added" variant="text" size="small" prepend-icon="mdi-broom" @click="q.clearAdded">Clear added</v-btn>
            <v-btn color="primary" variant="flat" prepend-icon="mdi-tray-arrow-down" :disabled="!counts.ready" :loading="savingAll" @click="addAll">
              Add {{ counts.ready || '' }} to inventory
            </v-btn>
          </div>
        </div>

        <v-progress-linear v-if="counts.pending" :model-value="((counts.total - counts.pending) / counts.total) * 100" color="primary" height="3" />

        <!-- Desktop table -->
        <v-data-table v-if="mdAndUp" :headers="headers" :items="items" item-value="key" items-per-page="-1" hide-default-footer @click:row="openRow">
          <template #item.card="{ item }">
            <div class="d-flex align-center ga-3 py-2" style="min-width: 240px">
              <div class="mini-thumb"><CardThumb :src="item.preview" :alt="item.fileName" /></div>
              <div class="min-w-0">
                <template v-if="item.form">
                  <div class="font-weight-medium truncate">{{ item.form.name }}</div>
                  <div class="text-caption text-secondary-c truncate">{{ subline(item) || '—' }}</div>
                </template>
                <template v-else>
                  <div class="text-secondary-c truncate">{{ item.fileName }}</div>
                  <div v-if="item.error" class="text-caption text-error">{{ item.error }}</div>
                  <v-skeleton-loader v-else type="text" width="160" class="bg-transparent" />
                </template>
              </div>
            </div>
          </template>
          <template #item.conf="{ item }">
            <v-chip v-if="item.scan" :color="confColor(conf(item))" variant="tonal" size="small" class="num">{{ Math.round(conf(item) * 100) }}%</v-chip>
            <span v-else class="text-muted-c">—</span>
          </template>
          <template #item.loc="{ item }">
            <span v-if="item.form" class="text-no-wrap">{{ locName(item.form.location_id) }}</span>
          </template>
          <template #item.value="{ item }">
            <template v-if="item.quote">
              <div class="num font-weight-semibold">{{ money(item.quote.median) }}</div>
              <div class="text-caption text-muted-c num text-no-wrap">{{ money(item.quote.low) }} – {{ money(item.quote.high) }}</div>
            </template>
            <v-tooltip v-else-if="item.priceError" :text="item.priceError" location="top">
              <template #activator="{ props: p }"><span v-bind="p" class="text-error text-caption">Pricing failed</span></template>
            </v-tooltip>
            <span v-else class="text-muted-c">—</span>
          </template>
          <template #item.status="{ item }">
            <v-tooltip :disabled="!item.error" :text="item.error" location="top">
              <template #activator="{ props: p }">
                <v-chip v-bind="p" :color="STATUS[item.status].color" variant="tonal" size="small" class="text-no-wrap">
                  <v-progress-circular v-if="item.status === 'processing' || item.status === 'saving'" indeterminate size="12" width="2" class="mr-2" />
                  <v-icon v-else-if="STATUS[item.status].icon" :icon="STATUS[item.status].icon" start size="14" />
                  {{ STATUS[item.status].label }}
                </v-chip>
              </template>
            </v-tooltip>
          </template>
          <template #item.actions="{ item }">
            <div class="d-flex justify-end ga-1">
              <v-btn v-if="item.status === 'ready'" icon="mdi-plus" variant="text" size="small" color="primary" aria-label="Add to inventory" @click.stop="q.save(item.key)" />
              <v-btn v-if="item.status === 'error'" icon="mdi-refresh" variant="text" size="small" aria-label="Retry" @click.stop="q.retry(item.key)" />
              <v-btn v-if="item.card" icon="mdi-open-in-new" variant="text" size="small" :to="`/cards/${item.card.id}`" aria-label="Open card" @click.stop />
              <v-btn v-if="item.status !== 'added' && item.status !== 'saving'" icon="mdi-close" variant="text" size="small" aria-label="Remove from queue" @click.stop="q.remove(item.key)" />
            </div>
          </template>
        </v-data-table>

        <!-- Mobile list -->
        <v-list v-else class="py-0">
          <template v-for="(item, n) in items" :key="item.key">
            <v-divider v-if="n" />
            <v-list-item class="py-3" @click="openRow(null, { item })">
              <template #prepend><div class="mini-thumb mr-3"><CardThumb :src="item.preview" :alt="item.fileName" /></div></template>
              <v-list-item-title class="font-weight-medium">{{ item.form?.name || item.fileName }}</v-list-item-title>
              <v-list-item-subtitle>
                <span v-if="item.error" class="text-error">{{ item.error }}</span>
                <span v-else>{{ STATUS[item.status].label }}<template v-if="item.scan"> · {{ Math.round(conf(item) * 100) }}%</template></span>
              </v-list-item-subtitle>
              <template #append>
                <div class="text-right">
                  <div v-if="item.quote" class="num font-weight-semibold">{{ money(item.quote.median) }}</div>
                  <v-progress-circular v-else-if="item.status === 'processing'" indeterminate size="16" width="2" color="primary" />
                  <v-btn v-if="item.status === 'error'" icon="mdi-refresh" variant="text" size="small" aria-label="Retry" @click.stop="q.retry(item.key)" />
                  <v-btn v-if="item.status === 'ready'" icon="mdi-plus" variant="text" size="small" color="primary" aria-label="Add to inventory" @click.stop="q.save(item.key)" />
                </div>
              </template>
            </v-list-item>
          </template>
        </v-list>
      </v-card>
    </template>

    <!-- Phone: keep the shutter in reach for back-to-back shots -->
    <v-btn
      v-if="!mdAndUp"
      color="primary"
      icon="mdi-camera-plus-outline"
      size="x-large"
      elevation="6"
      class="shutter"
      aria-label="Take photo"
      @click="camera?.click()"
    />

    <ScanItemDialog :item="editing" :locations="locations" @close="editingKey = null" @reprice="q.reprice" @save="q.save" @crop="q.recrop" />
  </div>
</template>

<style scoped>
.min-w-0 { min-width: 0; }
.font-weight-semibold { font-weight: 600; }
.settings { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; padding: 12px 18px; }
.set-loc { max-width: 280px; min-width: 220px; }
.drop { border-style: dashed !important; border-width: 2px !important; cursor: pointer; transition: border-color 0.15s, background 0.15s; }
.drop:hover, .drop.over { border-color: rgb(var(--v-theme-primary)) !important; }
.drop.over { background: rgb(var(--v-theme-brand-muted)) !important; }
.drop-inner { display: grid; justify-items: center; text-align: center; gap: 6px; padding: 48px 20px; }
.drop-icon { width: 68px; height: 68px; border-radius: 18px; display: grid; place-items: center; background: rgb(var(--v-theme-brand-muted)); color: rgb(var(--v-theme-primary)); margin-bottom: 6px; }
.drop h2 { font-size: 18px; font-weight: 600; margin: 0; }
.drop p { margin: 0; color: rgb(var(--v-theme-text-secondary)); font-size: 14px; max-width: 56ch; }
.mini-thumb { width: 40px; flex-shrink: 0; }
.mini-thumb :deep(.thumb) { border-radius: 5px; padding: 1px; }
.mini-thumb :deep(img), .mini-thumb :deep(.empty) { border-radius: 4px; }
.shutter { position: fixed !important; right: 16px; bottom: calc(16px + env(safe-area-inset-bottom, 0px)); z-index: 1005; }
</style>
