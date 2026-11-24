<script setup lang="ts">
// A small listing picture that opens a larger preview on hover or keyboard focus; clicking opens the listing.
defineProps<{ src: string | null; url?: string | null; title?: string; dim?: boolean }>()
</script>

<template>
  <v-tooltip location="end" open-delay="150" max-width="none" content-class="thumb-pop">
    <template #activator="{ props }">
      <a v-bind="props" :href="url || undefined" target="_blank" rel="noopener" class="lt" :class="{ dim }" :aria-label="title ? `Open listing: ${title}` : 'Open listing'">
        <img v-if="src" :src="src" alt="" loading="lazy" />
        <v-icon v-else icon="mdi-image-off-outline" size="small" />
      </a>
    </template>
    <img v-if="src" :src="src" :alt="title || ''" class="lt-big" />
  </v-tooltip>
</template>

<style scoped>
.lt { width: 48px; height: 48px; border-radius: 6px; overflow: hidden; display: grid; place-items: center; background: rgb(var(--v-theme-surface-sunken)); color: rgb(var(--v-theme-text-muted)); }
.lt img { width: 100%; height: 100%; object-fit: cover; }
.lt.dim { opacity: 0.45; }
</style>

<style>
.thumb-pop { padding: 4px !important; background: rgb(var(--v-theme-surface)) !important; }
.lt-big { display: block; max-width: min(360px, 80vw); max-height: 420px; border-radius: 4px; }
</style>
