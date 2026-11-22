import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import { useEventStream } from '../composables/useEventStream'
import vuetify from '../plugins/vuetify'
import { createAppRouter } from '../router'
import { useAlertsStore } from '../stores/alerts'
import { FakeEventSource } from '../test-fixtures'
import { stubApi } from '../test-setup'
import AlertIndicator from './AlertIndicator.vue'

let wrapper: VueWrapper

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})

afterEach(() => {
  wrapper.unmount()
  useEventStream().close()
})

async function mountIndicator(unread: () => number) {
  const calls = stubApi({
    'GET /alerts/unread-count': () => ({ body: { unread: unread() } }),
    'POST /alerts/5/read': { body: {} },
    'GET /alerts': { body: { items: [], total: 0, limit: 200, offset: 0 } },
  })
  wrapper = mount(AlertIndicator, {
    global: { plugins: [createPinia(), createAppRouter(createMemoryHistory()), vuetify] },
  })
  await flushPromises()
  return calls
}

function count(): string | null {
  const badge = wrapper.find('[data-test="alert-count"]')
  return badge.exists() ? badge.text() : null
}

test('the indicator shows the unread count from the server', async () => {
  await mountIndicator(() => 3)

  expect(count()).toContain('3')
})

test('an alert-created event raises the count without a reload', async () => {
  const calls = await mountIndicator(() => 0)
  expect(count()).toBeNull()
  const source = FakeEventSource.latest()
  source.open()

  source.send('alert_created', {
    type: 'alert_created',
    project_id: 1,
    alert_id: 5,
    alert_type: 'changes_detected',
    summary: 'Nightly: 3 changes',
  })
  await flushPromises()

  expect(count()).toContain('1')
  expect(calls.filter((call) => call.key === 'GET /alerts/unread-count')).toHaveLength(1)
})

test('the indicator follows the read state', async () => {
  let unread = 1
  await mountIndicator(() => unread)
  expect(count()).toContain('1')

  unread = 0
  await useAlertsStore().markRead(5)
  await flushPromises()

  expect(count()).toBeNull()
})

test('after a lost connection the count is loaded again', async () => {
  let unread = 1
  await mountIndicator(() => unread)
  const source = FakeEventSource.latest()
  source.open()
  source.drop()
  unread = 4 // alerts raised while the connection was away

  source.open()
  await flushPromises()

  expect(count()).toContain('4')
})
