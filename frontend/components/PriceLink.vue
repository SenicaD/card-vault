<script setup lang="ts">
import type { EbayItem } from '~/types'

// A price that links to the eBay listing it came from, when we kept one.
defineProps<{ value: number | null | undefined; listing?: EbayItem | null; label?: string }>()
</script>

<template>
  <a
    v-if="listing?.url && value != null"
    :href="listing.url"
    target="_blank"
    rel="noopener"
    class="price-link"
    :title="`${label ? label + ': ' : ''}${listing.title}`"
    @click.stop
  >{{ money(value) }}<v-icon icon="mdi-open-in-new" size="11" class="ml-1" aria-hidden="true" /><span class="sr-only"> (opens the eBay listing)</span></a>
  <span v-else>{{ money(value) }}</span>
</template>

<style scoped>
.price-link { color: inherit; text-decoration: underline; text-decoration-color: rgb(var(--v-theme-border)); text-underline-offset: 3px; white-space: nowrap; }
.price-link:hover, .price-link:focus-visible { color: rgb(var(--v-theme-primary)); text-decoration-color: currentColor; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
</style>
