import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, test } from 'vitest'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import type { HostFilters } from '../stores/workspace'
import { filtersFromQuery, filtersToQuery, useHostFilters } from './useHostFilters'

beforeEach(() => setActivePinia(createPinia()))

// --- The URL and the filters ---

test('filters round-trip through the URL', () => {
  const cases: HostFilters[] = [
    {},
    { open_port: 443 },
    { asn: 64496 },
    { organization: 'Example DNS' },
    { country: 'NL' },
    { ptr: 'mail.example' },
    { device_type: 'dns_server' },
    { tag: 'edge' },
    { change: 'changed' },
    { change: 'disappeared', open_port: 22 },
    { device_type: 'server', tag: 'customer A', open_port: 443 },
    { open_port: 22, asn: 4294967295, organization: 'Ünïversity & Co.', country: 'US', ptr: 'a b' },
  ]

  for (const filters of cases) {
    expect(filtersFromQuery(filtersToQuery(filters))).toEqual(filters)
  }
})

test('a set of filters becomes plain query parameters, and an empty one becomes none', () => {
  expect(filtersToQuery({ open_port: 443, country: 'NL' })).toEqual({
    open_port: '443',
    country: 'NL',
  })
  expect(filtersToQuery({})).toEqual({})
  expect(filtersToQuery({ organization: '  ' })).toEqual({})
})

test('the change status is mirrored in the URL, and an unknown one is left out', () => {
  expect(filtersToQuery({ change: 'appeared' })).toEqual({ change: 'appeared' })
  expect(filtersFromQuery({ change: 'appeared' })).toEqual({ change: 'appeared' })
  expect(filtersFromQuery({ change: 'moved' })).toEqual({})
})

test('what is not a valid filter value in a URL is left out', () => {
  expect(
    filtersFromQuery({
      open_port: '70000',
      asn: 'AS64496',
      country: 'Netherlands',
      organization: '   ',
      ptr: ['first', 'second'],
      scan: '12',
      unknown: 'x',
    }),
  ).toEqual({ ptr: 'first' })
  expect(filtersFromQuery({ open_port: '0' })).toEqual({})
  expect(filtersFromQuery({ country: 'nl', open_port: ' 80 ' })).toEqual({
    country: 'NL',
    open_port: 80,
  })
})

// --- In a component: the address bar is where the filters are kept ---

function mounted(router: Router) {
  let filters!: ReturnType<typeof useHostFilters>
  const Probe = defineComponent({
    setup() {
      filters = useHostFilters()
      return () => h('div')
    },
  })
  mount(Probe, { global: { plugins: [router] } })
  return filters
}

async function routerAt(path: string): Promise<Router> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/projects/:id/workspace', component: { render: () => h('div') } }],
  })
  await router.push(path)
  return router
}

test('the filters of a view are read from its URL, so a reload shows the same view', async () => {
  const router = await routerAt('/projects/3/workspace?scan=12&open_port=443&organization=Example')

  const filters = mounted(router)

  expect(filters.filters.value).toEqual({ open_port: 443, organization: 'Example' })
  expect(filters.active.value).toBe(true)
})

test('setting filters rewrites the URL and keeps what else is in it', async () => {
  const router = await routerAt('/projects/3/workspace?scan=12&open_port=443')
  const filters = mounted(router)

  await filters.set({ country: 'nl', ptr: 'www' })
  await flushPromises()

  expect(router.currentRoute.value.query).toEqual({ scan: '12', country: 'NL', ptr: 'www' })
  expect(filters.filters.value).toEqual({ country: 'NL', ptr: 'www' })

  await filters.clear()
  expect(router.currentRoute.value.query).toEqual({ scan: '12' })
  expect(filters.active.value).toBe(false)
})

test('going back in the history brings the earlier filters back', async () => {
  const router = await routerAt('/projects/3/workspace?asn=64496')
  const filters = mounted(router)

  await router.push('/projects/3/workspace?asn=64500')
  expect(filters.filters.value).toEqual({ asn: 64500 })

  router.back()
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 0))
  expect(filters.filters.value).toEqual({ asn: 64496 })
})


test('a device type that cannot be one is left out of the filters', () => {
  expect(filtersFromQuery({ device_type: 'Router; DROP', tag: 'x' })).toEqual({ tag: 'x' })
})
