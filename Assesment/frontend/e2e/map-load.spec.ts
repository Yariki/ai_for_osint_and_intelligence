// Check 2.12 (P1): with 10,000 hosts loaded, pan and zoom hold at least 30
// frames per second, and a filter change redraws within 1 second.
//
// NOT YET RUN: written on a machine without the browsers. The thresholds are
// those of the delivery plan; the result counts on the reference workstation only.
import { expect, type Page, type Route, test } from '@playwright/test'

const HOSTS = 10_000
const FILTERED = 1_250
const MIN_FPS = 30
const REDRAW_MS = 1000

/** A 1x1 grey PNG: every tile. The map draws it scaled; what matters is that tiles arrive. */
const TILE = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mN4/f/XfwAJ6AP0ZIH1+wAAAABJRU5ErkJggg==',
  'base64',
)

interface Point {
  address: string
  scan_id: number
  open_port_count: number
  latitude: number
  longitude: number
  accuracy_radius_km: number
}

/** Hosts in 400 cities of 25 each: clusters of co-located hosts, as city-level data gives. */
function points(count: number): Point[] {
  return Array.from({ length: count }, (_, index) => {
    const city = Math.floor(index / 25)
    return {
      address: `10.${Math.floor(index / 65536)}.${Math.floor(index / 256) % 256}.${index % 256}`,
      scan_id: 1,
      open_port_count: index % 7,
      latitude: -50 + ((city * 37) % 110),
      longitude: -170 + ((city * 83) % 340),
      accuracy_radius_km: 50,
    }
  })
}

const SCAN = {
  id: 1,
  project_id: 1,
  state: 'completed',
  phase: null,
  partial: false,
  failure_reason: null,
  failed_phase: null,
  technique_requested: 'automatic',
  technique_used: 'syn',
  targets: [],
  port_profile: 'top-1000',
  port_count: 1000,
  parameters: {},
  attestation_id: 1,
  vantage_point: 'default',
  created_at: '2026-11-17T09:00:00Z',
  started_at: '2026-11-17T09:00:00Z',
  finished_at: '2026-11-17T09:30:00Z',
  cancel_requested: false,
  retry_requested: false,
  enrichment_requested: false,
  dataset_versions: {},
  chunks_done: 1,
  chunks_total: 1,
  probes_sent: 0,
  receive_drops: 0,
  hosts_reachable: HOSTS,
  open_ports: 0,
}

function json(route: Route, body: unknown): Promise<void> {
  return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
}

/** Answer every request the workspace makes, with 10,000 hosts. */
async function serve(page: Page): Promise<void> {
  const all = points(HOSTS)
  await page.route('**/tiles/**', (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: TILE }),
  )
  await page.route('**/api/v1/**', (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace('/api/v1', '')
    const filtered = url.searchParams.has('country')
    const shown = filtered ? all.slice(0, FILTERED) : all
    switch (path) {
      case '/setup':
        return json(route, { setup_required: false })
      case '/session':
        return json(route, { username: 'analyst', expires_at: '2026-11-17T12:00:00Z' })
      case '/config':
        return json(route, {
          limits: {},
          port_profiles: [],
          scanner: null,
          tiles: {
            url_template: `${url.origin}/tiles/{z}/{x}/{y}.png`,
            attribution: '© test tiles',
          },
        })
      case '/projects/1/scans':
        return json(route, { items: [SCAN], total: 1, limit: 200, offset: 0 })
      case '/scans/1':
        return json(route, SCAN)
      case '/scans/1/hosts':
        return json(route, { items: [], total: shown.length, limit: 50, offset: 0 })
      case '/scans/1/map-points':
        return json(route, { located: shown, unlocated: [] })
      case '/events':
        return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': idle\n\n' })
      default:
        return route.fulfill({ status: 404, body: '{}' })
    }
  })
}

async function openWorkspace(page: Page): Promise<void> {
  await serve(page)
  await page.goto('/projects/1/workspace')
  await expect(page.getByTestId('map-count')).toContainText('10,000 hosts on the map')
  await page.locator('canvas.maplibregl-canvas').waitFor()
  // Let the first render and the tiles settle.
  await page.waitForTimeout(2000)
}

test('pan and zoom hold at least 30 frames per second with 10,000 hosts', async ({ page }) => {
  await openWorkspace(page)
  const box = await page.locator('canvas.maplibregl-canvas').boundingBox()
  if (!box) throw new Error('the map has no size')
  const centre = { x: box.x + box.width / 2, y: box.y + box.height / 2 }

  // Count the frames the browser paints while the map is moved.
  await page.evaluate(() => {
    const state = { frames: 0, running: true, started: performance.now(), stopped: 0 }
    ;(window as unknown as { __frames: typeof state }).__frames = state
    const tick = () => {
      state.frames++
      if (state.running) requestAnimationFrame(tick)
      else state.stopped = performance.now()
    }
    requestAnimationFrame(tick)
  })

  for (let round = 0; round < 4; round++) {
    // Pan: a drag across the map.
    await page.mouse.move(centre.x - 200, centre.y)
    await page.mouse.down()
    await page.mouse.move(centre.x + 200, centre.y + 80, { steps: 30 })
    await page.mouse.up()
    // Zoom in and out with the wheel.
    await page.mouse.move(centre.x, centre.y)
    for (let step = 0; step < 6; step++) {
      await page.mouse.wheel(0, round % 2 ? 240 : -240)
      await page.waitForTimeout(40)
    }
  }

  const measured = await page.evaluate(async () => {
    const state = (window as unknown as { __frames: { frames: number; running: boolean; started: number; stopped: number } }).__frames
    state.running = false
    await new Promise((resolve) => requestAnimationFrame(resolve))
    return { frames: state.frames, seconds: (state.stopped - state.started) / 1000 }
  })
  const fps = measured.frames / measured.seconds
  console.log(`${test.info().project.name}: ${fps.toFixed(1)} frames per second over ${measured.seconds.toFixed(1)} s`)
  expect(fps).toBeGreaterThanOrEqual(MIN_FPS)
})

test('a filter change redraws the map within 1 second', async ({ page }) => {
  await openWorkspace(page)

  await page.getByTestId('filter-country').locator('input').fill('NL')
  const started = Date.now()
  await page.getByTestId('filter-apply').click()
  // The map has its new points when the count says so; then two frames for the drawing.
  await expect(page.getByTestId('map-count')).toContainText('1,250 hosts on the map')
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  )
  const elapsed = Date.now() - started

  console.log(`${test.info().project.name}: filter change redrawn in ${elapsed} ms`)
  expect(elapsed).toBeLessThanOrEqual(REDRAW_MS)
})
