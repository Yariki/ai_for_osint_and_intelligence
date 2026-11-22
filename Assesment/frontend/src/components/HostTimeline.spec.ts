import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, test } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import type { Schemas } from '../api/client'
import vuetify from '../plugins/vuetify'
import { createAppRouter } from '../router'
import { useWorkspaceStore } from '../stores/workspace'
import { ENRICHMENT, hostDetail } from '../test-fixtures'
import HostDetailPanel from './HostDetailPanel.vue'
import HostTimeline from './HostTimeline.vue'

type Entry = Schemas['TimelineEntryOut']

let wrapper: VueWrapper | undefined

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
})

function entry(scanId: number, startedAt: string | null, changes: Partial<Entry> = {}): Entry {
  return {
    scan_id: scanId,
    state: startedAt ? 'completed' : 'queued',
    started_at: startedAt,
    finished_at: startedAt,
    reachable: true,
    open_ports: [22],
    opened: [],
    closed: [],
    ...changes,
  }
}

function plugins() {
  return [vuetify, createAppRouter(createMemoryHistory())]
}

function rows(component: VueWrapper): string[][] {
  return component
    .findAll('[data-test="timeline-entry"]')
    .map((row) => row.findAll('td').map((cell) => cell.text().replace(/\s+/g, ' ')))
}

// --- The timeline ---

test('the timeline orders scans by time, whatever order they arrive in', () => {
  wrapper = mount(HostTimeline, {
    props: {
      // Scan 12 was created first and started last; scan 15 has not started.
      entries: [
        entry(15, null, { reachable: false, open_ports: [] }),
        entry(12, '2026-11-16T09:00:00Z', { open_ports: [22, 443], opened: [443] }),
        entry(14, '2026-11-15T09:00:00Z', { open_ports: [22], closed: [80] }),
        entry(13, '2026-11-14T09:00:00Z', { open_ports: [22, 80] }),
      ],
      current: 14,
      firstSeenAt: '2026-11-14T09:00:01Z',
      lastSeenAt: '2026-11-16T09:00:02Z',
    },
    global: { plugins: plugins() },
  })

  const table = rows(wrapper)
  expect(table.map((row) => row[0])).toEqual(['#13', '#14', '#12', '#15'])
  expect(table.map((row) => row[3])).toEqual(['22, 80', '22', '22, 443', '—'])
  expect(table.map((row) => row[4])).toEqual(['—', 'closed: 80', 'opened: 443', '—'])
  expect(table[3]?.[2]).toBe('no answer (queued)')
  // The scan the detail was opened in is marked.
  const marked = wrapper.findAll('[data-test="timeline-entry"]').filter((row) =>
    row.classes().includes('font-weight-bold'),
  )
  expect(marked.map((row) => row.find('td').text())).toEqual(['#14'])
})

test('opened and closed ports of one scan are both shown', () => {
  wrapper = mount(HostTimeline, {
    props: {
      entries: [entry(4, '2026-11-15T09:00:00Z', { opened: [443, 8080], closed: [80] })],
      current: 4,
      firstSeenAt: null,
      lastSeenAt: null,
    },
    global: { plugins: plugins() },
  })

  expect(rows(wrapper)[0]?.[4]).toBe('opened: 443, 8080; closed: 80')
  expect(wrapper.find('[data-test="timeline-seen"]').text()).toContain('First seen in this project: —')
})

// --- The detail panel: enrichment, with unavailable shown as unavailable ---

function panel(detail: ReturnType<typeof hostDetail>): VueWrapper {
  const workspace = useWorkspaceStore()
  workspace.selected = detail
  wrapper = mount(HostDetailPanel, { global: { plugins: plugins() } })
  return wrapper
}

function field(component: VueWrapper, name: string): string {
  return component.find(`[data-test="enrichment-${name}"] td`).text()
}

test('the detail shows registration, location with its radius, versions and reverse DNS', () => {
  const component = panel(hostDetail('8.8.8.8'))

  expect(field(component, 'asn')).toBe('64496')
  expect(field(component, 'as_holder')).toBe('EXAMPLE-DNS')
  expect(field(component, 'organization')).toBe('Example DNS LLC (from the registry)')
  expect(field(component, 'rir')).toBe('ARIN')
  expect(field(component, 'location')).toBe('37.4056, -122.0775')
  expect(field(component, 'accuracy_radius_km')).toBe('1000 km')
  // Location is called approximate wherever it is shown (FR-ENR-8).
  for (const name of ['country', 'city', 'location']) {
    expect(component.find(`[data-test="enrichment-${name}"] th`).text()).toContain('approximate')
  }
  expect(component.find('[data-test="location-note"]').text()).toContain('approximate')
  const versions = component.find('[data-test="dataset-versions"]').text().replace(/\s+/g, ' ')
  expect(versions).toContain('geolite2-city 2026-11-13, rir-delegation-arin 2026-11-16')
  expect(component.find('[data-test="reverse-dns"]').text()).toBe('dns.example.net')
})

test('an unavailable field is rendered as unavailable, and an empty one as a dash', () => {
  const component = panel(
    hostDetail('8.8.8.8', {
      enrichment: {
        ...ENRICHMENT,
        country: null,
        city: null,
        latitude: null,
        longitude: null,
        accuracy_radius_km: null,
        network_name: null,
        organization: 'EXAMPLE-DNS',
        organization_source: 'as_holder',
        unavailable: ['country', 'city', 'location', 'accuracy_radius_km'],
      },
    }),
  )

  for (const name of ['country', 'city', 'location', 'accuracy_radius_km']) {
    expect(field(component, name)).toBe('unavailable')
  }
  // The registry was read and has no network name: empty, which is not the same.
  expect(field(component, 'network_name')).toBe('—')
  expect(field(component, 'asn')).toBe('64496')
  expect(field(component, 'organization')).toBe(
    'EXAMPLE-DNS (the AS holder: the registry has no record)',
  )
  expect(component.find('[data-test="location-note"]').text()).toContain('missing or out of date')
})

test('a host that was not enriched says so, and its reverse DNS state is put in words', () => {
  const silent = panel(
    hostDetail('8.8.8.4', {
      reachable: false,
      evidence: [],
      open_ports: [],
      enrichment: null,
      reverse_dns: { names: [], status: null },
    }),
  )
  expect(silent.find('[data-test="enrichment-none"]').exists()).toBe(true)
  expect(silent.find('[data-test="reverse-dns"]').text()).toBe('Not looked up.')
  silent.unmount()

  for (const [status, words] of [
    ['no_name', 'no name for this address'],
    ['failed', 'did not answer'],
    ['disabled', 'switched off'],
  ] as const) {
    const component = panel(hostDetail('8.8.8.2', { reverse_dns: { names: [], status } }))
    expect(component.find('[data-test="reverse-dns"]').text()).toContain(words)
    component.unmount()
  }
  wrapper = undefined
})

test('the timeline of the host is part of its detail', () => {
  const component = panel(
    hostDetail('8.8.8.8', {
      timeline: [entry(7, '2026-11-15T10:00:00Z'), entry(3, '2026-11-10T10:00:00Z')],
    }),
  )

  expect(rows(component).map((row) => row[0])).toEqual(['#3', '#7'])
})
