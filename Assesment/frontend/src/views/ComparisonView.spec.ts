import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, expect, test } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import vuetify from '../plugins/vuetify'
import { createAppRouter } from '../router'
import { scan } from '../test-fixtures'
import { stubApi } from '../test-setup'
import AlertsView from './AlertsView.vue'
import ComparisonView from './ComparisonView.vue'

const DIFF = {
  id: 41,
  stored: true,
  base_scan_id: 8,
  target_scan_id: 9,
  compared_ports: [
    [22, 22],
    [80, 81],
  ],
  not_comparable: ['8.8.8.200'],
  summary: { host_appeared: 1, port_opened: 1, enrichment_changed: 1, compared_addresses: 4 },
  changes: [
    { address: '8.8.8.3', kind: 'host_appeared', port: null, field: null, before: null, after: null, dataset_change: false },
    { address: '8.8.8.1', kind: 'port_opened', port: 80, field: null, before: null, after: null, dataset_change: false },
    { address: '8.8.8.2', kind: 'enrichment_changed', port: null, field: 'city', before: 'Delft', after: 'Leiden', dataset_change: true },
  ],
  created_at: '2026-11-17T02:05:00Z',
}

const SESSION = {
  'GET /setup': { body: { setup_required: false } },
  'GET /session': { body: { username: 'analyst', expires_at: '2026-11-18T12:00:00Z' } },
}

let wrapper: VueWrapper | undefined

afterEach(() => {
  wrapper?.unmount()
  document.body.innerHTML = ''
})

async function mountAt(component: object, props: Record<string, unknown>, path: string) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  wrapper = mount(component, { props, global: { plugins: [pinia, router, vuetify] } })
  await flushPromises()
  return router
}

test('a stored diff shows the changes by kind, the compared ports and the dataset flag', async () => {
  stubApi({ ...SESSION, 'GET /diffs/41': { body: DIFF } })
  await mountAt(ComparisonView, { diffId: 41 }, '/diffs/41')

  expect(wrapper!.find('[data-test="comparison-ports"]').text()).toContain('22, 80-81')
  expect(wrapper!.find('[data-test="comparison-not-comparable"]').text()).toContain('8.8.8.200')
  expect(wrapper!.find('[data-test="count-port_opened"]').text()).toContain('1')
  expect(wrapper!.find('[data-test="kind-port_opened"]').text()).toContain('port 80')
  const enrichment = wrapper!.find('[data-test="kind-enrichment_changed"]')
  expect(enrichment.text()).toContain('city: Delft → Leiden')
  expect(enrichment.find('[data-test="dataset-change"]').exists()).toBe(true)
  expect(wrapper!.find('[data-test="kind-host_appeared"] [data-test="dataset-change"]').exists()).toBe(false)
})

test('two scans of a project are compared on request', async () => {
  const calls = stubApi({
    ...SESSION,
    'GET /projects/1/scans': { body: { items: [scan(9), scan(8)], total: 2, limit: 200, offset: 0 } },
    'GET /projects/1/compare': { body: { ...DIFF, id: null, stored: false, created_at: null } },
  })
  await mountAt(ComparisonView, { projectId: 1 }, '/projects/1/compare?base=8&target=9')

  const compare = calls.find((call) => call.key === 'GET /projects/1/compare')
  expect(compare?.query).toEqual({ base: '8', target: '9' })
  expect(wrapper!.findAll('[data-test="change-row"]')).toHaveLength(3)
})

test('a stored diff opens from its alert', async () => {
  stubApi({
    'GET /alerts': {
      body: {
        items: [
          {
            id: 5,
            project_id: 1,
            project_name: 'Edge',
            type: 'changes_detected',
            summary: 'Nightly: 3 changes since scan 8',
            link: { diff_id: 41, scan_id: 9, base_scan_id: 8 },
            created_at: '2026-11-17T02:05:00Z',
            read_at: null,
          },
        ],
        total: 1,
        limit: 200,
        offset: 0,
      },
    },
    'POST /alerts/5/read': { body: {} },
    'GET /alerts/unread-count': { body: { unread: 0 } },
    ...SESSION,
  })
  const router = await mountAt(AlertsView, {}, '/alerts')

  await wrapper!.find('[data-test="alert-5"]').trigger('click')
  await flushPromises()

  expect(router.currentRoute.value.fullPath).toBe('/diffs/41')
})
