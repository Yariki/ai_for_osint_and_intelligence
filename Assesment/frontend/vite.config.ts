import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [vue(), vuetify()],
  server: {
    // Development only. In the deployed stack the proxy routes /api to the API.
    proxy: { '/api': 'http://localhost:8000' },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['src/test-setup.ts'],
    // Browser tests run under Playwright: npm run e2e.
    exclude: ['e2e/**', 'node_modules/**'],
    // Vuetify ships untransformed CSS imports, so Vitest must process it itself.
    server: { deps: { inline: ['vuetify'] } },
  },
})
