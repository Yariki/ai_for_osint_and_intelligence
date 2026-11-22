import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import vuetify from '../plugins/vuetify'
import { errorReply, stubApi } from '../test-setup'
import DatasetSettingsView from '../views/DatasetSettingsView.vue'
import { age, type Dataset, describe, useDatasetsStore } from './datasets'

function dataset(name: string, changes: Partial<Dataset> = {}): Dataset {
  return {
    name,
    version: '2026-11-13',
    status: 'ok',
    detail: null,
    loaded_at: '2026-11-14T06:00:00Z',
    age_seconds: 3 * 86400 + 120,
    checked_at: '2026-11-17T06:00:00Z',
    update_requested: false,
    ...changes,
  }
}

const DATASETS = [
  dataset('geolite2-city'),
  dataset('geolite2-asn', {
    version: '2026-10-02',
    status: 'stale',
    age_seconds: 46 * 86400,
    detail: 'MaxMind refused the account ID or the licence key (GeoLite2-ASN).',
  }),
  dataset('rir-objects-arin', {
    version: null,
    status: 'missing',
    loaded_at: null,
    age_seconds: null,
    detail: 'There is no arin_db.xml in the directory for registry files.',
  }),
]

let wrapper: VueWrapper | undefined

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  vi.useRealTimers()
})

// --- The store ---

test('the datasets are loaded as the API reports them', async () => {
  stubApi({ 'GET /datasets': { body: DATASETS } })
  const store = useDatasetsStore()

  await store.fetchDatasets()

  expect(store.datasets).toEqual(DATASETS)
})

test('the trigger asks for an update of one dataset and shows that it was asked for', async () => {
  const calls = stubApi({
    'GET /datasets': { body: DATASETS },
    'POST /datasets/geolite2-asn/update': {
      status: 202,
      body: { ...DATASETS[1], update_requested: true },
    },
  })
  const store = useDatasetsStore()
  await store.fetchDatasets()

  await store.requestUpdate('geolite2-asn')

  expect(calls.at(-1)?.key).toBe('POST /datasets/geolite2-asn/update')
  expect(store.datasets.map((item) => item.update_requested)).toEqual([false, true, false])
})

test('ages and dataset names are put in words', () => {
  expect(age(null)).toBe('—')
  expect(age(1200)).toBe('under an hour')
  expect(age(3600)).toBe('1 hour')
  expect(age(5 * 3600 + 59)).toBe('5 hours')
  expect(age(86400)).toBe('1 day')
  expect(age(46 * 86400)).toBe('46 days')
  expect(describe('geolite2-city')).toContain('coordinates')
  expect(describe('rir-delegation-ripe')).toBe(
    'RIPE delegation statistics: registry and registration country',
  )
  expect(describe('rir-objects-lacnic')).toContain('LACNIC registration records')
  expect(describe('something-new')).toBe('something-new')
})

// --- The settings page ---

async function page(replies: Parameters<typeof stubApi>[0]) {
  const calls = stubApi(replies)
  wrapper = mount(DatasetSettingsView, { global: { plugins: [vuetify] } })
  await flushPromises()
  return { calls, component: wrapper }
}

test('the page shows the version, age and status the API reports for each dataset', async () => {
  const { component } = await page({ 'GET /datasets': { body: DATASETS } })

  const rows = component.findAll('[data-test="dataset-row"]')
  expect(rows).toHaveLength(3)
  const cells = (name: string) => rows.map((row) => row.find(`[data-test="${name}"]`).text())
  expect(cells('dataset-version')).toEqual(['2026-11-13', '2026-10-02', '—'])
  expect(cells('dataset-age')).toEqual(['3 days', '46 days', '—'])
  expect(cells('dataset-status')).toEqual(['in use', 'too old, not used', 'no data'])
  expect(rows[1]?.find('[data-test="dataset-detail"]').text()).toContain('refused')
  expect(rows[2]?.find('[data-test="dataset-detail"]').text()).toContain('arin_db.xml')
  expect(component.text()).toContain('MaxMind')
})

test('the trigger starts an update, and the page follows it until it is done', async () => {
  vi.useFakeTimers()
  const state = { datasets: DATASETS.map((item) => ({ ...item })) }
  const { calls, component } = await page({
    'GET /datasets': () => ({ body: state.datasets }),
    'POST /datasets/geolite2-asn/update': () => {
      state.datasets[1] = { ...DATASETS[1]!, update_requested: true }
      return { status: 202, body: state.datasets[1] }
    },
  })

  await component.findAll('[data-test="dataset-update"]')[1]?.trigger('click')
  await flushPromises()

  expect(calls.at(-1)?.key).toBe('POST /datasets/geolite2-asn/update')
  const button = () => component.findAll('[data-test="dataset-update"]')[1]
  expect(button()?.text()).toBe('Update requested')
  expect(button()?.attributes('disabled')).toBeDefined()

  // The orchestrator carries the update out; the page reads the list again and shows it.
  state.datasets[1] = dataset('geolite2-asn', { version: '2026-11-17', age_seconds: 30 })
  await vi.advanceTimersByTimeAsync(5100)
  await flushPromises()

  expect(component.findAll('[data-test="dataset-version"]')[1]?.text()).toBe('2026-11-17')
  expect(component.findAll('[data-test="dataset-status"]')[1]?.text()).toBe('in use')
  expect(button()?.text()).toBe('Update now')
  // With nothing pending, the page stops asking.
  const requests = calls.length
  await vi.advanceTimersByTimeAsync(20000)
  expect(calls).toHaveLength(requests)
})

test('before the orchestrator has run, the page says that no dataset is known', async () => {
  const { component } = await page({ 'GET /datasets': { body: [] } })

  expect(component.find('[data-test="datasets-empty"]').exists()).toBe(true)
})

test('an error of the trigger is shown', async () => {
  const { component } = await page({
    'GET /datasets': { body: DATASETS },
    'POST /datasets/geolite2-city/update': errorReply(404, 'not_found', 'There is no such dataset.'),
  })

  await component.find('[data-test="dataset-update"]').trigger('click')
  await flushPromises()

  expect(component.text()).toContain('There is no such dataset.')
})
