<script setup lang="ts">
import type { IdentTraceRow } from '~/types'

// How the card's photo was identified: what read it, what it concluded, and which listings it read titles from.
const props = defineProps<{ cardId: number; version?: string | null }>()
const { api } = useApi()
const chain = ref<IdentTraceRow[]>([])
const error = ref('')
async function load() {
  try { chain.value = await api<IdentTraceRow[]>(`/api/cards/${props.cardId}/debug/identification`) } catch (e: any) { error.value = e.message }
}
onMounted(load)
watch(() => props.version, load)
const pct = (v: unknown) => (typeof v === 'number' ? Math.round(v * 100) + '%' : '—')
const fields = (r: Record<string, any>) => [['Name', r.name], ['Set', r.set_name], ['Year', r.year], ['Number', r.card_number], ['Variant', r.variant], ['Grade', r.grade], ['Search words', r.search_query]]
  .filter(([, v]) => v)
const boxText = (b: { x: number; y: number; w: number; h: number }) => `${Math.round(b.x * 100)}% from the left, ${Math.round(b.y * 100)}% from the top, ${Math.round(b.w * 100)}% wide, ${Math.round(b.h * 100)}% tall`
</script>

<template>
  <div>
    <v-alert v-if="error" type="error" variant="tonal" density="compact" :text="error" />
    <p v-if="!chain.length && !error" class="text-body-2 text-muted-c mb-0">No identification record for this card's photo. It was added before debug records existed, or by hand.</p>
    <div v-for="(r, n) in chain" :key="r.id" class="ident" :class="{ older: n > 0 }">
      <div class="text-body-2 font-weight-medium mb-1">
        {{ n === 0 ? 'Current photo' : 'Original photo it was cut from' }} · {{ stamp(r.created_at) }}
        <v-chip size="x-small" variant="tonal" class="ml-1">{{ TRIGGER_LABEL[r.trigger ?? ''] ?? r.trigger }}</v-chip>
      </div>
      <template v-if="r.identity">
        <div class="text-body-2 mb-2">
          Read by <strong>{{ r.identity.identifier }}</strong><template v-if="r.identity.photo_kb"> from a {{ r.identity.photo_kb }} KB photo</template>,
          confidence <strong class="num">{{ pct(r.identity.result.confidence) }}</strong>.
          <template v-if="r.identity.crop">Cropped: {{ boxText(r.identity.crop) }}.</template>
        </div>
        <v-table density="compact" class="mb-2"><tbody><tr v-for="[k, v] in fields(r.identity.result)" :key="k"><td class="text-muted-c" style="width: 130px">{{ k }}</td><td>{{ v }}</td></tr></tbody></v-table>
        <div v-if="r.identity.matches.length">
          <div class="text-caption text-muted-c mb-1">
            eBay matched {{ r.identity.matched_total }} listings to the photo. The details were read from the first {{ r.identity.read_limit }} that aren't lots or reprints (marked "Read").
          </div>
          <div v-for="m in r.identity.matches.slice(0, 20)" :key="m.id" class="mrow" :class="{ dim: !m.read }">
            <v-chip size="x-small" :color="m.read ? 'success' : 'grey'" variant="tonal">{{ m.junk ? 'Skipped' : m.read ? 'Read' : 'Not read' }}</v-chip>
            <a :href="m.u || undefined" target="_blank" rel="noopener" class="truncate">{{ m.t }}</a><span class="num text-no-wrap">{{ money(m.p) }}</span>
          </div>
        </div>
      </template>
      <v-alert v-for="note in r.notes ?? []" :key="note" type="info" variant="tonal" density="compact" class="mt-2" :text="note" />
    </div>
  </div>
</template>

<style scoped>
.ident { padding-bottom: 14px; margin-bottom: 14px; border-bottom: 1px solid rgb(var(--v-theme-border)); }
.ident:last-child { border-bottom: 0; margin-bottom: 0; }
.ident.older { opacity: 0.8; }
.mrow { display: grid; grid-template-columns: auto 1fr auto; gap: 10px; align-items: center; font-size: 13px; padding: 3px 0; }
.mrow.dim { opacity: 0.6; }
.mrow a { color: inherit; text-decoration: none; } .mrow a:hover { color: rgb(var(--v-theme-primary)); }
</style>
