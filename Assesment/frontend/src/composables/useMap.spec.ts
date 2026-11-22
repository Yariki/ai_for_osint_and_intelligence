import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import type { Feature, Point } from 'geojson'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import WorkspaceMap from '../components/WorkspaceMap.vue'
import vuetify from '../plugins/vuetify'
import { useWorkspaceStore } from '../stores/workspace'
import { CONFIG, hostDetail, hostRow, mapPoint, scan } from '../test-fixtures'
import { FakeMap } from '../test-map'
import { type Call, stubApi } from '../test-setup'
import {
  accuracyCircle,
  addressNumber,
  bounds,
  canSplit,
  type ClusterHost,
  clusterHosts,
  MAX_ZOOM,
  MAXMIND_ATTRIBUTION,
  pathSegments,
  toFeatures,
  useMap,
} from './useMap'

vi.mock('maplibre-gl', async () => (await import('../test-map')).fakeMapLibre)

const TILES = {
  url_template: 'https://tiles.example.org/{z}/{x}/{y}.png',
  attribution: '© Example Maps contributors',
}
const HERE: [number, number] = [37.4056, -122.0775]

/** `count` hosts on one coordinate, as city-level location produces them. */
function colocated(count: number) {
  return Array.from({ length: count }, (_, index) =>
    mapPoint(`8.8.${Math.floor(index / 256)}.${index % 256}`, HERE[0], HERE[1], {
      open_port_count: index % 5,
    }),
  )
}

beforeEach(() => {
  FakeMap.instances = []
  setActivePinia(createPinia())
})

// --- The data the map is fed ---

test('hosts become GeoJSON points with the longitude first', () => {
  const collection = toFeatures([mapPoint('8.8.8.8', 37.4056, -122.0775, { open_port_count: 3 })])

  expect(collection.features).toEqual([
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [-122.0775, 37.4056] },
      properties: { address: '8.8.8.8', scan_id: 7, open_port_count: 3 },
    },
  ])
})

test('the hosts of a cluster are listed in address order', () => {
  const leaves = toFeatures([
    mapPoint('8.8.8.10', ...HERE),
    mapPoint('8.8.8.9', ...HERE),
    mapPoint('1.0.0.1', ...HERE),
  ]).features

  expect(clusterHosts(leaves).map((host) => host.address)).toEqual([
    '1.0.0.1',
    '8.8.8.9',
    '8.8.8.10',
  ])
  expect(addressNumber('1.0.0.1')).toBe(16777217)
})

test('a cluster can be split only while the map can zoom that far', () => {
  expect(canSplit(5)).toBe(true)
  expect(canSplit(MAX_ZOOM)).toBe(true)
  // Hosts on one coordinate would split only beyond the last zoom level: never.
  expect(canSplit(MAX_ZOOM + 1)).toBe(false)
})

test('the bounds hold every point', () => {
  expect(bounds([])).toBeNull()
  expect(
    bounds([mapPoint('a', 52.37, 4.89), mapPoint('b', -35.28, 149.13), mapPoint('c', 37.4, -122.1)]),
  ).toEqual([-122.1, -35.28, 149.13, 52.37])
})

function distanceKm(a: [number, number], b: [number, number]): number {
  const radians = (degrees: number) => (degrees * Math.PI) / 180
  const dLat = radians(b[0] - a[0])
  const dLon = radians(b[1] - a[1])
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(radians(a[0])) * Math.cos(radians(b[0])) * Math.sin(dLon / 2) ** 2
  return 2 * 6371 * Math.asin(Math.sqrt(h))
}

test.each([
  [52.3759, 4.8975, 20],
  [37.4056, -122.0775, 1000],
  [-35.2809, 149.13, 50],
  [69.65, 18.96, 200],
])('the accuracy circle of (%f, %f) lies %i km from its centre', (latitude, longitude, radius) => {
  const ring = accuracyCircle(latitude, longitude, radius).geometry.coordinates[0] ?? []

  expect(ring).toHaveLength(65)
  expect(ring[0]).toEqual(ring.at(-1)) // closed
  for (const [pointLongitude = 0, pointLatitude = 0] of ring) {
    const distance = distanceKm([latitude, longitude], [pointLatitude, pointLongitude])
    expect(Math.abs(distance - radius)).toBeLessThan(radius * 0.001)
  }
})

// --- The map object ---

async function mountedMap(points = colocated(3)) {
  const clicked: ClusterHost[] = []
  const clusters: ClusterHost[][] = []
  const map = useMap({
    onHost: (host) => clicked.push(host),
    onCluster: (hosts) => clusters.push(hosts),
  })
  map.setPoints(points) // before the style has loaded
  await map.mount(document.createElement('div'), TILES)
  const fake = FakeMap.latest()
  fake.fire('load')
  return { map, fake, clicked, clusters }
}

test('the map is created with the configured tile source and no other', async () => {
  const { fake } = await mountedMap()

  const style = fake.options.style as { sources: Record<string, Record<string, unknown>> }
  expect(style.sources).toEqual({
    tiles: {
      type: 'raster',
      tiles: ['https://tiles.example.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© Example Maps contributors',
    },
  })
  expect(fake.options.maxZoom).toBe(MAX_ZOOM)
})

test('hosts are a clustered source that keeps hosts on one coordinate together', async () => {
  const { fake } = await mountedMap()

  const hosts = fake.getSource('hosts')
  expect(hosts?.spec.cluster).toBe(true)
  // Clustering goes on beyond the last zoom level of the map.
  expect(hosts?.spec.clusterMaxZoom as number).toBeGreaterThan(MAX_ZOOM)
  // The points that were set before the style had loaded are there.
  expect((hosts?.data as { features: unknown[] }).features).toHaveLength(3)
  expect(fake.layers.map((layer) => layer.id)).toEqual([
    'accuracy',
    'path-solid',
    'path-dashed',
    'clusters',
    'host',
  ])
  expect(fake.fitted).toHaveLength(1)
})

test('new points replace the old ones, and the view is fitted only once', async () => {
  const { map, fake } = await mountedMap()

  map.setPoints(colocated(10))
  map.setPoints(colocated(11))

  expect((fake.getSource('hosts')?.data as { features: unknown[] }).features).toHaveLength(11)
  expect(fake.fitted).toHaveLength(1)
})

test('a cluster that can be split is zoomed into', async () => {
  const { fake, clusters } = await mountedMap()
  const source = fake.getSource('hosts')
  if (source) source.expansionZoom = 9
  const cluster: Feature<Point> = {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [4.9, 52.4] },
    properties: { cluster_id: 5, point_count: 40 },
  }

  fake.fire('click:clusters', [cluster])
  await flushPromises()

  expect(fake.eased).toEqual([{ center: [4.9, 52.4], zoom: 9 }])
  expect(clusters).toEqual([])
})

test('a cluster of hosts on one coordinate opens as the list of all of them', async () => {
  const points = colocated(300)
  const { fake, clusters } = await mountedMap(points)
  const source = fake.getSource('hosts')
  if (source) {
    source.expansionZoom = MAX_ZOOM + 5
    source.leaves = toFeatures(points).features
  }

  fake.fire('click:clusters', [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [HERE[1], HERE[0]] },
      properties: { cluster_id: 1, point_count: 300 },
    },
  ])
  await flushPromises()

  expect(fake.eased).toEqual([])
  expect(clusters).toHaveLength(1)
  expect(clusters[0]).toHaveLength(300)
  expect(new Set(clusters[0]?.map((host) => host.address)).size).toBe(300)
})

test('a click on a single host selects it', async () => {
  const { fake, clicked } = await mountedMap()

  fake.fire('click:host', toFeatures([mapPoint('8.8.8.8', ...HERE, { scan_id: 9 })]).features)

  expect(clicked).toEqual([{ address: '8.8.8.8', scan_id: 9, open_port_count: 1 }])
})

test('the accuracy radius of a location is drawn, and removed again', async () => {
  const { map, fake } = await mountedMap()
  const accuracy = fake.getSource('accuracy')

  map.showAccuracy({ latitude: 52.3759, longitude: 4.8975, radiusKm: 20 })
  expect((accuracy?.data as Feature).geometry.type).toBe('Polygon')

  map.showAccuracy(null)
  expect(accuracy?.data).toEqual({ type: 'FeatureCollection', features: [] })

  // A location without a known radius has nothing to draw.
  map.showAccuracy({ latitude: 52.3759, longitude: 4.8975, radiusKm: null })
  expect(accuracy?.data).toEqual({ type: 'FeatureCollection', features: [] })
})

// --- The map component in the workspace ---

let wrapper: VueWrapper | undefined

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  document.body.innerHTML = ''
})

async function workspaceMap(tiles: typeof TILES | null, located = colocated(300)) {
  const unlocated = [
    { address: '200.3.13.2', scan_id: 7, open_port_count: 2 },
    { address: '200.3.13.7', scan_id: 7, open_port_count: 2 },
  ]
  const rows = [...located.slice(0, 48), hostRow('200.3.13.2', 2), hostRow('200.3.13.7', 2)]
  const calls: Call[] = stubApi({
    'GET /config': { body: { ...CONFIG, tiles } },
    'GET /scans/7': { body: scan(7, { state: 'completed' }) },
    'GET /scans/7/hosts': {
      body: { items: rows, total: located.length + unlocated.length, limit: 50, offset: 0 },
    },
    'GET /scans/7/map-points': { body: { located, unlocated } },
    'GET /scans/7/hosts/8.8.1.43': { body: hostDetail('8.8.1.43') },
  })
  const workspace = useWorkspaceStore()
  await workspace.open(7)
  wrapper = mount(WorkspaceMap, { global: { plugins: [vuetify] }, attachTo: document.body })
  await flushPromises()
  return { calls, workspace, component: wrapper }
}

test('without a tile source the notice is shown and no tile request is made', async () => {
  const { calls, component } = await workspaceMap(null)

  expect(component.find('[data-test="map-notice"]').text()).toContain('No map tile source')
  expect(component.find('[data-test="map"]').exists()).toBe(false)
  // No map object exists, so nothing can ask any server for a tile.
  expect(FakeMap.instances).toEqual([])
  expect(calls.every((call) => call.key.startsWith('GET /scans/7') || call.key === 'GET /config')).toBe(true)
  // The location data is still credited: the table shows it.
  expect(component.find('[data-test="maxmind-attribution"]').text()).toBe(MAXMIND_ATTRIBUTION)
})

test('with a tile source both attributions are visible, and locations are called approximate', async () => {
  const { component } = await workspaceMap(TILES)

  expect(component.find('[data-test="tile-attribution"]').text()).toContain(
    '© Example Maps contributors',
  )
  expect(component.find('[data-test="maxmind-attribution"]').text()).toContain('MaxMind')
  expect(component.find('[data-test="map-count"]').text()).toContain('approximate')
  expect(FakeMap.instances).toHaveLength(1)
  const style = FakeMap.latest().options.style as { sources: { tiles: { attribution: string } } }
  expect(style.sources.tiles.attribution).toBe('© Example Maps contributors')
})

test('with 300 hosts on one coordinate each can be selected from the cluster list', async () => {
  const located = colocated(300)
  const { workspace, component, calls } = await workspaceMap(TILES, located)
  const fake = FakeMap.latest()
  fake.fire('load')
  const source = fake.getSource('hosts')
  if (source) {
    source.expansionZoom = MAX_ZOOM + 5
    source.leaves = toFeatures(located).features
  }

  fake.fire('click:clusters', [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [HERE[1], HERE[0]] },
      properties: { cluster_id: 1, point_count: 300 },
    },
  ])
  await flushPromises()

  // The list is in a dialog, which Vuetify renders at the end of the document.
  const list = document.querySelector('[data-test="cluster-list"]')
  expect(list?.textContent).toContain('300 hosts at this location')
  // Every one of the 300 can be reached: page by page...
  const seen = new Set<string>()
  const pages = Math.ceil(300 / 50)
  for (let page = 1; page <= pages; page++) {
    for (const item of document.querySelectorAll('[data-test="host-list-item"]')) {
      seen.add(item.querySelector('.v-list-item-title')?.textContent ?? '')
    }
    const next = document.querySelector<HTMLElement>('.v-pagination__next button')
    if (page < pages) {
      next?.click()
      await flushPromises()
    }
  }
  expect(seen).toEqual(new Set(located.map((point) => point.address)))

  // ...or by a part of its address. Selecting it opens its detail and closes the list.
  const search = document.querySelector<HTMLInputElement>('[data-test="host-list-search"] input')
  if (search) {
    search.value = '8.8.1.43'
    search.dispatchEvent(new Event('input'))
  }
  await flushPromises()
  const found = document.querySelectorAll<HTMLElement>('[data-test="host-list-item"]')
  expect(found).toHaveLength(1)
  found[0]?.click()
  await flushPromises()

  expect(calls.at(-1)?.key).toBe('GET /scans/7/hosts/8.8.1.43')
  expect(workspace.selected?.address).toBe('8.8.1.43')
  expect(component.findComponent({ name: 'ClusterHostList' }).props('hosts')).toEqual([])
}, 20_000) // 300 hosts are clicked through a dialog page by page

test('hosts on the map plus unlocated hosts equal the rows of the table', async () => {
  const { workspace } = await workspaceMap(TILES)

  const onMap = (FakeMap.latest().options.style as object) && workspace.located.length
  expect(onMap + workspace.unlocated.length).toBe(workspace.total)
  expect(workspace.unlocated.map((host) => host.address)).toEqual(['200.3.13.2', '200.3.13.7'])
})

test('selecting a host draws its accuracy radius on the map', async () => {
  const { workspace } = await workspaceMap(TILES)
  const fake = FakeMap.latest()
  fake.fire('load')

  await workspace.select('8.8.1.43')
  await flushPromises()
  expect((fake.getSource('accuracy')?.data as Feature).geometry.type).toBe('Polygon')

  await workspace.select(null)
  await flushPromises()
  expect(fake.getSource('accuracy')?.data).toEqual({ type: 'FeatureCollection', features: [] })
})

test('points that arrive live are drawn without a reload', async () => {
  const { workspace } = await workspaceMap(TILES, colocated(2))
  const fake = FakeMap.latest()
  fake.fire('load')

  workspace.applyEvent({
    type: 'hosts_changed',
    scan_id: 7,
    project_id: 1,
    hosts: [
      { address: '9.9.9.9', reachable: true, open_port_count: 1, latitude: 47.37, longitude: 8.55 },
    ],
  })
  await flushPromises()

  const features = (fake.getSource('hosts')?.data as { features: Feature[] }).features
  expect(features.map((feature) => feature.properties?.address)).toContain('9.9.9.9')
})

test('the map is removed with its component', async () => {
  const { component } = await workspaceMap(TILES)
  const fake = FakeMap.latest()

  component.unmount()
  wrapper = undefined

  expect(fake.removed).toBe(true)
})


// --- The path of a traceroute (FR-TRC-6, check 3.10 in part) ---

const AMSTERDAM: [number, number] = [4.8975, 52.3759]
const FRANKFURT: [number, number] = [8.6821, 50.1109]
const LONDON: [number, number] = [-0.1276, 51.5072]

test('consecutive located hops are joined by solid segments', () => {
  const segments = pathSegments([
    { ttl: 1, point: AMSTERDAM },
    { ttl: 2, point: FRANKFURT },
    { ttl: 3, point: LONDON },
  ])

  expect(segments.map((s) => [s.properties.from_ttl, s.properties.to_ttl, s.properties.dashed])).toEqual([
    [1, 2, false],
    [2, 3, false],
  ])
  expect(segments[0]?.geometry.coordinates).toEqual([AMSTERDAM, FRANKFURT])
})

test('the segment across a silent or unlocated hop is dashed', () => {
  const segments = pathSegments([
    { ttl: 1, point: null }, // a private hop before the first located one: no segment
    { ttl: 2, point: AMSTERDAM },
    { ttl: 3, point: null }, // silent
    { ttl: 4, point: null }, // unlocated
    { ttl: 5, point: FRANKFURT },
    { ttl: 6, point: LONDON },
  ])

  expect(segments.map((s) => [s.properties.from_ttl, s.properties.to_ttl, s.properties.dashed])).toEqual([
    [2, 5, true],
    [5, 6, false],
  ])
})

test('a TTL that has not arrived yet also makes the segment dashed, and order is by TTL', () => {
  const segments = pathSegments([
    { ttl: 4, point: LONDON },
    { ttl: 1, point: AMSTERDAM },
    { ttl: 2, point: FRANKFURT },
  ])

  expect(segments.map((s) => [s.properties.from_ttl, s.properties.to_ttl, s.properties.dashed])).toEqual([
    [1, 2, false],
    [2, 4, true],
  ])
})

test('a path with fewer than two located hops has no segment', () => {
  expect(pathSegments([])).toEqual([])
  expect(pathSegments([{ ttl: 1, point: AMSTERDAM }, { ttl: 2, point: null }])).toEqual([])
})

test('the path is drawn on the map and removed again', async () => {
  const { map, fake } = await mountedMap()

  map.showPath([
    { ttl: 1, point: AMSTERDAM },
    { ttl: 2, point: null },
    { ttl: 3, point: FRANKFURT },
  ])
  const drawn = fake.getSource('path')?.data as { features: Array<{ properties: { dashed: boolean } }> }
  expect(drawn.features.map((f) => f.properties.dashed)).toEqual([true])
  // The two layers tell solid from dashed by the property.
  const dashed = fake.layers.find((layer) => layer.id === 'path-dashed') as { paint: Record<string, unknown> }
  expect(dashed.paint['line-dasharray']).toEqual([2, 2])

  map.showPath([])
  expect(fake.getSource('path')?.data).toEqual({ type: 'FeatureCollection', features: [] })
})
