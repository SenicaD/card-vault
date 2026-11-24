<script setup lang="ts">
import type { Card } from '~/types'

// Cards entered more than once (same name, grade, set, number and version). Merging keeps the first card's photo,
// prices and history, adds up the quantities and deletes the others.
const open = defineModel<boolean>({ required: true })
const props = defineProps<{ groups: Card[][] }>()
const emit = defineEmits<{ merged: [] }>()
const { api } = useApi()
const busy = ref<number | null>(null)
const error = ref('')

async function merge(group: Card[]) {
  const [keep, ...rest] = group
  busy.value = keep.id; error.value = ''
  try {
    await api<Card>('/api/cards/merge', { method: 'POST', body: { keep: keep.id, merge: rest.map((c) => c.id) } })
    emit('merged')
  } catch (e: any) { error.value = e.message } finally { busy.value = null }
}
const line = (c: Card) => [c.year, c.set_name, c.card_number && '#' + c.card_number.replace(/^#/, ''), c.grade, c.parallel].filter(Boolean).join(' · ')
</script>

<template>
  <v-dialog v-model="open" max-width="640" scrollable>
    <v-card>
      <div class="card-head"><div><h2><v-icon icon="mdi-content-duplicate" size="18" />Possible duplicates</h2>
        <p>The same card entered more than once. Merging keeps the first one's photo and price history, adds up the quantities and deletes the rest.</p></div></div>
      <v-divider />
      <v-card-text>
        <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-3" :text="error" />
        <p v-if="!props.groups.length" class="text-body-2 text-muted-c">No duplicates left.</p>
        <div v-for="g in props.groups" :key="g[0].id" class="group">
          <div class="d-flex justify-space-between align-center ga-2">
            <div class="min-w-0">
              <strong class="truncate d-block">{{ g[0].name }}</strong>
              <span class="text-caption text-secondary-c">{{ line(g[0]) || '—' }}</span>
            </div>
            <v-btn size="small" color="primary" variant="flat" prepend-icon="mdi-merge" :loading="busy === g[0].id" @click="merge(g)">
              Merge {{ g.length }} into 1 (qty {{ g.reduce((n, c) => n + c.quantity, 0) }})
            </v-btn>
          </div>
          <div class="text-caption text-muted-c mt-1">
            <NuxtLink v-for="(c, i) in g" :key="c.id" :to="`/cards/${c.id}`" class="mr-2">#{{ c.id }}{{ i === 0 ? ' (kept)' : '' }} · qty {{ c.quantity }}</NuxtLink>
          </div>
        </div>
      </v-card-text>
      <v-divider />
      <v-card-actions><v-spacer /><v-btn variant="text" @click="open = false">Close</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.group { padding: 10px 0; border-bottom: 1px solid rgb(var(--v-theme-border)); }
.min-w-0 { min-width: 0; }
</style>
