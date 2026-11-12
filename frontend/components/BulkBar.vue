<script setup lang="ts">
import { STATUS_LABELS, type Location } from '~/types'

// Actions on the selected cards. Edits only send the fields you filled in.
const props = defineProps<{ ids: number[]; locations: Location[] }>()
const emit = defineEmits<{ clear: []; changed: []; reprice: [] }>()
const { api } = useApi()

const editing = ref(false)
const deleting = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')

const KEEP = '__keep__'
const blank = () => ({ location_id: KEEP as number | string | null, status: KEEP as string, grade: null as string | null, parallel: '', cost: '' as string | number, condition: KEEP as string | null })
const f = ref(blank())

const locationItems = computed(() => [
  { value: KEEP, title: 'Leave as is' }, { value: null, title: 'Unassigned' },
  ...props.locations.map((l) => ({ value: l.id, title: l.name })),
])
const statusItems = [{ value: KEEP, title: 'Leave as is' }, ...Object.entries(STATUS_LABELS).map(([value, title]) => ({ value, title }))]
const conditionItems = [{ value: KEEP, title: 'Leave as is' }, ...CONDITIONS.map((c) => ({ value: c.value, title: c.name }))]

const changes = computed(() => {
  const c: Record<string, unknown> = {}
  if (f.value.location_id !== KEEP) c.location_id = f.value.location_id
  if (f.value.status !== KEEP) c.status = f.value.status
  if (f.value.condition !== KEEP) c.condition = f.value.condition
  if (f.value.grade) c.grade = f.value.grade
  if (f.value.parallel.trim()) c.parallel = f.value.parallel.trim()
  if (f.value.cost !== '' && f.value.cost != null) c.cost = Number(f.value.cost)
  return c
})

async function run(fn: () => Promise<void>) {
  busy.value = true; error.value = ''
  try { await fn() } catch (e: any) { error.value = e.message } finally { busy.value = false }
}
const apply = () => run(async () => {
  const r = await api<{ updated: number }>('/api/cards/bulk/update', { method: 'POST', body: { ids: props.ids, changes: changes.value } })
  notice.value = `Updated ${r.updated} cards`
  editing.value = false
  f.value = blank()
  emit('changed')
})
const remove = () => run(async () => {
  const r = await api<{ deleted: number }>('/api/cards/bulk/delete', { method: 'POST', body: { ids: props.ids } })
  notice.value = `Deleted ${r.deleted} cards`
  deleting.value = false
  emit('clear'); emit('changed')
})
</script>

<template>
  <div class="bulkbar">
    <div class="d-flex align-center ga-2 flex-wrap">
      <strong class="num">{{ ids.length }} selected</strong>
      <v-btn size="small" variant="text" @click="emit('clear')">Clear</v-btn>
      <v-spacer />
      <v-btn size="small" variant="tonal" prepend-icon="mdi-pencil-outline" @click="editing = true">Edit</v-btn>
      <v-btn size="small" variant="tonal" prepend-icon="mdi-refresh" @click="emit('reprice')">Re-price</v-btn>
      <v-btn size="small" variant="tonal" color="error" prepend-icon="mdi-delete-outline" @click="deleting = true">Delete</v-btn>
    </div>
    <v-snackbar :model-value="!!notice" timeout="3000" @update:model-value="notice = ''">{{ notice }}</v-snackbar>

    <v-dialog v-model="editing" max-width="520">
      <v-card>
        <v-card-title>Edit {{ ids.length }} cards</v-card-title>
        <v-card-text>
          <p class="text-body-2 text-secondary-c mb-4">Only the fields you change are applied. Changing grade or version clears each card's value until it is re-priced.</p>
          <v-alert v-if="error" type="error" variant="tonal" class="mb-3" :text="error" />
          <v-row dense>
            <v-col cols="12" sm="6"><v-select v-model="f.location_id" :items="locationItems" label="Move to" /></v-col>
            <v-col cols="12" sm="6"><v-select v-model="f.status" :items="statusItems" label="Status" /></v-col>
            <v-col cols="12" sm="6"><v-combobox v-model="f.grade" :items="GRADE_OPTIONS" label="Grade" placeholder="Leave as is" clearable /></v-col>
            <v-col cols="12" sm="6"><v-select v-model="f.condition" :items="conditionItems" label="Condition" /></v-col>
            <v-col cols="12" sm="6"><v-text-field v-model="f.parallel" label="Version" placeholder="Leave as is" /></v-col>
            <v-col cols="12" sm="6"><v-text-field v-model="f.cost" label="Paid per card" type="number" min="0" step="0.01" prefix="$" /></v-col>
          </v-row>
        </v-card-text>
        <v-card-actions>
          <v-spacer /><v-btn variant="text" @click="editing = false">Cancel</v-btn>
          <v-btn color="primary" variant="flat" :loading="busy" :disabled="!Object.keys(changes).length" @click="apply">Apply to {{ ids.length }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="deleting" max-width="420">
      <v-card>
        <v-card-title>Delete {{ ids.length }} cards?</v-card-title>
        <v-card-text>This removes the cards with their photos' records, price history and tracked listings. It can't be undone.
          <v-alert v-if="error" type="error" variant="tonal" class="mt-3" :text="error" />
        </v-card-text>
        <v-card-actions>
          <v-spacer /><v-btn variant="text" @click="deleting = false">Cancel</v-btn>
          <v-btn color="error" variant="flat" :loading="busy" @click="remove">Delete {{ ids.length }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.bulkbar { position: sticky; top: 64px; z-index: 5; padding: 10px 18px; background: rgb(var(--v-theme-brand-muted)); border-bottom: 1px solid rgb(var(--v-theme-border)); }
</style>
