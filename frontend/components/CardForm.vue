<script setup lang="ts">
import { CATEGORY_LABELS, type CardFields, type Location } from '~/types'

const model = defineModel<CardFields>({ required: true })
const props = defineProps<{ locations: Location[] }>()

const categories = Object.entries(CATEGORY_LABELS).map(([value, title]) => ({ value, title }))
const conditions = [{ value: null, title: '—' }, ...['NM', 'LP', 'MP', 'HP', 'DMG'].map((c) => ({ value: c, title: c }))]
const locationItems = computed(() => [
  { value: null, title: 'Unassigned' },
  ...props.locations.map((l) => ({ value: l.id, title: l.name })),
])
</script>

<template>
  <div class="form">
    <div class="section-label">Identification</div>
    <v-row dense>
      <v-col cols="12" sm="6"><v-select v-model="model.category" :items="categories" label="Type" /></v-col>
      <v-col cols="12" sm="6"><v-text-field v-model="model.year" label="Year" inputmode="numeric" /></v-col>
      <v-col cols="12"><v-text-field v-model="model.name" :label="model.category === 'sports' ? 'Player' : 'Card name'" required /></v-col>
      <v-col cols="12" sm="8"><v-text-field v-model="model.set_name" label="Set" /></v-col>
      <v-col cols="12" sm="4"><v-text-field v-model="model.card_number" label="Card number" /></v-col>
      <v-col cols="12"><v-text-field v-model="model.variant" label="Variant or parallel" placeholder="Refractor, 1st Edition, Holo…" /></v-col>
      <v-col cols="12" sm="6"><v-text-field v-model="model.grade" label="Grade" placeholder="Raw, PSA 10…" /></v-col>
      <v-col cols="12" sm="6"><v-select v-model="model.condition" :items="conditions" label="Condition" /></v-col>
    </v-row>

    <div class="section-label mt-4">Storage</div>
    <v-row dense>
      <v-col cols="12" sm="6"><v-select v-model="model.location_id" :items="locationItems" label="Stored in" /></v-col>
      <v-col cols="12" sm="6"><v-text-field v-model="model.slot" label="Slot" placeholder="Page 3, pocket 5" /></v-col>
      <v-col cols="12" sm="4"><v-text-field v-model.number="model.quantity" label="Quantity" type="number" min="1" /></v-col>
      <v-col cols="12" sm="8"><v-text-field v-model="model.search_query" label="eBay search" prepend-inner-icon="mdi-magnify" /></v-col>
      <v-col cols="12"><v-textarea v-model="model.notes" label="Notes" /></v-col>
    </v-row>
  </div>
</template>

<style scoped>
.section-label { font-size: 11.5px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: rgb(var(--v-theme-text-muted)); margin-bottom: 8px; }
</style>
