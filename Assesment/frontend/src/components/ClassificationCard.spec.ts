import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, test } from 'vitest'

import vuetify from '../plugins/vuetify'
import { useWorkspaceStore } from '../stores/workspace'
import { hostDetail, hostRow, scan } from '../test-fixtures'
import { type Call, stubApi } from '../test-setup'
import ClassificationCard from './ClassificationCard.vue'
import HostAnnotations from './HostAnnotations.vue'

const CLASSIFICATION = {
  run: 1,
  created_at: '2026-11-17T10:00:00Z',
  device_type: 'server',
  sub_role: 'web',
  confidence: 0.65,
  rules: [
    { rule: 'server-web-port', type: 'server', sub_role: 'web', confidence: 0.5, evidence: { open_ports: [80, 443] } },
    { rule: 'server-web-name', type: 'server', sub_role: 'web', confidence: 0.3, evidence: { ptr_names: ['www.example.nl'] } },
  ],
  ruleset_version: '1',
}

let wrapper: VueWrapper | undefined

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  document.body.innerHTML = ''
})

/** A workspace on scan 7 of project 1, with 193.0.6.1 selected; the server follows the override. */
async function selected(override: string | null = null): Promise<{ calls: Call[]; state: { override: string | null } }> {
  const state = { override }
  const detail = () => ({
    body: hostDetail('193.0.6.1', {
      classification: CLASSIFICATION,
      type_override: state.override,
      effective_type: state.override ?? 'server',
    }),
  })
  const calls = stubApi({
    'GET /scans/7': { body: scan(7, { state: 'completed' }) },
    'GET /scans/7/hosts': { body: { items: [hostRow('193.0.6.1')], total: 1, limit: 50, offset: 0 } },
    'GET /scans/7/map-points': { body: { located: [], unlocated: [] } },
    'GET /scans/7/hosts/193.0.6.1': detail,
    'PUT /projects/1/hosts/193.0.6.1/override': (body) => {
      state.override = (body as { device_type: string }).device_type
      return { body: {} }
    },
    'DELETE /projects/1/hosts/193.0.6.1/override': () => {
      state.override = null
      return { body: {} }
    },
    'PUT /projects/1/hosts/193.0.6.1/annotations': { body: {} },
  })
  const workspace = useWorkspaceStore()
  await workspace.open(7)
  await workspace.select('193.0.6.1')
  wrapper = mount(ClassificationCard, { global: { plugins: [vuetify] }, attachTo: document.body })
  await flushPromises()
  return { calls, state }
}

test('the automatic result is shown with its confidence, version and evidence', async () => {
  await selected()

  const card = wrapper!
  expect(card.find('[data-test="classification-override"]').exists()).toBe(false)
  expect(card.find('[data-test="classification-automatic"]').text()).toBe(
    'Server (web), confidence 65%, ruleset 1.',
  )
  const rules = card.findAll('[data-test="classification-rules"] li').map((item) => item.text())
  expect(rules).toEqual([
    'server-web-port → Server (50%): open ports 80, 443',
    'server-web-name → Server (30%): name www.example.nl',
  ])
})

test('with an override set, both values are rendered', async () => {
  await selected('router')

  const card = wrapper!
  expect(card.find('[data-test="classification-override"]').text()).toContain('Router, set by the analyst')
  expect(card.find('[data-test="classification-automatic"]').text()).toBe(
    'Automatic: Server (web), confidence 65%, ruleset 1.',
  )
  expect(card.find('[data-test="override-clear"]').exists()).toBe(true)
})

test('clearing the override shows the automatic value alone', async () => {
  const { calls } = await selected('router')

  await wrapper!.find('[data-test="override-clear"]').trigger('click')
  await flushPromises()

  expect(calls.some((call) => call.key === 'DELETE /projects/1/hosts/193.0.6.1/override')).toBe(true)
  expect(wrapper!.find('[data-test="classification-override"]').exists()).toBe(false)
  expect(wrapper!.find('[data-test="classification-automatic"]').text()).toBe(
    'Server (web), confidence 65%, ruleset 1.',
  )
})

test('setting an override sends it and shows it beside the automatic value', async () => {
  const { calls } = await selected()

  await wrapper!.find('[data-test="override-open"]').trigger('click')
  await flushPromises()
  // Choose in the store directly: the select's menu is a teleported overlay.
  await useWorkspaceStore().setOverride('dns_server')
  await flushPromises()

  expect(calls.find((call) => call.key === 'PUT /projects/1/hosts/193.0.6.1/override')?.body).toEqual({
    device_type: 'dns_server',
  })
  expect(wrapper!.find('[data-test="classification-override"]').text()).toContain('DNS server')
  expect(wrapper!.find('[data-test="classification-automatic"]').text()).toContain('Automatic: Server')
})

test('a host that is not classified yet says so', async () => {
  stubApi({})
  const workspace = useWorkspaceStore()
  workspace.selected = hostDetail('8.8.8.8')
  wrapper = mount(ClassificationCard, { global: { plugins: [vuetify] } })

  expect(wrapper.find('[data-test="classification-automatic"]').text()).toBe('Not classified yet.')
})

test('tags and notes are saved for the host and the view is read again', async () => {
  const { calls } = await selected()
  wrapper!.unmount()
  wrapper = mount(HostAnnotations, { global: { plugins: [vuetify] }, attachTo: document.body })
  await flushPromises()

  await wrapper.find('[data-test="annotations-notes"] textarea').setValue('  Campus web server. ')
  await useWorkspaceStore().saveAnnotations(['campus', 'web'], '  Campus web server. ')
  await flushPromises()

  const saved = calls.find((call) => call.key === 'PUT /projects/1/hosts/193.0.6.1/annotations')
  expect(saved?.body).toEqual({ tags: ['campus', 'web'], notes: 'Campus web server.' })
  // The detail was loaded again after saving.
  expect(calls.filter((call) => call.key === 'GET /scans/7/hosts/193.0.6.1').length).toBeGreaterThan(1)
})
