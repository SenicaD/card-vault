<script setup lang="ts">
import type { RepriceStatus } from '~/types'

// Starts a background re-price and follows it: progress while it runs, then what moved.
const emit = defineEmits<{ finished: [] }>()
const { api } = useApi()
const status = ref<RepriceStatus>({ state: 'idle' })
const error = ref('')
const dismissed = ref(false)
let timer: ReturnType<typeof setInterval> | undefined

const pct = computed(() => (status.value.total ? Math.round(((status.value.done ?? 0) / status.value.total) * 100) : 0))
const visible = computed(() => !dismissed.value && status.value.state !== 'idle')

async function poll() {
  try {
    const was = status.value.state
    status.value = await api<RepriceStatus>('/api/cards/bulk/reprice')
    if (status.value.state !== 'running') {
      clearInterval(timer); timer = undefined
      if (was === 'running') emit('finished')
    }
  } catch (e: any) { error.value = e.message }
}
function follow() {
  dismissed.value = false
  if (!timer) timer = setInterval(poll, 1500)
  poll()
}
async function start(ids: number[] | null) {
  error.value = ''
  try {
    status.value = await api<RepriceStatus>('/api/cards/bulk/reprice', { method: 'POST', body: ids ? { ids } : undefined })
    follow()
  } catch (e: any) { error.value = e.message }
}
// A job may already be running from before this page was opened.
onMounted(async () => {
  try {
    const s = await api<RepriceStatus>('/api/cards/bulk/reprice')
    if (s.state === 'running') { status.value = s; follow() }
  } catch {}
})
onBeforeUnmount(() => clearInterval(timer))
defineExpose({ start })
</script>

<template>
  <v-alert v-if="error" type="error" variant="tonal" border="start" class="mb-4" closable :text="error" @click:close="error = ''" />
  <v-card v-if="visible" class="mb-4">
    <div class="card-body">
      <div class="d-flex align-center justify-space-between ga-3 flex-wrap">
        <div class="font-weight-medium">
          <v-icon :icon="status.state === 'running' ? 'mdi-sync' : 'mdi-check-circle-outline'" :class="{ spin: status.state === 'running' }" size="18" class="mr-1" />
          <template v-if="status.state === 'running'">Re-pricing {{ status.done }} of {{ status.total }}…</template>
          <template v-else>Re-priced {{ status.done }} of {{ status.total }}: {{ status.changed }} changed, {{ status.unchanged }} the same<template v-if="status.failed">, {{ status.failed }} failed</template></template>
        </div>
        <v-btn v-if="status.state === 'done'" size="small" variant="text" @click="dismissed = true">Dismiss</v-btn>
      </div>
      <v-progress-linear :model-value="pct" height="6" rounded class="my-3" :color="status.failed ? 'warning' : 'primary'" />
      <div v-if="status.state === 'running'" class="text-caption text-muted-c">You can keep working or leave this page. It continues in the background.</div>
      <div v-if="status.top_changes?.length" class="movers">
        <div class="text-caption text-muted-c mb-1">Biggest changes</div>
        <NuxtLink v-for="c in status.top_changes" :key="c.id" :to="`/cards/${c.id}`" class="mover">
          <span class="truncate">{{ c.name }}</span>
          <span class="num text-no-wrap">{{ money(c.before) }} → <strong>{{ money(c.after) }}</strong></span>
        </NuxtLink>
      </div>
    </div>
  </v-card>
</template>

<style scoped>
.spin { animation: spin 1.2s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.movers { display: grid; gap: 2px; }
.mover { display: flex; justify-content: space-between; gap: 12px; font-size: 13.5px; padding: 4px 0; color: inherit; text-decoration: none; border-bottom: 1px solid rgb(var(--v-theme-border)); }
.mover:hover { color: rgb(var(--v-theme-primary)); }
</style>
