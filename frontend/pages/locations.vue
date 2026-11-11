<script setup lang="ts">
import { useDisplay } from 'vuetify'
import type { Location } from '~/types'

const { api } = useApi()
const { xs } = useDisplay()
const locations = ref<Location[]>([])
const name = ref('')
const kind = ref('box')
const error = ref('')
const loading = ref(true)
const adding = ref(false)
const pendingDelete = ref<Location | null>(null)

const KINDS = [
  { value: 'box', title: 'Box', icon: 'mdi-package-variant-closed' },
  { value: 'binder', title: 'Binder', icon: 'mdi-book-open-variant-outline' },
  { value: 'case', title: 'Case', icon: 'mdi-briefcase-outline' },
  { value: 'shelf', title: 'Shelf', icon: 'mdi-bookshelf' },
]
const kindIcon = (k: string) => KINDS.find((x) => x.value === k)?.icon || 'mdi-archive-outline'

const headers = [
  { title: 'Name', key: 'name' },
  { title: 'Kind', key: 'kind' },
  { title: 'Cards', key: 'card_count', align: 'end' as const },
  { title: 'Created', key: 'created_at' },
  { title: '', key: 'actions', sortable: false, align: 'end' as const, width: 120 },
]

const totalCards = computed(() => locations.value.reduce((a, l) => a + l.card_count, 0))

async function load() {
  loading.value = true
  locations.value = await api<Location[]>('/api/locations').catch((e) => {
    error.value = e.message
    return []
  })
  loading.value = false
}

async function add() {
  if (!name.value.trim()) return
  adding.value = true
  error.value = ''
  try {
    await api('/api/locations', { method: 'POST', body: { name: name.value.trim(), kind: kind.value } })
    name.value = ''
    await load()
  } catch (e: any) {
    error.value = `Couldn't add the location: ${e.message}`
  } finally {
    adding.value = false
  }
}

async function confirmDelete() {
  const l = pendingDelete.value
  if (!l) return
  try {
    await api(`/api/locations/${l.id}`, { method: 'DELETE' })
    await load()
  } catch (e: any) {
    error.value = `Couldn't delete "${l.name}": ${e.message}`
  } finally {
    pendingDelete.value = null
  }
}

const open = (l: Location) => navigateTo({ path: '/', query: { location: l.id } })

onMounted(load)
</script>

<template>
  <div>
    <PageHeader
      title="Storage"
      subtitle="The boxes, binders and shelves your cards physically live in. Each card can also record a slot, like a binder page and pocket."
    />

    <v-alert v-if="error" type="error" variant="tonal" border="start" closable class="mb-4" :text="error" @click:close="error = ''" />

    <v-row dense class="mb-2">
      <v-col cols="6" md="3"><StatCard label="Locations" :value="locations.length" icon="mdi-archive-outline" color="primary" /></v-col>
      <v-col cols="6" md="3"><StatCard label="Cards stored" :value="totalCards" icon="mdi-cards-outline" color="info" /></v-col>
    </v-row>

    <v-row>
      <v-col cols="12" lg="4">
        <v-card>
          <div class="card-head">
            <div><h2><v-icon icon="mdi-plus-box-outline" size="18" />New location</h2><p>Pick it when you scan cards.</p></div>
          </div>
          <form class="card-body d-grid" @submit.prevent="add">
            <v-text-field v-model="name" label="Name" placeholder="Binder A, Shoebox 3, Top shelf" />
            <v-select v-model="kind" :items="KINDS" label="Kind">
              <template #item="{ props: p, item }">
                <v-list-item v-bind="p" :prepend-icon="item.raw.icon" />
              </template>
            </v-select>
            <v-btn type="submit" color="primary" variant="flat" block :disabled="!name.trim()" :loading="adding" prepend-icon="mdi-plus">
              Add location
            </v-btn>
          </form>
        </v-card>
      </v-col>

      <v-col cols="12" lg="8">
        <v-card>
          <div class="card-head">
            <div><h2><v-icon icon="mdi-archive-outline" size="18" />All locations</h2><p>Click a row to see its cards.</p></div>
          </div>
          <v-list v-if="xs" class="py-0">
            <div v-if="!loading && !locations.length" class="py-8 px-4 text-secondary-c">No locations yet. Add your first one, then pick it when you scan.</div>
            <template v-for="(l, n) in locations" :key="l.id">
              <v-divider v-if="n" />
              <v-list-item class="py-2" @click="open(l)">
                <template #prepend><div class="loc-icon mr-3"><v-icon :icon="kindIcon(l.kind)" size="18" /></div></template>
                <v-list-item-title class="font-weight-medium">{{ l.name }}</v-list-item-title>
                <v-list-item-subtitle><span class="text-capitalize">{{ l.kind }}</span> · <span class="num">{{ l.card_count }}</span> {{ l.card_count === 1 ? 'card' : 'cards' }}</v-list-item-subtitle>
                <template #append>
                  <v-btn icon="mdi-delete-outline" variant="text" color="error" :aria-label="`Delete ${l.name}`" @click.stop="pendingDelete = l" />
                </template>
              </v-list-item>
            </template>
          </v-list>
          <v-data-table
            v-else
            :headers="headers"
            :items="locations"
            :loading="loading"
            items-per-page="25"
            @click:row="(_: unknown, r: { item: Location }) => open(r.item)"
          >
            <template #item.name="{ item }">
              <div class="d-flex align-center ga-3 py-2">
                <div class="loc-icon"><v-icon :icon="kindIcon(item.kind)" size="18" /></div>
                <span class="font-weight-medium">{{ item.name }}</span>
              </div>
            </template>
            <template #item.kind="{ item }"><span class="text-capitalize">{{ item.kind }}</span></template>
            <template #item.card_count="{ item }"><span class="num">{{ item.card_count }}</span></template>
            <template #item.created_at="{ item }"><span class="num text-secondary-c" :title="isoDate(item.created_at)">{{ isoDate(item.created_at).slice(0, 10) }}</span></template>
            <template #item.actions="{ item }">
              <v-btn icon="mdi-open-in-new" variant="text" size="small" :aria-label="`Open ${item.name}`" @click.stop="open(item)" />
              <v-btn icon="mdi-delete-outline" variant="text" size="small" color="error" :aria-label="`Delete ${item.name}`" @click.stop="pendingDelete = item" />
            </template>
            <template #no-data>
              <div class="py-8 text-secondary-c">No locations yet. Add your first one, then pick it when you scan.</div>
            </template>
          </v-data-table>
        </v-card>
      </v-col>
    </v-row>

    <v-dialog :model-value="!!pendingDelete" max-width="440" @update:model-value="(v) => !v && (pendingDelete = null)">
      <v-card v-if="pendingDelete">
        <div class="card-head"><h2><v-icon icon="mdi-alert-outline" color="error" size="18" />Delete location</h2></div>
        <div class="card-body">
          Delete <strong>{{ pendingDelete.name }}</strong>? Its {{ pendingDelete.card_count }}
          {{ pendingDelete.card_count === 1 ? 'card stays' : 'cards stay' }} in inventory as unassigned.
        </div>
        <v-card-actions class="px-4 pb-4">
          <v-spacer />
          <v-btn variant="text" @click="pendingDelete = null">Cancel</v-btn>
          <v-btn color="error" variant="flat" @click="confirmDelete">Delete</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.d-grid { display: grid; gap: 14px; }
.loc-icon { width: 32px; height: 32px; border-radius: 8px; display: grid; place-items: center; background: rgb(var(--v-theme-brand-muted)); color: rgb(var(--v-theme-primary)); }
</style>
