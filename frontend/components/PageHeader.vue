<script setup lang="ts">
defineProps<{ title: string; subtitle?: string; crumbs?: { title: string; to?: string }[] }>()
</script>

<template>
  <header class="page-header">
    <div class="min-w-0">
      <v-breadcrumbs v-if="crumbs?.length" :items="crumbs" density="compact" class="pa-0 mb-1 crumbs">
        <template #divider><v-icon icon="mdi-chevron-right" size="14" /></template>
      </v-breadcrumbs>
      <h1 class="truncate">{{ title }}</h1>
      <p v-if="subtitle">{{ subtitle }}</p>
    </div>
    <div v-if="$slots.actions" class="actions"><slot name="actions" /></div>
  </header>
</template>

<style scoped>
.page-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-bottom: 20px; }
.min-w-0 { min-width: 0; }
h1 { font-size: 22px; font-weight: 700; line-height: 1.25; margin: 0; letter-spacing: -0.01em; }
p { margin: 4px 0 0; font-size: 14px; color: rgb(var(--v-theme-text-secondary)); }
.actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.crumbs { font-size: 13px; color: rgb(var(--v-theme-text-muted)); }
.crumbs :deep(.v-breadcrumbs-item--link) { color: rgb(var(--v-theme-text-secondary)); }
</style>
