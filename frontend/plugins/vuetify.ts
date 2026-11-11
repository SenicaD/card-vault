// Theme follows the HMVP / @its/ui-core conventions: one brand colour, a tinted
// canvas, white bordered cards, light chrome, and semantic status colours that
// never follow the brand.
import { createVuetify, type ThemeDefinition } from 'vuetify'

const BRAND = '#2557d6'

const light: ThemeDefinition = {
  dark: false,
  colors: {
    primary: BRAND,
    brand: BRAND,
    'brand-muted': '#e8eefc',
    background: '#eef1f5',
    surface: '#ffffff',
    'surface-chrome': '#f7f8fa',
    'surface-sunken': '#e6eaf0',
    border: '#dde2e9',
    'on-surface': '#18202e',
    'on-background': '#18202e',
    text: '#18202e',
    'text-secondary': '#5a6577',
    'text-muted': '#8a94a5',
    success: '#1f8a5b',
    warning: '#b7770f',
    error: '#c4321f',
    info: '#1f7ae0',
    sold: '#6b7280',
  },
}

const dark: ThemeDefinition = {
  dark: true,
  colors: {
    primary: '#5b86ff',
    brand: '#5b86ff',
    'brand-muted': '#1b2440',
    background: '#0f1216',
    surface: '#171a1f',
    'surface-chrome': '#1c2026',
    'surface-sunken': '#14171b',
    border: '#2a2f36',
    'on-surface': '#e7e9ed',
    'on-background': '#e7e9ed',
    text: '#e7e9ed',
    'text-secondary': '#aab0ba',
    'text-muted': '#767c88',
    success: '#3fbf86',
    warning: '#e3a548',
    error: '#f0574a',
    info: '#3b82f6',
    sold: '#8b93a3',
  },
}

function initialTheme() {
  try {
    const saved = localStorage.getItem('cv-theme')
    if (saved === 'light' || saved === 'dark') return saved
  } catch {}
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export default defineNuxtPlugin((nuxtApp) => {
  const vuetify = createVuetify({
    theme: { defaultTheme: initialTheme(), themes: { light, dark } },
    defaults: {
      VBtn: { rounded: 'lg', class: 'text-none', style: 'letter-spacing: 0' },
      VCard: { rounded: 'lg', flat: true, border: true },
      VChip: { rounded: 'lg' },
      VList: { density: 'comfortable' },
      VTextField: { variant: 'outlined', density: 'compact', rounded: 'lg', hideDetails: 'auto', color: 'primary' },
      VSelect: { variant: 'outlined', density: 'compact', rounded: 'lg', hideDetails: 'auto', color: 'primary' },
      VCombobox: { variant: 'outlined', density: 'compact', rounded: 'lg', hideDetails: 'auto', color: 'primary' },
      VTextarea: { variant: 'outlined', density: 'compact', rounded: 'lg', hideDetails: 'auto', color: 'primary', rows: 3 },
      VDataTable: { density: 'comfortable', hover: true },
    },
  })
  nuxtApp.vueApp.use(vuetify)
})
