import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { expect, test, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import App from './App.vue'
import vuetify from './plugins/vuetify'
import { createAppRouter } from './router'
import { FakeEventSource } from './test-fixtures'
import { NOT_AUTHENTICATED, stubApi } from './test-setup'

async function mountApp(replies: Parameters<typeof stubApi>[0]) {
  vi.stubGlobal('EventSource', FakeEventSource)
  const calls = stubApi(replies)
  const router = createAppRouter(createMemoryHistory())
  const wrapper = mount(App, { global: { plugins: [createPinia(), router, vuetify] } })
  await router.push('/')
  await flushPromises()
  return { wrapper, router, calls }
}

// Smoke test for the scaffold: Vue, Vuetify, Pinia and the router work together.
test('the application shell mounts and shows the product name', async () => {
  const { wrapper, router } = await mountApp({
    'GET /setup': { body: { setup_required: false } },
    'GET /session': NOT_AUTHENTICATED,
  })

  expect(wrapper.text()).toContain('Network Discovery Map')
  expect(router.currentRoute.value.name).toBe('login')
  expect(wrapper.find('[data-test="logout"]').exists()).toBe(false)
})

test('a logged-in analyst sees their name and can log out', async () => {
  const { wrapper, router, calls } = await mountApp({
    'GET /setup': { body: { setup_required: false } },
    'GET /session': { body: { username: 'analyst', expires_at: '2026-11-15T12:00:00Z' } },
    'GET /projects': { body: { items: [], total: 0, limit: 200, offset: 0 } },
    'GET /alerts/unread-count': { body: { unread: 2 } },
    'POST /logout': { status: 204 },
  })
  expect(wrapper.find('[data-test="username"]').text()).toBe('analyst')
  expect(wrapper.find('[data-test="alert-count"]').text()).toContain('2')

  await wrapper.find('[data-test="logout"]').trigger('click')
  await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('login'))

  expect(calls.map((call) => call.key)).toContain('POST /logout')
  expect(wrapper.find('[data-test="username"]').exists()).toBe(false)
})
