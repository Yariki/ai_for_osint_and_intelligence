import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, test } from 'vitest'

import type { ClusterHost } from '../composables/useMap'
import vuetify from '../plugins/vuetify'
import { useWorkspaceStore } from '../stores/workspace'
import { hostDetail } from '../test-fixtures'
import { stubApi } from '../test-setup'
import ClusterHostList from './ClusterHostList.vue'
import UnlocatedPanel from './UnlocatedPanel.vue'

let wrapper: VueWrapper | undefined

beforeEach(() => setActivePinia(createPinia()))

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  document.body.innerHTML = ''
})

function hosts(count: number): ClusterHost[] {
  return Array.from({ length: count }, (_, index) => ({
    address: `8.8.${Math.floor(index / 256)}.${index % 256}`,
    scan_id: 7,
    open_port_count: index % 4,
  }))
}

function items(): string[] {
  return [...document.querySelectorAll('[data-test="host-list-item"] .v-list-item-title')].map(
    (item) => item.textContent ?? '',
  )
}

async function nextPage(): Promise<void> {
  document.querySelector<HTMLElement>('.v-pagination__next button')?.click()
  await flushPromises()
}

// --- The list of a cluster (FR-MAP-2) ---

test('no dialog is shown while there is no cluster to list', () => {
  wrapper = mount(ClusterHostList, {
    props: { hosts: [] },
    global: { plugins: [vuetify] },
    attachTo: document.body,
  })

  expect(document.querySelector('[data-test="cluster-list"]')).toBeNull()
})

test('a small cluster lists all its hosts at once, with their open ports', async () => {
  wrapper = mount(ClusterHostList, {
    props: { hosts: hosts(4) },
    global: { plugins: [vuetify] },
    attachTo: document.body,
  })
  await flushPromises()

  const list = document.querySelector('[data-test="cluster-list"]')
  expect(list?.textContent).toContain('4 hosts at this location')
  expect(list?.textContent).toContain('approximate')
  expect(items()).toEqual(['8.8.0.0', '8.8.0.1', '8.8.0.2', '8.8.0.3'])
  expect(document.querySelector('[data-test="host-list-search"]')).toBeNull()
  expect(document.querySelector('[data-test="host-list-pages"]')).toBeNull()
})

test('every host of a cluster of 300 can be selected', async () => {
  const all = hosts(300)
  wrapper = mount(ClusterHostList, {
    props: { hosts: all },
    global: { plugins: [vuetify] },
    attachTo: document.body,
  })
  await flushPromises()

  // Go through the pages and click every host that is listed.
  for (let page = 1; page <= 6; page++) {
    for (const item of document.querySelectorAll<HTMLElement>('[data-test="host-list-item"]')) {
      item.click()
    }
    if (page < 6) await nextPage()
  }

  const selected = (wrapper.emitted('select') ?? []).map(([host]) => (host as ClusterHost).address)
  expect(selected).toHaveLength(300)
  expect(new Set(selected)).toEqual(new Set(all.map((host) => host.address)))
}, 20_000) // 300 hosts are clicked through a dialog page by page

test('a long list can be narrowed by a part of the address', async () => {
  wrapper = mount(ClusterHostList, {
    props: { hosts: hosts(300) },
    global: { plugins: [vuetify] },
    attachTo: document.body,
  })
  await flushPromises()
  await nextPage()

  const search = document.querySelector<HTMLInputElement>('[data-test="host-list-search"] input')
  if (search) {
    search.value = '8.8.1.4'
    search.dispatchEvent(new Event('input'))
  }
  await flushPromises()

  // 8.8.1.4 and 8.8.1.40 to 8.8.1.43 (the cluster ends at 8.8.1.43). Back on the first page.
  expect(items()).toEqual(['8.8.1.4', '8.8.1.40', '8.8.1.41', '8.8.1.42', '8.8.1.43'])
})

test('closing the dialog is reported to the map', async () => {
  wrapper = mount(ClusterHostList, {
    props: { hosts: hosts(2) },
    global: { plugins: [vuetify] },
    attachTo: document.body,
  })
  await flushPromises()

  document.querySelector<HTMLElement>('[data-test="cluster-list-close"]')?.click()

  expect(wrapper.emitted('close')).toHaveLength(1)
})

// --- Hosts without a location (FR-MAP-3) ---

test('hosts without coordinates are listed in the unlocated panel and can be selected', async () => {
  const calls = stubApi({ 'GET /scans/9/hosts/8.8.0.11': { body: hostDetail('8.8.0.11') } })
  const workspace = useWorkspaceStore()
  workspace.unlocated = hosts(20).map((host) => ({ ...host, scan_id: 9 }))
  wrapper = mount(UnlocatedPanel, { global: { plugins: [vuetify] }, attachTo: document.body })
  await flushPromises()

  const panel = wrapper.find('[data-test="unlocated-panel"]')
  expect(panel.text()).toContain('Without a location: 20')
  expect(panel.text()).toContain('not on the map')
  // Eight per page here; the twelfth host is on the second page.
  expect(items()).toHaveLength(8)
  await nextPage()
  const wanted = [...document.querySelectorAll<HTMLElement>('[data-test="host-list-item"]')].find(
    (item) => item.textContent?.includes('8.8.0.11'),
  )
  wanted?.click()
  await flushPromises()

  // The host is opened in the scan its entry names.
  expect(calls.at(-1)?.key).toBe('GET /scans/9/hosts/8.8.0.11')
  expect(workspace.selected?.address).toBe('8.8.0.11')
})

test('the panel says so when every host has a location', () => {
  wrapper = mount(UnlocatedPanel, { global: { plugins: [vuetify] } })

  expect(wrapper.text()).toContain('Without a location: 0')
  expect(wrapper.text()).toContain('Every host that is shown has a location')
})
