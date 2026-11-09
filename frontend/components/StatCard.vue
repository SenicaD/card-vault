<script setup lang="ts">
defineProps<{ label: string; value: string | number; icon: string; color?: string; sub?: string }>()
</script>

<template>
  <v-card class="stat">
    <div class="stat-inner">
      <div class="stat-icon" :class="`bg-${color || 'primary'}`"><v-icon :icon="icon" size="20" /></div>
      <div class="stat-text">
        <div class="stat-label">{{ label }}</div>
        <div class="stat-value num">{{ value }}</div>
        <div v-if="sub" class="stat-sub">{{ sub }}</div>
      </div>
    </div>
  </v-card>
</template>

<style scoped>
.stat { height: 100%; }
.stat-inner { display: flex; gap: 14px; align-items: flex-start; padding: 18px; }
.stat-icon { width: 40px; height: 40px; border-radius: 10px; display: grid; place-items: center; flex-shrink: 0; opacity: 0.92; }
.stat-text { min-width: 0; }
.stat-label { font-size: 12.5px; font-weight: 500; color: rgb(var(--v-theme-text-secondary)); }
.stat-value { font-size: 24px; font-weight: 700; line-height: 1.2; letter-spacing: -0.02em; margin-top: 2px; }
.stat-sub { font-size: 12px; color: rgb(var(--v-theme-text-muted)); margin-top: 2px; }
@media (max-width: 600px) {
  .stat-inner { padding: 14px; }
  .stat-icon { display: none; }
  .stat-value { font-size: 20px; white-space: nowrap; }
}
</style>
