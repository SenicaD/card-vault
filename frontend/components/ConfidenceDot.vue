<script setup lang="ts">
// How sure a card's value is: a coloured dot (green high, amber medium, red low) whose tooltip says why.
const props = defineProps<{ confidence?: number | null; reasons?: string[] | null; pending?: number | null; showLabel?: boolean }>()

const level = computed(() => {
  const c = props.confidence
  if (c == null) return null
  return c >= 70 ? { color: 'success', label: 'High' } : c >= 40 ? { color: 'warning', label: 'Medium' } : { color: 'error', label: 'Low' }
})
const tip = computed(() => {
  if (!level.value) return ''
  const lines = [`${level.value.label} confidence (${props.confidence}/100)`, ...(props.reasons ?? []).map((r) => `• ${r}`)]
  if (props.pending != null) lines.push(`• A new price of ${money(props.pending)} is waiting for the next lookup to agree`)
  return lines.join('\n')
})
</script>

<template>
  <span v-if="level" class="conf" :title="tip" :aria-label="tip">
    <v-icon :color="level.color" size="10" icon="mdi-circle" />
    <span v-if="showLabel" class="text-caption ml-1">{{ level.label }} confidence</span>
    <v-icon v-if="pending != null" size="14" icon="mdi-timer-sand" class="ml-1 text-muted-c" />
  </span>
</template>

<style scoped>
.conf { display: inline-flex; align-items: center; margin-left: 6px; cursor: help; white-space: nowrap; }
</style>
