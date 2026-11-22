import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { createMemoryHistory, type Router } from 'vue-router'

import App from '../App.vue'
import vuetify from '../plugins/vuetify'
import { createAppRouter } from '../router'
import { useWorkspaceStore } from '../stores/workspace'
import { CONFIG, FakeEventSource, hostRow, scan } from '../test-fixtures'
import { FakeMap } from '../test-map'
import { type Call, stubApi } from '../test-setup'

vi.mock('maplibre-gl', async () => (await import('../test-map')).fakeMapLibre)

const SCANS = [scan(9, { state: 'running' }), scan(7, { state: 'completed' }), scan(3)]

let wrapper: VueWrapper | undefined
let router: Router

beforeEach(() => {
  FakeEventSource.instances = []
  FakeMap.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  document.body.innerHTML = ''
})

function hosts(scanId: number) {
  return {
    body: { items: [hostRow('8.8.8.1', 2, { scan_id: scanId })], total: 1, limit: 50, offset: 0 },
  }
}
const NO_POINTS = { body: { located: [], unlocated: [] } }

async function open(path: string, scans = SCANS): Promise<Call[]> {
  const calls = stubApi({
    'GET /setup': { body: { setup_required: false } },
    'GET /session': { body: { username: 'analyst', expires_at: '2026-11-17T12:00:00Z' } },
    'GET /config': { body: CONFIG },
    'GET /projects/1/scans': { body: { items: scans, total: scans.length, limit: 200, offset: 0 } },
    'GET /projects/1/hosts': hosts(0),
    'GET /projects/1/map-points': NO_POINTS,
    ...Object.fromEntries(
      [9, 7, 3].flatMap((id) => [
        [`GET /scans/${id}`, { body: scans.find((item) => item.id === id) }],
        [`GET /scans/${id}/hosts`, hosts(id)],
        [`GET /scans/${id}/map-points`, NO_POINTS],
      ]),
    ),
  })
  router = createAppRouter(createMemoryHistory())
  wrapper = mount(App, { global: { plugins: [createPinia(), router, vuetify] } })
  await router.push(path)
  await flushPromises()
  return calls
}

/** Let a navigation finish: the router completes it after the current task. */
async function settle(): Promise<void> {
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await flushPromises()
}

function fetched(calls: Call[], path: string): Call[] {
  return calls.filter((call) => call.key === `GET ${path}`)
}

test('the workspace opens on the latest scan of the project', async () => {
  const calls = await open('/projects/1/workspace')

  expect(useWorkspaceStore().scan?.id).toBe(9)
  expect(fetched(calls, '/scans/9/hosts')).toHaveLength(1)
  expect(fetched(calls, '/scans/7/hosts')).toHaveLength(0)
  expect(wrapper?.find('[data-test="scan-state"]').text()).toContain('running')
})

test('a scan named in the URL is opened instead, and a reload shows it again', async () => {
  const calls = await open('/projects/1/workspace?scan=7&country=NL')

  expect(useWorkspaceStore().scan?.id).toBe(7)
  // The filters of the URL are applied from the first request on.
  expect(fetched(calls, '/scans/7/hosts')[0]?.query).toMatchObject({ country: 'NL' })
  expect(fetched(calls, '/scans/7/map-points')[0]?.query).toMatchObject({ country: 'NL' })
  expect(fetched(calls, '/scans/9/hosts')).toHaveLength(0)
})

test('the whole project can be shown, from the URL', async () => {
  const calls = await open('/projects/1/workspace?view=project')

  const workspace = useWorkspaceStore()
  expect(workspace.projectView).toBe(true)
  expect(workspace.scan).toBeNull()
  expect(fetched(calls, '/projects/1/hosts')).toHaveLength(1)
  expect(wrapper?.find('[data-test="project-view-note"]').exists()).toBe(true)
})

test('a link to a scan opens the workspace of its project on that scan', async () => {
  const calls = await open('/scans/7')

  const workspace = useWorkspaceStore()
  expect(workspace.scan?.id).toBe(7)
  expect(workspace.projectId).toBe(1)
  // The selector has the scans of the project.
  expect(workspace.scans.map((item) => item.id)).toEqual([9, 7, 3])
  expect(fetched(calls, '/projects/1/scans')).toHaveLength(1)
})

test('applying filters puts them in the URL and loads table and map with them', async () => {
  const calls = await open('/projects/1/workspace?scan=7')
  const before = calls.length

  await wrapper?.find('[data-test="filter-open_port"] input').setValue('443')
  await wrapper?.find('[data-test="filter-organization"] input').setValue('Example')
  await wrapper?.find('[data-test="filter-bar"]').trigger('submit')
  await flushPromises()
  await settle()

  expect(router.currentRoute.value.query).toEqual({
    scan: '7',
    open_port: '443',
    organization: 'Example',
  })
  const after = calls.slice(before)
  expect(after.map((call) => call.key).sort()).toEqual([
    'GET /scans/7/hosts',
    'GET /scans/7/map-points',
  ])
  for (const call of after) {
    expect(call.query).toMatchObject({ open_port: '443', organization: 'Example' })
  }

  // Clearing them takes them out of the URL again.
  await wrapper?.find('[data-test="filter-clear"]').trigger('click')
  await settle()
  expect(router.currentRoute.value.query).toEqual({ scan: '7' })
})

test('a scan without ICMP discovery warns that hosts answering only ping were missed', async () => {
  await open('/projects/1/workspace?scan=7', [
    scan(9, { state: 'running' }),
    scan(7, { state: 'completed', icmp_discovery_unavailable: true }),
  ])

  const warning = wrapper?.find('[data-test="icmp-unavailable"]')
  expect(warning?.exists()).toBe(true)
  expect(warning?.text()).toContain('answers only ping')

  // A scan with ICMP discovery has no such warning.
  await router.push('/projects/1/workspace?scan=9')
  await settle()
  expect(useWorkspaceStore().scan?.id).toBe(9)
  expect(wrapper?.find('[data-test="icmp-unavailable"]').exists()).toBe(false)
})

test('a project without a scan says so', async () => {
  await open('/projects/1/workspace', [])

  expect(wrapper?.find('[data-test="workspace-empty"]').exists()).toBe(true)
  expect(wrapper?.find('[data-test="filter-bar"]').exists()).toBe(false)
})

test('without a tile source the workspace shows the notice and creates no map', async () => {
  await open('/projects/1/workspace')

  expect(wrapper?.find('[data-test="map-notice"]').exists()).toBe(true)
  expect(FakeMap.instances).toEqual([])
  expect(wrapper?.find('[data-test="host-row"]').exists()).toBe(true)
})
