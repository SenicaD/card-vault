<script setup lang="ts">
import { useDisplay, useTheme } from 'vuetify'
import type { Stats } from '~/types'

const route = useRoute()
const theme = useTheme()
const { mdAndUp, xs } = useDisplay()
const { api } = useApi()

const drawer = ref(true)
const rail = ref(false)
watch(mdAndUp, (v) => (drawer.value = v), { immediate: true })

const nav = [
  { to: '/', title: 'Inventory', icon: 'mdi-view-dashboard-outline', match: (p: string) => p === '/' || p.startsWith('/cards') },
  { to: '/scan', title: 'Scan cards', icon: 'mdi-camera-outline', match: (p: string) => p.startsWith('/scan') },
  { to: '/locations', title: 'Storage', icon: 'mdi-archive-outline', match: (p: string) => p.startsWith('/locations') },
]

// Integration status, shown in the header so it's always clear whether data is live.
const modes = ref<Stats['modes'] | null>(null)
onMounted(async () => {
  modes.value = (await api<Stats>('/api/cards/stats').catch(() => null))?.modes ?? null
})
const demo = computed(() => modes.value && (modes.value.identifier === 'mock' || modes.value.ebay === 'mock'))

const isDark = computed(() => theme.global.current.value.dark)
function toggleTheme() {
  const next = isDark.value ? 'light' : 'dark'
  theme.global.name.value = next
  try { localStorage.setItem('cv-theme', next) } catch {}
}

const search = ref('')
function doSearch() {
  navigateTo({ path: '/', query: search.value ? { q: search.value } : {} })
}
</script>

<template>
  <v-navigation-drawer
    v-model="drawer"
    :rail="rail && mdAndUp"
    :permanent="mdAndUp"
    :temporary="!mdAndUp"
    width="240"
    color="surface"
    class="shell-nav"
  >
    <div class="brand" :class="{ 'brand--rail': rail && mdAndUp }">
      <div class="brand-mark"><v-icon icon="mdi-cards-outline" size="20" /></div>
      <div v-if="!(rail && mdAndUp)" class="brand-text">
        <div class="brand-name">CardVault</div>
        <div class="brand-sub">Collection manager</div>
      </div>
    </div>

    <v-list nav density="compact" class="px-2">
      <v-list-subheader v-if="!(rail && mdAndUp)" class="nav-heading">Workspace</v-list-subheader>
      <v-list-item
        v-for="n in nav"
        :key="n.to"
        :to="n.to"
        :prepend-icon="n.icon"
        :title="n.title"
        :active="n.match(route.path)"
        color="primary"
        rounded="lg"
        class="nav-item"
      />
    </v-list>

    <template #append>
      <div class="pa-2">
        <v-list nav density="compact">
          <v-list-item
            v-if="mdAndUp"
            :prepend-icon="rail ? 'mdi-chevron-double-right' : 'mdi-chevron-double-left'"
            :title="rail ? '' : 'Collapse'"
            rounded="lg"
            class="nav-item"
            @click="rail = !rail"
          />
        </v-list>
        <div v-if="!(rail && mdAndUp)" class="nav-footer">v0.1.0</div>
      </div>
    </template>
  </v-navigation-drawer>

  <v-app-bar flat height="64" color="surface" class="shell-bar">
    <v-app-bar-nav-icon v-if="!mdAndUp" @click="drawer = !drawer" />
    <div class="bar-search">
      <v-text-field
        v-model="search"
        prepend-inner-icon="mdi-magnify"
        :placeholder="xs ? 'Search cards' : 'Search cards by name, set, number'"
        density="compact"
        variant="solo-filled"
        flat
        rounded="lg"
        hide-details
        single-line
        @keydown.enter="doSearch"
      />
    </div>
    <v-spacer />
    <v-chip
      v-if="modes"
      :color="demo ? 'warning' : 'success'"
      variant="tonal"
      size="small"
      class="mr-2 d-none d-sm-flex"
      :prepend-icon="demo ? 'mdi-flask-outline' : 'mdi-check-circle-outline'"
    >
      {{ demo ? 'Demo mode' : 'Live' }}
      <v-tooltip activator="parent" location="bottom">
        Identification: {{ modes.identifier }} · eBay: {{ modes.ebay }}
      </v-tooltip>
    </v-chip>
    <v-btn
      :icon="isDark ? 'mdi-weather-sunny' : 'mdi-weather-night'"
      variant="text"
      size="small"
      :aria-label="isDark ? 'Switch to light theme' : 'Switch to dark theme'"
      @click="toggleTheme"
    />
    <div v-if="route.path.startsWith('/scan')" class="mr-2" />
    <v-btn v-else color="primary" variant="flat" prepend-icon="mdi-camera-plus-outline" to="/scan" class="ml-2 mr-3 d-none d-md-flex">
      Scan card
    </v-btn>
  </v-app-bar>

  <v-main class="shell-main">
    <div class="page">
      <slot />
    </div>
  </v-main>

  <v-btn
    v-if="!mdAndUp && !route.path.startsWith('/scan')"
    to="/scan"
    color="primary"
    icon="mdi-camera-plus-outline"
    size="large"
    elevation="4"
    class="scan-fab"
    aria-label="Scan a card"
  />
</template>

<style scoped>
.shell-nav { border-right: 1px solid rgb(var(--v-theme-border)) !important; }
.brand { display: flex; align-items: center; gap: 10px; height: 64px; padding: 0 16px; border-bottom: 1px solid rgb(var(--v-theme-border)); }
.brand--rail { justify-content: center; padding: 0; }
.brand-mark {
  width: 34px; height: 34px; border-radius: 9px; flex-shrink: 0;
  display: grid; place-items: center; color: #fff;
  background: linear-gradient(135deg, rgb(var(--v-theme-primary)), #7b5bff);
}
.brand-name { font-weight: 700; font-size: 15px; line-height: 1.1; }
.brand-sub { font-size: 11.5px; color: rgb(var(--v-theme-text-muted)); }
.nav-heading { font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: rgb(var(--v-theme-text-muted)); }
.nav-item :deep(.v-list-item-title) { font-weight: 500; font-size: 14px; }
.nav-footer { font-size: 11px; color: rgb(var(--v-theme-text-muted)); padding: 4px 12px 8px; }
.shell-bar { border-bottom: 1px solid rgb(var(--v-theme-border)) !important; padding-top: env(safe-area-inset-top, 0px); }
.bar-search { width: min(420px, 100%); margin-left: 16px; min-width: 0; }
@media (max-width: 599px) { .bar-search { margin-left: 4px; } }
.shell-main { background: rgb(var(--v-theme-background)); }
.scan-fab { position: fixed !important; right: 16px; bottom: calc(16px + env(safe-area-inset-bottom, 0px)); z-index: 1005; }
</style>
