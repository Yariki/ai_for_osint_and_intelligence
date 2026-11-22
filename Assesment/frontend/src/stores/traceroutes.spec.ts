import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, test } from 'vitest'

import { errorReply, stubApi } from '../test-setup'
import { type Traceroute, type TracerouteDetail, useTraceroutesStore } from './traceroutes'

function run(id: number, changes: Partial<Traceroute> = {}): Traceroute {
  return {
    id,
    project_id: 1,
    target: '8.8.8.8',
    method: 'icmp',
    parameters: { port: null, max_hops: 30, probes_per_hop: 2, timeout_ms: 1000 },
    state: 'running',
    reached: null,
    failure_reason: null,
    vantage_point: 'default',
    created_at: '2026-11-17T10:00:00Z',
    dispatched_at: '2026-11-17T10:00:01Z',
    finished_at: null,
    ...changes,
  }
}

function hopEvent(runId: number, ttl: number | null, responders: Array<[string, number | null, number | null]>, state = 'running') {
  return {
    type: 'traceroute_hop',
    project_id: 1,
    run_id: runId,
    target: '8.8.8.8',
    state,
    ttl,
    responders: responders.map(([address, latitude, longitude]) => ({
      address,
      rtt_ms: [1.5, null],
      unlocatable: latitude === null,
      latitude,
      longitude,
    })),
    lost: responders.length ? 1 : 2,
  }
}

beforeEach(() => setActivePinia(createPinia()))

test('the history of a host is loaded for its project and address', async () => {
  const calls = stubApi({
    'GET /projects/1/traceroutes': { body: { items: [run(9), run(4)], total: 2, limit: 50, offset: 0 } },
  })
  const store = useTraceroutesStore()

  await store.fetchHistory(1, '8.8.8.8')

  expect(store.history.map((item) => item.id)).toEqual([9, 4])
  expect(calls[0]?.query).toEqual({ target: '8.8.8.8', limit: '50' })
})

test('a started traceroute is put first in the history and opened', async () => {
  const calls = stubApi({
    'GET /projects/1/traceroutes': { body: { items: [run(4)], total: 1, limit: 50, offset: 0 } },
    'POST /projects/1/traceroutes': { status: 201, body: run(9, { state: 'queued' }) },
  })
  const store = useTraceroutesStore()
  await store.fetchHistory(1, '8.8.8.8')

  await store.start(1, { target: '8.8.8.8', method: 'tcp', port: null, max_hops: 30, probes_per_hop: 3, timeout_ms: 1000 })

  expect(calls.at(-1)?.body).toMatchObject({ target: '8.8.8.8', method: 'tcp' })
  expect(store.history.map((item) => item.id)).toEqual([9, 4])
  expect(store.current).toMatchObject({ id: 9, hops: [] })
})

test('a refusal by the server is passed on and changes nothing', async () => {
  stubApi({
    'POST /projects/1/traceroutes': errorReply(422, 'target_rejected', 'The traceroute was not started.', [
      { field: 'target', code: 'out_of_scope', message: 'The address is outside the project scope.' },
    ]),
  })
  const store = useTraceroutesStore()

  await expect(
    store.start(1, { target: '9.9.9.9', method: 'icmp', port: null, max_hops: 30, probes_per_hop: 3, timeout_ms: 1000 }),
  ).rejects.toMatchObject({ code: 'target_rejected' })
  expect(store.current).toBeNull()
  expect(store.history).toEqual([])
})

test('hop events are applied in TTL order, whatever order they arrive in', () => {
  const store = useTraceroutesStore()
  store.current = { ...run(9), hops: [] } as TracerouteDetail

  store.applyEvent(hopEvent(9, 3, [['193.0.6.1', 52.37, 4.89]]))
  store.applyEvent(hopEvent(9, 1, [['10.0.0.1', null, null]]))
  store.applyEvent(hopEvent(9, 2, []))
  // The same TTL again replaces it rather than adding a second one.
  store.applyEvent(hopEvent(9, 1, [['10.0.0.1', null, null]]))

  const hops = store.current?.hops ?? []
  expect(hops.map((hop) => hop.ttl)).toEqual([1, 2, 3])
  expect(hops.map((hop) => hop.silent)).toEqual([false, true, false])
  expect(hops[0]?.responders[0]).toMatchObject({ address: '10.0.0.1', unlocatable: true })
  expect(hops[2]?.responders[0]).toMatchObject({ latitude: 52.37, longitude: 4.89 })
  expect(hops[1]?.lost).toBe(2)
})

test('events of another run, or of no traceroute, change nothing in the open run', () => {
  const store = useTraceroutesStore()
  store.current = { ...run(9), hops: [] } as TracerouteDetail
  store.history = [run(9), run(4)]

  store.applyEvent(hopEvent(4, 1, [['10.0.0.1', null, null]], 'completed'))
  store.applyEvent({ type: 'hosts_changed', scan_id: 1, project_id: 1, hosts: [] })

  expect(store.current?.hops).toEqual([])
  // The history shows the other run's state.
  expect(store.history[1]?.state).toBe('completed')
})

test('when the run ends it is read again, with its enrichment and names', async () => {
  const done: TracerouteDetail = {
    ...run(9, { state: 'completed', reached: true }),
    hops: [
      {
        ttl: 1,
        responders: [{ address: '8.8.8.8', rtt_ms: [1.5], unlocatable: false, ptr_names: ['dns.example.net'], ptr_status: 'ok' }],
        probes: [],
        lost: 0,
        silent: false,
      },
    ],
  }
  const calls = stubApi({ 'GET /traceroutes/9': { body: done } })
  const store = useTraceroutesStore()
  store.current = { ...run(9), hops: [] } as TracerouteDetail

  store.applyEvent(hopEvent(9, null, [], 'completed'))
  await flushPromises()

  expect(calls.map((call) => call.key)).toEqual(['GET /traceroutes/9'])
  expect(store.current).toEqual(done)
})
