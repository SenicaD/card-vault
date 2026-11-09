<script setup lang="ts">
// Card photo in trading-card proportions (2.5 x 3.5); high-value cards get a holo-foil frame.
defineProps<{ src: string | null; alt: string; foil?: boolean }>()
</script>

<template>
  <div class="thumb" :class="{ foil }">
    <img v-if="src" :src="src" :alt="alt" loading="lazy" />
    <div v-else class="empty" aria-hidden="true"><v-icon icon="mdi-image-off-outline" /></div>
  </div>
</template>

<style scoped>
.thumb {
  aspect-ratio: 5 / 7; border-radius: 10px; padding: 2px;
  background: rgb(var(--v-theme-border)); overflow: hidden;
}
.thumb.foil { background: var(--foil); }
img, .empty {
  width: 100%; height: 100%; object-fit: cover; border-radius: 8px;
  background: rgb(var(--v-theme-surface-sunken));
}
.empty { display: grid; place-items: center; color: rgb(var(--v-theme-text-muted)); }
</style>
