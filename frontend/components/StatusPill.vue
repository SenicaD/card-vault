<script setup lang="ts">
import { STATUS_LABELS, type Status } from '~/types'
defineProps<{ status: Status; size?: string }>()
const COLORS: Record<Status, string> = { in_stock: 'success', listed: 'warning', sold: 'sold' }
</script>

<template>
  <v-chip :color="COLORS[status]" variant="tonal" :size="size || 'small'" class="font-weight-medium flex-shrink-0">
    <span class="dot" />{{ STATUS_LABELS[status] }}
  </v-chip>
</template>

<style scoped>
.dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; margin-right: 6px; }
</style>
