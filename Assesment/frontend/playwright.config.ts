// Browser tests (check 2.12): the map under load, in current Chromium and Firefox.
//
//   npx playwright install chromium firefox    (once; downloads the browsers)
//   npm run e2e
//
// The API is not needed: each test answers the application's requests itself.
// The numbers are meaningful on the reference workstation only (P1); run the
// tests there, with a display, not in CI.
import { defineConfig, devices } from '@playwright/test'

const PORT = 5180

export default defineConfig({
  testDir: 'e2e',
  timeout: 120_000,
  // One test at a time: frame rates measured side by side measure each other.
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://localhost:${PORT}`,
    viewport: { width: 1600, height: 1000 },
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1600, height: 1000 } } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'], viewport: { width: 1600, height: 1000 } } },
  ],
  webServer: {
    command: `npm run dev -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: true,
  },
})
