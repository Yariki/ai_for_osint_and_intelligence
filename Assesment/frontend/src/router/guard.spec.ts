import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, test } from 'vitest'
import { createMemoryHistory, type Router } from 'vue-router'

import { api, setSessionLostHandler } from '../api/client'
import { useSessionStore } from '../stores/session'
import { NOT_AUTHENTICATED, type Reply, stubApi } from '../test-setup'
import { createAppRouter } from './index'

const SESSION: Reply = { body: { username: 'analyst', expires_at: '2026-11-15T12:00:00Z' } }
const SETUP_DONE: Reply = { body: { setup_required: false } }
const SETUP_REQUIRED: Reply = { body: { setup_required: true } }

let router: Router

beforeEach(() => {
  setActivePinia(createPinia())
  router = createAppRouter(createMemoryHistory())
})

afterEach(() => {
  setSessionLostHandler(null)
})

async function visit(path: string): Promise<string> {
  await router.push(path)
  return router.currentRoute.value.fullPath
}

// --- Without a session ---

test('without a session a protected route leads to login and remembers the target', async () => {
  stubApi({ 'GET /setup': SETUP_DONE, 'GET /session': NOT_AUTHENTICATED })

  expect(await visit('/password')).toBe('/login?redirect=/password')
  expect(router.currentRoute.value.name).toBe('login')
})

test('without a session every route except setup and login leads to login', async () => {
  stubApi({ 'GET /setup': SETUP_DONE, 'GET /session': NOT_AUTHENTICATED })

  for (const route of router.getRoutes()) {
    if (route.name === 'setup' || route.name === 'login') continue
    await router.push(route.path.replace(/:\w+/g, '1'))
    expect(router.currentRoute.value.name, `route ${route.path}`).toBe('login')
  }
})

test('without a session the login view is reachable', async () => {
  stubApi({ 'GET /setup': SETUP_DONE, 'GET /session': NOT_AUTHENTICATED })

  expect(await visit('/login')).toBe('/login')
})

// --- First run ---

test('while setup is required every route leads to setup', async () => {
  stubApi({ 'GET /setup': SETUP_REQUIRED })

  expect(await visit('/password')).toBe('/setup')
  expect(await visit('/login')).toBe('/setup')
  expect(await visit('/setup')).toBe('/setup')
})

test('once setup is done the setup view leads to login', async () => {
  stubApi({ 'GET /setup': SETUP_DONE, 'GET /session': NOT_AUTHENTICATED })

  expect(await visit('/setup')).toBe('/login')
})

// --- With a session ---

test('with a session the target is reached and login leads away', async () => {
  const calls = stubApi({ 'GET /setup': SETUP_DONE, 'GET /session': SESSION })

  expect(await visit('/password')).toBe('/password')
  await router.push('/login')
  expect(router.currentRoute.value.name).not.toBe('login')

  // The server is asked once, not on every navigation.
  expect(calls.filter((call) => call.key === 'GET /session')).toHaveLength(1)
})

// --- A 401 from any call ---

test('after a 401 from any call the analyst is sent to login', async () => {
  stubApi({
    'GET /setup': SETUP_DONE,
    'GET /session': SESSION,
    'GET /projects': NOT_AUTHENTICATED,
  })
  await visit('/password')
  const session = useSessionStore()
  expect(session.isAuthenticated).toBe(true)

  await expect(api.get('/projects')).rejects.toMatchObject({ status: 401 })
  await flushPromises()

  expect(session.isAuthenticated).toBe(false)
  expect(router.currentRoute.value.fullPath).toBe('/login?redirect=/password')
})
