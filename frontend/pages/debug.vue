<script setup lang="ts">
import type { EbayEvent, JobRunRow } from '~/types'

const { api } = useApi()
const tab = ref('jobs')
const error = ref('')

interface Health {
  version: string; uptime_seconds: number; started_at: string; build_time: string; identifier: string
  ebay: { mode: string; env: string; marketplace: string; credentials_set: boolean; token_seconds_left: number | null }
  refresh: { every_hours: number; enabled: boolean }
  db: { path?: string; size_mb?: number; tables: Record<string, number> }
  disk?: { free_gb: number; total_gb: number }
  images: { count: number; size_mb: number }
  backups: { name: string; size_mb: number; modified: string }[]
}
const jobs = ref<{ runs: JobRunRow[]; current: any; daily_enabled: boolean; every_hours: number } | null>(null)
const events = ref<{ events: EbayEvent[]; last_hour: { calls: number; errors: number; rate_limited: number; avg_ms: number | null } } | null>(null)
const health = ref<Health | null>(null)
const test = ref<{ ok: boolean; ms: number; returned?: number; error?: string } | null>(null)
const testing = ref(false)
const expanded = ref<number[]>([])

async function load() {
  error.value = ''
  try {
    ;[jobs.value, events.value, health.value] = await Promise.all([
      api('/api/debug/jobs'), api('/api/debug/events'), api('/api/debug/health'),
    ]) as any
  } catch (e: any) { error.value = e.message }
}
async function runTest() {
  testing.value = true
  try { test.value = await api('/api/debug/health/ebay-test', { method: 'POST' }); await load() } finally { testing.value = false }
}
onMounted(load)

const dur = (a: string, b: string | null) => (b ? `${Math.max(0, Math.round((Date.parse(b) - Date.parse(a)) / 1000))} s` : 'running')
const up = (s: number) => (s < 3600 ? `${Math.round(s / 60)} min` : s < 86400 ? `${(s / 3600).toFixed(1)} h` : `${(s / 86400).toFixed(1)} days`)
const eventHeaders = [{ title: 'Time', key: 'ts' }, { title: 'Why', key: 'purpose' }, { title: 'Call', key: 'api' }, { title: 'What', key: 'detail', sortable: false }, { title: 'Results', key: 'returned', align: 'end' as const }, { title: 'Time taken', key: 'ms', align: 'end' as const }]
</script>

<template>
  <div>
    <PageHeader title="Debug" subtitle="What the app has been doing behind the scenes: background jobs, calls to eBay, and the health of this install.">
      <template #actions><v-btn variant="outlined" prepend-icon="mdi-refresh" @click="load">Refresh</v-btn></template>
    </PageHeader>
    <v-alert v-if="error" type="error" variant="tonal" border="start" class="mb-4" :text="error" />

    <v-tabs v-model="tab" class="mb-4">
      <v-tab value="jobs">Background jobs</v-tab>
      <v-tab value="ebay">eBay calls
        <v-chip v-if="events?.last_hour.errors" size="x-small" color="error" class="ml-2">{{ events.last_hour.errors }}</v-chip></v-tab>
      <v-tab value="health">Health</v-tab>
    </v-tabs>

    <v-window v-model="tab">
      <!-- Jobs -->
      <v-window-item value="jobs">
        <v-alert v-if="jobs && !jobs.daily_enabled" type="info" variant="tonal" class="mb-3">
          The daily re-price is off: it only runs against live eBay data and when the refresh interval is above zero.
        </v-alert>
        <v-alert v-else-if="jobs" type="success" variant="tonal" density="compact" class="mb-3">
          The daily re-price checks for cards due about every hour and re-prices any not priced in the last {{ jobs.every_hours }} hours.
        </v-alert>
        <v-card v-if="jobs?.current?.state === 'running'" class="mb-3"><div class="card-body">
          A bulk re-price is running: {{ jobs.current.done }} of {{ jobs.current.total }} done, {{ jobs.current.failed }} failed.
        </div></v-card>
        <v-card>
          <div class="card-head"><div><h2><v-icon icon="mdi-history" size="18" />Runs</h2><p>The last 100 runs of the daily job and of bulk re-prices you started. Click a row with failures to see why.</p></div></div>
          <p v-if="jobs && !jobs.runs.length" class="px-5 pb-4 text-body-2 text-muted-c">No runs recorded yet.</p>
          <v-table v-else density="compact">
            <thead><tr><th>Started</th><th>Kind</th><th class="text-right">Cards</th><th class="text-right">Priced</th><th class="text-right">Failed</th><th class="text-right">Took</th></tr></thead>
            <tbody>
              <template v-for="r in jobs?.runs ?? []" :key="r.id">
                <tr :class="{ clickable: r.failed }" @click="r.failed && (expanded = expanded.includes(r.id) ? expanded.filter((x) => x !== r.id) : [...expanded, r.id])">
                  <td class="num text-no-wrap">{{ stamp(r.started_at) }}</td><td>{{ r.kind === 'daily' ? 'Daily refresh' : 'Bulk re-price' }}</td>
                  <td class="text-right num">{{ r.total }}</td><td class="text-right num">{{ r.refreshed }}</td>
                  <td class="text-right num" :class="{ 'text-error': r.failed }">{{ r.failed }}</td><td class="text-right num">{{ dur(r.started_at, r.finished_at) }}</td>
                </tr>
                <tr v-if="expanded.includes(r.id)"><td colspan="6" class="errs">
                  <div v-for="e in r.errors ?? []" :key="e.card_id" class="errline">
                    <NuxtLink :to="`/cards/${e.card_id}?debug=1`">{{ e.name || e.query }}</NuxtLink><span class="text-error">{{ e.error }}</span>
                  </div>
                </td></tr>
              </template>
            </tbody>
          </v-table>
        </v-card>
      </v-window-item>

      <!-- eBay calls -->
      <v-window-item value="ebay">
        <v-row dense class="mb-2" v-if="events">
          <v-col cols="6" md="3"><StatCard label="Calls, last hour" :value="events.last_hour.calls" icon="mdi-lan-connect" color="primary" /></v-col>
          <v-col cols="6" md="3"><StatCard label="Errors" :value="events.last_hour.errors" icon="mdi-alert-circle-outline" :color="events.last_hour.errors ? 'error' : 'success'" /></v-col>
          <v-col cols="6" md="3"><StatCard label="Rate-limited" :value="events.last_hour.rate_limited" icon="mdi-speedometer-slow" :color="events.last_hour.rate_limited ? 'warning' : 'success'" sub="eBay said slow down" /></v-col>
          <v-col cols="6" md="3"><StatCard label="Average time" :value="events.last_hour.avg_ms != null ? events.last_hour.avg_ms + ' ms' : '—'" icon="mdi-timer-outline" color="info" /></v-col>
        </v-row>
        <v-card>
          <div class="card-head"><div><h2><v-icon icon="mdi-swap-horizontal" size="18" />Recent calls to eBay</h2><p>Kept in memory since the app last started, newest first. Item checks are the one-by-one "is it still listed?" lookups.</p></div></div>
          <v-data-table :headers="eventHeaders" :items="events?.events ?? []" density="compact" :items-per-page="25" class="no-pointer">
            <template #item.ts="{ item }"><span class="num text-no-wrap">{{ new Date(item.ts).toLocaleTimeString() }}</span></template>
            <template #item.detail="{ item }"><div class="detail">{{ item.detail }}</div>
              <div v-if="item.error" class="text-error text-caption">{{ item.rate_limited ? 'Rate limited: ' : '' }}{{ item.error }}</div></template>
            <template #item.returned="{ item }"><span class="num">{{ item.returned ?? '—' }}</span></template>
            <template #item.ms="{ item }"><span class="num">{{ item.ms }} ms</span></template>
          </v-data-table>
        </v-card>
      </v-window-item>

      <!-- Health -->
      <v-window-item value="health">
        <v-row v-if="health">
          <v-col cols="12" md="6">
            <v-card class="h-100">
              <div class="card-head"><div><h2><v-icon icon="mdi-server-outline" size="18" />This install</h2></div></div>
              <dl class="facts card-body pt-0">
                <div><dt>Version</dt><dd>{{ health.version }}</dd></div>
                <div><dt>Built</dt><dd class="num">{{ stamp(health.build_time) }}</dd></div>
                <div><dt>Running for</dt><dd>{{ up(health.uptime_seconds) }}</dd></div>
                <div><dt>Identifier</dt><dd>{{ health.identifier }}</dd></div>
                <div><dt>Daily re-price</dt><dd>{{ health.refresh.enabled ? `every ${health.refresh.every_hours} h` : 'off' }}</dd></div>
                <div v-if="health.disk"><dt>Disk free</dt><dd>{{ health.disk.free_gb }} of {{ health.disk.total_gb }} GB</dd></div>
                <div><dt>Photos</dt><dd>{{ health.images.count }} files, {{ health.images.size_mb }} MB</dd></div>
              </dl>
            </v-card>
          </v-col>
          <v-col cols="12" md="6">
            <v-card class="h-100">
              <div class="card-head"><div><h2><v-icon icon="mdi-shopping-outline" size="18" />eBay connection</h2></div>
                <v-btn size="small" variant="outlined" :loading="testing" @click="runTest">Test connection</v-btn></div>
              <dl class="facts card-body pt-0">
                <div><dt>Mode</dt><dd>{{ health.ebay.mode }} ({{ health.ebay.env }}, {{ health.ebay.marketplace }})</dd></div>
                <div><dt>Credentials</dt><dd>{{ health.ebay.credentials_set ? 'set' : 'not set' }}</dd></div>
                <div><dt>Access token</dt><dd>{{ health.ebay.token_seconds_left != null ? `valid for ${Math.round(health.ebay.token_seconds_left / 60)} more minutes` : 'none yet (fetched on first call)' }}</dd></div>
              </dl>
              <v-alert v-if="test" :type="test.ok ? 'success' : 'error'" variant="tonal" density="compact" class="mx-5 mb-4">
                {{ test.ok ? `Worked: eBay answered in ${test.ms} ms.` : `Failed after ${test.ms} ms: ${test.error}` }}
              </v-alert>
            </v-card>
          </v-col>
          <v-col cols="12" md="6">
            <v-card class="h-100">
              <div class="card-head"><div><h2><v-icon icon="mdi-database-outline" size="18" />Database</h2><p v-if="health.db.path">{{ health.db.path }} · {{ health.db.size_mb }} MB</p></div></div>
              <dl class="facts card-body pt-0"><div v-for="(n, t) in health.db.tables" :key="t"><dt>{{ t }}</dt><dd class="num">{{ n.toLocaleString() }} rows</dd></div></dl>
            </v-card>
          </v-col>
          <v-col cols="12" md="6">
            <v-card class="h-100">
              <div class="card-head"><div><h2><v-icon icon="mdi-backup-restore" size="18" />Backups</h2><p>Copies of the database kept next to it, newest first.</p></div></div>
              <p v-if="!health.backups.length" class="px-5 pb-4 text-body-2 text-muted-c">No backup files found next to the database.</p>
              <dl v-else class="facts card-body pt-0"><div v-for="b in health.backups" :key="b.name"><dt>{{ b.name }}</dt><dd class="num">{{ stamp(b.modified) }} · {{ b.size_mb }} MB</dd></div></dl>
            </v-card>
          </v-col>
        </v-row>
      </v-window-item>
    </v-window>
  </div>
</template>

<style scoped>
.facts > div { display: flex; justify-content: space-between; gap: 12px; padding: 8px 0; border-bottom: 1px solid rgb(var(--v-theme-border)); font-size: 13.5px; }
.facts > div:last-child { border-bottom: 0; }
.facts dt { color: rgb(var(--v-theme-text-secondary)); flex-shrink: 0; }
.facts dd { margin: 0; font-weight: 500; text-align: right; overflow-wrap: anywhere; }
.detail { max-width: 380px; overflow-wrap: anywhere; font-size: 13px; }
.clickable { cursor: pointer; }
.errs { background: rgb(var(--v-theme-surface-sunken)); }
.errline { display: grid; grid-template-columns: minmax(120px, 220px) 1fr; gap: 12px; font-size: 13px; padding: 3px 0; }
</style>
