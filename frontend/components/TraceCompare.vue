<script setup lang="ts">
import type { TraceCompare } from '~/types'

// What changed between two lookups of the same card.
const props = defineProps<{ cardId: number; traceIds?: [number, number] | null }>()
const { api } = useApi()
const data = ref<TraceCompare | null>(null)
const error = ref('')
const loading = ref(false)

async function load() {
  loading.value = true; error.value = ''; data.value = null
  try {
    const q = props.traceIds ? { a: props.traceIds[0], b: props.traceIds[1] } : {}
    data.value = await api<TraceCompare>(`/api/cards/${props.cardId}/debug/compare`, { query: q })
  } catch (e: any) { error.value = e.message } finally { loading.value = false }
}
onMounted(load)
watch(() => props.traceIds, load)
const delta = (a: number | null, b: number | null) => (a == null || b == null ? '' : `${b - a >= 0 ? '+' : '−'}${money(Math.abs(b - a))}`)
</script>

<template>
  <div>
    <v-progress-linear v-if="loading" indeterminate />
    <v-alert v-if="error" type="info" variant="tonal" density="compact" :text="error" />
    <template v-if="data">
      <p class="text-body-2 mb-2">
        <strong>{{ stamp(data.a.created_at) }}</strong> ({{ TRIGGER_LABEL[data.a.trigger ?? ''] ?? data.a.trigger }})
        → <strong>{{ stamp(data.b.created_at) }}</strong> ({{ TRIGGER_LABEL[data.b.trigger ?? ''] ?? data.b.trigger }})
      </p>
      <v-alert v-if="data.search_changed.query || data.search_changed.mode || data.search_changed.version" type="warning" variant="tonal" density="compact" class="mb-3">
        The search itself was different, so the results aren't directly comparable:
        <span v-if="data.search_changed.query">the search words changed ("{{ data.a.query }}" → "{{ data.b.query }}"). </span>
        <span v-if="data.search_changed.mode">the search type changed ({{ data.a.mode }} → {{ data.b.mode }}). </span>
        <span v-if="data.search_changed.version">the version filter changed.</span>
      </v-alert>
      <v-table density="compact" class="mb-3">
        <thead><tr><th>Grade</th><th class="text-right">Before</th><th class="text-right">After</th><th class="text-right">Change</th><th class="text-right">Listings used</th></tr></thead>
        <tbody>
          <tr v-for="g in data.grades" :key="g.grade">
            <td>{{ g.grade }}</td><td class="text-right num">{{ money(g.a_median) }}</td><td class="text-right num">{{ money(g.b_median) }}</td>
            <td class="text-right num" :class="(g.b_median ?? 0) < (g.a_median ?? 0) ? 'text-error' : 'text-success'">{{ delta(g.a_median, g.b_median) || '—' }}</td>
            <td class="text-right num">{{ g.a_used ?? '—' }} → {{ g.b_used ?? '—' }}</td>
          </tr>
        </tbody>
      </v-table>
      <div class="text-caption text-muted-c mb-2">{{ data.unchanged }} listings were the same in both.</div>

      <template v-for="sec in [
        { k: 'new', title: 'New listings', rows: data.new }, { k: 'gone', title: 'No longer in the results', rows: data.gone },
      ]" :key="sec.k">
        <div v-if="sec.rows.length" class="mb-3">
          <div class="text-body-2 font-weight-medium mb-1">{{ sec.title }} ({{ sec.rows.length }})</div>
          <div v-for="r in sec.rows.slice(0, 15)" :key="r.id" class="crow"><span class="truncate">{{ r.t }}</span>
            <span class="num text-no-wrap"><v-chip size="x-small" :color="DECISION_COLOR[r.d]" variant="tonal" class="mr-2">{{ DECISION_LABEL[r.d] }}</v-chip>{{ money(r.p) }}</span></div>
          <div v-if="sec.rows.length > 15" class="text-caption text-muted-c">and {{ sec.rows.length - 15 }} more</div>
        </div>
      </template>
      <div v-if="data.price_changed.length" class="mb-3">
        <div class="text-body-2 font-weight-medium mb-1">Price changed ({{ data.price_changed.length }})</div>
        <div v-for="r in data.price_changed.slice(0, 15)" :key="r.id" class="crow"><span class="truncate">{{ r.t }}</span><span class="num text-no-wrap">{{ money(r.was) }} → <strong>{{ money(r.p) }}</strong></span></div>
      </div>
      <div v-if="data.decision_changed.length" class="mb-1">
        <div class="text-body-2 font-weight-medium mb-1">Treated differently ({{ data.decision_changed.length }})</div>
        <div v-for="r in data.decision_changed.slice(0, 15)" :key="r.id" class="crow"><span class="truncate">{{ r.t }}</span>
          <span class="text-no-wrap text-caption">{{ DECISION_LABEL[r.was_decision] }} → <strong>{{ DECISION_LABEL[r.d] }}</strong></span></div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.crow { display: flex; justify-content: space-between; gap: 12px; font-size: 13px; padding: 4px 0; border-bottom: 1px solid rgb(var(--v-theme-border)); }
</style>
