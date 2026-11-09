import vuetify, { transformAssetUrls } from 'vite-plugin-vuetify'

// The API is proxied through Nuxt so the phone only needs to reach one host (port 3000).
const apiBase = process.env.NUXT_API_BASE || 'http://localhost:8000'

export default defineNuxtConfig({
  compatibilityDate: '2024-11-01',
  ssr: false,
  devtools: { enabled: false },
  css: ['vuetify/styles', '@mdi/font/css/materialdesignicons.css', '~/assets/css/main.css'],
  build: { transpile: ['vuetify'] },
  devServer: { host: '0.0.0.0', port: 3000 },
  vite: {
    vue: { template: { transformAssetUrls } },
    plugins: [vuetify({ autoImport: true })],
  },
  nitro: {
    routeRules: {
      '/api/**': { proxy: `${apiBase}/api/**` },
      '/images/**': { proxy: `${apiBase}/images/**` },
    },
  },
  app: {
    pageTransition: { name: 'page', mode: 'out-in' },
    head: {
      title: 'CardVault',
      htmlAttrs: { lang: 'en' },
      meta: [
        { name: 'viewport', content: 'width=device-width, initial-scale=1, viewport-fit=cover' },
        { name: 'theme-color', content: '#ffffff' },
      ],
      link: [
        { rel: 'preconnect', href: 'https://fonts.googleapis.com' },
        { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: '' },
        {
          rel: 'stylesheet',
          href: 'https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap',
        },
      ],
    },
  },
})
