<script setup lang="ts">
import type { TableHeader } from '~/composables/tables'

// One look and behaviour for client-side data tables: three-click sort with empty values last, search, paging and
// empty states. Attributes, listeners and slots it doesn't use go to the inner v-data-table.
defineOptions({ inheritAttrs: false })

const props = defineProps<{
  headers: TableHeader[]
  items: any[]
  searchPlaceholder: string
  emptyText?: string
  // The "M" in "N of M rows", when the page shows fewer items than it has (Matching listings' version switch)
  total?: number
}>()
const search = defineModel<string | null>('search', { default: '' })
const sortBy = defineModel<{ key: string; order?: boolean | 'asc' | 'desc' }[]>('sortBy', { default: () => [] })
const page = defineModel<number>('page', { default: 1 })
const itemsPerPage = defineModel<number>('itemsPerPage', { default: 10 })

const attrs = useAttrs()
const slots = useSlots()

const isDesc = (key: string) => sortBy.value.find((s) => s.key === key)?.order === 'desc'

const tableHeaders = computed(() =>
  props.headers.map(({ text, ...h }) => (h.sortable === false || h.sortRaw ? h : { ...h, sortRaw: columnCompare(h, () => isDesc(h.key)) })),
)
const rows = computed(() => searchRows(props.items, props.headers, search.value))
const total = computed(() => props.total ?? props.items.length)
const count = computed(() => {
  const unit = total.value === 1 ? 'row' : 'rows'
  return rows.value.length === total.value ? `${total.value} ${unit}` : `${rows.value.length} of ${total.value} ${unit}`
})
const clickable = computed(() => !!attrs['onClick:row'])
const passSlots = computed(() => Object.keys(slots).filter((n) => n !== 'no-data'))

watch(search, () => { page.value = 1 })
</script>

<template>
  <div>
    <div class="dt-toolbar">
      <v-text-field
        v-model="search"
        :placeholder="searchPlaceholder"
        prepend-inner-icon="mdi-magnify"
        clearable
        hide-details
        density="compact"
        class="dt-search"
      />
      <span class="dt-count text-secondary-c num">{{ count }}</span>
    </div>
    <v-data-table
      v-bind="$attrs"
      v-model:sort-by="sortBy"
      v-model:page="page"
      v-model:items-per-page="itemsPerPage"
      :headers="tableHeaders"
      :items="rows"
      :items-per-page-options="[10, 25, 50, 100]"
      density="comfortable"
      hover
      :class="{ 'dt-static': !clickable }"
    >
      <template v-for="name in passSlots" #[name]="scope"><slot :name="name" v-bind="scope ?? {}" /></template>
      <template #no-data>
        <div v-if="items.length" class="py-6 text-secondary-c">
          No rows match "{{ (search || '').trim() }}"
          <v-btn variant="text" size="small" color="primary" class="ml-1" @click="search = ''">Clear search</v-btn>
        </div>
        <slot v-else name="no-data"><div class="py-6 text-secondary-c">{{ emptyText }}</div></slot>
      </template>
    </v-data-table>
  </div>
</template>

<style scoped>
.dt-toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 16px; padding: 10px 16px; }
.dt-search { max-width: 280px; min-width: 180px; }
.dt-count { font-size: 12px; }
/* Rows that open nothing, and the empty-state row, keep the default cursor. */
.dt-static :deep(tbody tr), :deep(tbody tr.v-data-table-rows-no-data) { cursor: default; }
</style>
