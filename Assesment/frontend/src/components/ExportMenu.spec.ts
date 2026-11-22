import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { expect, test } from 'vitest'

import vuetify from '../plugins/vuetify'
import { useWorkspaceStore } from '../stores/workspace'
import ExportMenu from './ExportMenu.vue'

async function links(props: InstanceType<typeof ExportMenu>['$props']): Promise<string[]> {
  const wrapper = mount(ExportMenu, { props, global: { plugins: [vuetify] }, attachTo: document.body })
  await wrapper.find('[data-test="export"]').trigger('click')
  await flushPromises()
  const found = ['csv', 'json'].map(
    (format) =>
      document.body.querySelector(`[data-test="export-${format}"]`)?.getAttribute('href') ?? '',
  )
  wrapper.unmount()
  document.body.innerHTML = ''
  return found
}

test('the menu links to the endpoint with the query and each format', async () => {
  const [csv, json] = await links({
    path: '/scans/3/hosts',
    query: { reachable: true, open_port: 443, ptr: 'mail example', tag: undefined, sort: 'asn' },
  })

  expect(csv).toBe('/api/v1/scans/3/hosts?reachable=true&open_port=443&ptr=mail+example&sort=asn&format=csv')
  expect(json).toBe('/api/v1/scans/3/hosts?reachable=true&open_port=443&ptr=mail+example&sort=asn&format=json')
})

test('the host export asks for what the table shows: its filters and its order', () => {
  setActivePinia(createPinia())
  const workspace = useWorkspaceStore()
  workspace.scan = { id: 3 } as typeof workspace.scan
  workspace.filters = { open_port: 443, country: 'NL', change: 'appeared' }
  workspace.sort = 'asn'
  workspace.order = 'desc'

  expect(workspace.exportTarget).toEqual({
    path: '/scans/3/hosts',
    query: { open_port: 443, country: 'NL', change: 'appeared', sort: 'asn', order: 'desc' },
  })

  workspace.filters = { open_port: 443 }
  expect(workspace.exportTarget?.query).toEqual({
    reachable: true,
    open_port: 443,
    sort: 'asn',
    order: 'desc',
  })
})
