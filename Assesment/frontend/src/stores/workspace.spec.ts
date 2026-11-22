import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { useEventStream } from '../composables/useEventStream'
import { FakeEventSource, hostDetail, hostRow as host, mapPoint, scan } from '../test-fixtures'
import { type Call, type Reply, stubApi } from '../test-setup'
import { type HostRow, type MapPoint, type UnlocatedHost, useWorkspaceStore } from './workspace'

function page(items: HostRow[], total = items.length): Reply {
  return { body: { items, total, limit: 50, offset: 0 } }
}

type Changed = [address: string, openPorts: number, latitude?: number, longitude?: number]

function hostsChanged(scanId: number, hosts: Changed[], projectId = 1) {
  return {
    type: 'hosts_changed',
    scan_id: scanId,
    project_id: projectId,
    hosts: hosts.map(([address, count, latitude, longitude]) => ({
      address,
      reachable: true,
      open_port_count: count,
      latitude: latitude ?? null,
      longitude: longitude ?? null,
    })),
  }
}

const RUNNING = scan(7, { state: 'running', phase: 'port_scan', chunks_done: 1, chunks_total: 4 })

/** A server whose answers the test can change, as a scan goes on. */
function server(hosts: HostRow[], current = RUNNING) {
  const state = {
    hosts,
    scan: current,
    located: [] as MapPoint[],
    unlocated: [] as UnlocatedHost[],
  }
  const points = () => ({ body: { located: state.located, unlocated: state.unlocated } })
  const calls: Call[] = stubApi({
    'GET /scans/7': () => ({ body: state.scan }),
    'GET /scans/7/hosts': () => page(state.hosts),
    'GET /scans/7/map-points': points,
    'GET /projects/1/scans': () => ({
      body: { items: [scan(9), state.scan, scan(3)], total: 3, limit: 200, offset: 0 },
    }),
    'GET /projects/1/hosts': () => page(state.hosts),
    'GET /projects/1/map-points': points,
  })
  return { state, calls }
}

function keys(calls: Call[], from = 0): string[] {
  return calls
    .slice(from)
    .map((call) => call.key)
    .sort()
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

// --- Loading ---

test('open loads the scan, the first page of its discovered hosts and its map points', async () => {
  const { state, calls } = server([host('8.8.8.1', 2), host('8.8.8.2', 1)])
  state.located = [mapPoint('8.8.8.1', 37.4, -122.1)]
  state.unlocated = [{ address: '8.8.8.2', scan_id: 7, open_port_count: 1 }]
  const workspace = useWorkspaceStore()

  await workspace.open(7)

  expect(workspace.scan?.id).toBe(7)
  expect(workspace.projectId).toBe(1)
  expect(workspace.hosts.map((row) => row.address)).toEqual(['8.8.8.1', '8.8.8.2'])
  expect(workspace.total).toBe(2)
  expect(workspace.located.map((point) => point.address)).toEqual(['8.8.8.1'])
  expect(workspace.unlocated.map((point) => point.address)).toEqual(['8.8.8.2'])
  const table = calls.find((call) => call.key === 'GET /scans/7/hosts')
  expect(table?.query).toEqual({
    reachable: 'true',
    sort: 'address',
    order: 'asc',
    limit: '50',
    offset: '0',
  })
  // The map asks for the same hosts as the table: all of them, not a page.
  const map = calls.find((call) => call.key === 'GET /scans/7/map-points')
  expect(map?.query).toEqual({ reachable: 'true' })
})

test('the scans of the project are loaded for the selector, and the first is the latest', async () => {
  server([])
  const workspace = useWorkspaceStore()

  const latest = await workspace.loadScans(1)

  expect(latest?.id).toBe(9)
  expect(workspace.scans.map((item) => item.id)).toEqual([9, 7, 3])
})

test('sorting and paging are asked from the server', async () => {
  const { calls } = server([host('8.8.8.1')])
  const workspace = useWorkspaceStore()
  await workspace.open(7)

  await workspace.setSort('organization', 'desc')
  await workspace.setPage(3)

  expect(calls.at(-2)?.query).toMatchObject({ sort: 'organization', order: 'desc', offset: '0' })
  expect(calls.at(-1)?.query).toMatchObject({ sort: 'organization', order: 'desc', offset: '100' })
})

// --- Filters: applied by the server, to the table and the map together ---

test('filters are sent with both requests and reset the page', async () => {
  const { calls } = server([host('8.8.8.1')])
  const workspace = useWorkspaceStore()
  await workspace.open(7)
  await workspace.setPage(4)
  const before = calls.length

  await workspace.setFilters({ open_port: 443, country: 'NL', organization: 'example' })

  expect(workspace.filtersActive).toBe(true)
  expect(workspace.page).toBe(1)
  expect(keys(calls, before)).toEqual(['GET /scans/7/hosts', 'GET /scans/7/map-points'])
  const [table, map] = calls.slice(before).sort((a, b) => a.key.localeCompare(b.key))
  const filters = { reachable: 'true', open_port: '443', country: 'NL', organization: 'example' }
  expect(table?.query).toEqual({ ...filters, sort: 'address', order: 'asc', limit: '50', offset: '0' })
  expect(map?.query).toEqual(filters)
})

test('setting the same filters again asks nothing', async () => {
  const { calls } = server([host('8.8.8.1')])
  const workspace = useWorkspaceStore()
  await workspace.open(7)
  await workspace.setFilters({ asn: 64496 })
  const before = calls.length

  await workspace.setFilters({ asn: 64496 })

  expect(calls).toHaveLength(before)
})

test('with a filter active a hosts-changed event causes one debounced refetch and no local filtering', async () => {
  const { state, calls } = server([host('8.8.8.1', 1, { country: 'NL' })])
  const workspace = useWorkspaceStore()
  await workspace.open(7)
  await workspace.setFilters({ country: 'NL' })
  const before = calls.length

  // Three events in a burst. One is for the row on screen, two for hosts the
  // client knows nothing about: whether they pass the filter is the server's to say.
  workspace.applyEvent(hostsChanged(7, [['8.8.8.1', 5]]))
  workspace.applyEvent(hostsChanged(7, [['8.8.8.2', 2, 52.37, 4.89]]))
  workspace.applyEvent(hostsChanged(7, [['8.8.8.3', 1, 37.4, -122.1]]))

  // Nothing was applied locally: not to the row, not to the map.
  expect(workspace.hosts.map((row) => [row.address, row.open_port_count])).toEqual([['8.8.8.1', 1]])
  expect(workspace.located).toEqual([])
  expect(calls).toHaveLength(before)

  // The server's answer: one of the new hosts passes the filter.
  state.hosts = [host('8.8.8.1', 5, { country: 'NL' }), host('8.8.8.2', 2, { country: 'NL' })]
  state.located = [mapPoint('8.8.8.2', 52.37, 4.89)]
  await vi.advanceTimersByTimeAsync(499)
  expect(calls).toHaveLength(before)
  await vi.advanceTimersByTimeAsync(2)
  await flushPromises()

  // One refetch of everything on screen, with the filter.
  expect(keys(calls, before)).toEqual([
    'GET /scans/7',
    'GET /scans/7/hosts',
    'GET /scans/7/map-points',
  ])
  expect(calls.at(-1)?.query).toMatchObject({ country: 'NL' })
  expect(workspace.hosts.map((row) => row.address)).toEqual(['8.8.8.1', '8.8.8.2'])
  expect(workspace.located.map((point) => point.address)).toEqual(['8.8.8.2'])
  await vi.advanceTimersByTimeAsync(2000)
  expect(calls).toHaveLength(before + 3)
})

// --- Live updates without a filter ---

test('a hosts-changed event updates the matching rows', async () => {
  const { calls } = server([host('8.8.8.1', 1), host('8.8.8.2', 0), host('8.8.8.3', 0)])
  const workspace = useWorkspaceStore()
  await workspace.open(7)
  const requests = calls.length

  workspace.applyEvent(hostsChanged(7, [['8.8.8.1', 3], ['8.8.8.3', 1]]))

  expect(workspace.hosts.map((row) => [row.address, row.open_port_count])).toEqual([
    ['8.8.8.1', 3],
    ['8.8.8.2', 0],
    ['8.8.8.3', 1],
  ])
  // The rows on screen were updated from the event alone: nothing was fetched.
  await vi.advanceTimersByTimeAsync(2000)
  expect(calls).toHaveLength(requests)
})

test('a hosts-changed event puts new hosts on the map at once', async () => {
  const { state } = server([host('8.8.8.1', 1)])
  state.unlocated = [{ address: '8.8.8.1', scan_id: 7, open_port_count: 1 }]
  const workspace = useWorkspaceStore()
  await workspace.open(7)

  workspace.applyEvent(
    hostsChanged(7, [
      ['8.8.8.5', 2, 37.4056, -122.0775], // a new host with a location
      ['8.8.8.6', 0], // a new host without one
      ['8.8.8.1', 4, 52.3759, 4.8975], // a known host that now has a location
    ]),
  )

  expect(workspace.located).toEqual([
    {
      address: '8.8.8.5',
      scan_id: 7,
      open_port_count: 2,
      latitude: 37.4056,
      longitude: -122.0775,
      accuracy_radius_km: null,
    },
    {
      address: '8.8.8.1',
      scan_id: 7,
      open_port_count: 4,
      latitude: 52.3759,
      longitude: 4.8975,
      accuracy_radius_km: null,
    },
  ])
  // Every host is in exactly one of the two lists.
  expect(workspace.unlocated).toEqual([{ address: '8.8.8.6', scan_id: 7, open_port_count: 0 }])

  // A later event for a located host keeps it on the map and updates its count.
  workspace.applyEvent(hostsChanged(7, [['8.8.8.5', 3]]))
  expect(workspace.located[0]).toMatchObject({ address: '8.8.8.5', open_port_count: 3 })
  expect(workspace.unlocated).toHaveLength(1)
})

test('an event for another scan changes nothing', async () => {
  server([host('8.8.8.1', 1)])
  const workspace = useWorkspaceStore()
  await workspace.open(7)

  workspace.applyEvent(hostsChanged(8, [['8.8.8.1', 9, 1, 1]]))
  workspace.applyEvent({ type: 'scan_progress', scan_id: 8, project_id: 1, state: 'failed' })

  expect(workspace.hosts[0]?.open_port_count).toBe(1)
  expect(workspace.located).toEqual([])
  expect(workspace.scan?.state).toBe('running')
})

test('a newly discovered host makes the table load again, once', async () => {
  const { state, calls } = server([host('8.8.8.1', 1)])
  const workspace = useWorkspaceStore()
  await workspace.open(7)
  const requests = calls.length

  // Two events in quick succession, each with a host that is not on screen.
  state.hosts = [host('8.8.8.1', 1), host('8.8.8.5', 2), host('8.8.8.9', 0)]
  workspace.applyEvent(hostsChanged(7, [['8.8.8.5', 2]]))
  workspace.applyEvent(hostsChanged(7, [['8.8.8.9', 0]]))
  await vi.advanceTimersByTimeAsync(600)
  await flushPromises()

  expect(workspace.hosts.map((row) => row.address)).toEqual(['8.8.8.1', '8.8.8.5', '8.8.8.9'])
  expect(keys(calls, requests)).toEqual([
    'GET /scans/7',
    'GET /scans/7/hosts',
    'GET /scans/7/map-points',
  ])
})

test('a progress event updates the scan, here and in the list of scans', async () => {
  server([])
  const workspace = useWorkspaceStore()
  await workspace.loadScans(1)
  await workspace.open(7)

  workspace.applyEvent({
    type: 'scan_progress',
    scan_id: 7,
    project_id: 1,
    state: 'running',
    phase: 'enrichment',
    chunks_done: 4,
    chunks_total: 4,
    probes_sent: 29952,
  })

  const progress = { phase: 'enrichment', chunks_done: 4, chunks_total: 4, probes_sent: 29952 }
  expect(workspace.scan).toMatchObject(progress)
  expect(workspace.scans.find((item) => item.id === 7)).toMatchObject(progress)
})

test('when the scan ends, the final counts are loaded', async () => {
  const { state } = server([host('8.8.8.1', 2)])
  const workspace = useWorkspaceStore()
  await workspace.open(7)

  state.scan = scan(7, { state: 'completed', chunks_done: 4, chunks_total: 4 })
  state.hosts = [host('8.8.8.1', 2, { closed_count: 998 })]
  workspace.applyEvent({
    type: 'scan_progress',
    scan_id: 7,
    project_id: 1,
    state: 'completed',
    phase: null,
    chunks_done: 4,
    chunks_total: 4,
    probes_sent: 256000,
  })
  await vi.advanceTimersByTimeAsync(600)
  await flushPromises()

  expect(workspace.scan?.state).toBe('completed')
  expect(workspace.hosts[0]?.closed_count).toBe(998)
})

// --- The project-wide view ---

test('the project-wide view loads the hosts and the points of the project', async () => {
  const { state, calls } = server([host('8.8.8.1', 2, { scan_id: 9 }), host('8.8.8.2', 1)])
  state.located = [mapPoint('8.8.8.1', 37.4, -122.1, { scan_id: 9 })]
  const workspace = useWorkspaceStore()
  await workspace.setFilters({ ptr: 'example' })

  await workspace.openProjectView(1)

  expect(workspace.projectView).toBe(true)
  expect(workspace.scan).toBeNull()
  expect(workspace.hosts.map((row) => [row.address, row.scan_id])).toEqual([
    ['8.8.8.1', 9],
    ['8.8.8.2', 7],
  ])
  expect(keys(calls)).toEqual(['GET /projects/1/hosts', 'GET /projects/1/map-points'])
  expect(calls[0]?.query).toMatchObject({ reachable: 'true', ptr: 'example' })
})

test('in the project-wide view an event of the project causes one debounced refetch', async () => {
  const { calls } = server([host('8.8.8.1', 1)])
  const workspace = useWorkspaceStore()
  await workspace.openProjectView(1)
  const before = calls.length

  // Any scan of the project can change the latest state of an address.
  workspace.applyEvent(hostsChanged(9, [['8.8.8.1', 4]]))
  workspace.applyEvent(hostsChanged(12, [['8.8.8.7', 1]]))
  // An event of another project does not.
  workspace.applyEvent(hostsChanged(40, [['9.9.9.9', 1]], 2))
  expect(workspace.hosts[0]?.open_port_count).toBe(1)
  await vi.advanceTimersByTimeAsync(600)
  await flushPromises()

  expect(keys(calls, before)).toEqual(['GET /projects/1/hosts', 'GET /projects/1/map-points'])
})

test('a host of the project-wide view is opened in the scan its row comes from', async () => {
  const detail = hostDetail('8.8.8.1', { scan_id: 9 })
  const calls = stubApi({
    'GET /projects/1/hosts': page([host('8.8.8.1', 2, { scan_id: 9 })]),
    'GET /projects/1/map-points': { body: { located: [], unlocated: [] } },
    'GET /scans/9/hosts/8.8.8.1': { body: detail },
  })
  const workspace = useWorkspaceStore()
  await workspace.openProjectView(1)

  await workspace.select('8.8.8.1', 9)

  expect(workspace.selected).toEqual(detail)
  expect(calls.at(-1)?.key).toBe('GET /scans/9/hosts/8.8.8.1')
})

// --- Host detail ---

test('selecting a host loads its detail, and an event for it loads it again', async () => {
  const detail = hostDetail('8.8.8.1')
  const calls = stubApi({
    'GET /scans/7': { body: RUNNING },
    'GET /scans/7/hosts': page([host('8.8.8.1', 1)]),
    'GET /scans/7/map-points': { body: { located: [], unlocated: [] } },
    'GET /scans/7/hosts/8.8.8.1': { body: detail },
  })
  const workspace = useWorkspaceStore()
  await workspace.open(7)

  await workspace.select('8.8.8.1')
  expect(workspace.selected).toEqual(detail)

  workspace.applyEvent(hostsChanged(7, [['8.8.8.1', 2]]))
  await flushPromises()
  expect(calls.filter((call) => call.key === 'GET /scans/7/hosts/8.8.8.1')).toHaveLength(2)

  await workspace.select(null)
  expect(workspace.selected).toBeNull()
})

// --- Check 1B.18, second sentence: after a dropped connection the view reloads ---

test('after a dropped connection the view reloads to the correct state', async () => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
  const { state } = server([host('8.8.8.1', 1)])
  const workspace = useWorkspaceStore()
  const stream = useEventStream()
  // What the workspace view does when it is shown.
  const stopEvents = stream.on(workspace.applyEvent)
  const stopReconnect = stream.onReconnect(() => void workspace.reload())
  await workspace.open(7)
  stream.connect()
  FakeEventSource.latest().open()

  // The connection drops. The scan goes on, and its events are never seen:
  // the server does not replay them.
  FakeEventSource.latest().drop()
  state.scan = scan(7, { state: 'completed', chunks_done: 4, chunks_total: 4, open_ports: 5 })
  state.hosts = [host('8.8.8.1', 3), host('8.8.8.2', 2)]
  state.located = [mapPoint('8.8.8.2', 52.37, 4.89)]
  expect(workspace.scan?.state).toBe('running')
  expect(workspace.hosts).toHaveLength(1)

  // The connection is back.
  FakeEventSource.latest().open()
  await flushPromises()

  expect(workspace.scan).toMatchObject({ state: 'completed', chunks_done: 4, open_ports: 5 })
  expect(workspace.hosts.map((row) => [row.address, row.open_port_count])).toEqual([
    ['8.8.8.1', 3],
    ['8.8.8.2', 2],
  ])
  expect(workspace.located.map((point) => point.address)).toEqual(['8.8.8.2'])

  stopEvents()
  stopReconnect()
  stream.close()
})
