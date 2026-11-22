import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, test } from 'vitest'

import { ApiError } from '../api/client'
import { CONFIG, scan } from '../test-fixtures'
import { errorReply, stubApi } from '../test-setup'
import { type Scan, useScansStore } from './scans'

function page(items: Scan[]) {
  return { body: { items, total: items.length, limit: 200, offset: 0 } }
}

beforeEach(() => {
  setActivePinia(createPinia())
})

test('fetchScans loads the scans of a project', async () => {
  const calls = stubApi({ 'GET /projects/1/scans': page([scan(2), scan(1)]) })
  const store = useScansStore()

  await store.fetchScans(1)

  expect(store.scans.map((item) => item.id)).toEqual([2, 1])
  expect(calls[0]?.query).toEqual({ limit: '200' })
})

test('fetchConfig keeps the ceilings and the scanner capabilities', async () => {
  stubApi({ 'GET /config': { body: CONFIG } })
  const store = useScansStore()

  await store.fetchConfig()

  expect(store.config?.limits.pps.max).toBe(5000)
  expect(store.config?.scanner?.syn_capable).toBe(true)
})

test('launch sends the configuration and puts the new scan first', async () => {
  const calls = stubApi({
    'GET /projects/1/scans': page([scan(1)]),
    'POST /projects/1/scans': { status: 201, body: scan(2) },
  })
  const store = useScansStore()
  await store.fetchScans(1)
  const body = {
    targets: ['8.8.8.0/30'],
    discovery: { icmp_echo: true, tcp_ports: [80, 443] },
    scan_all_targets: false,
    port_profile: 'top-100' as const,
    custom_ports: null,
    technique: 'automatic' as const,
    pps: 1000,
    timeout_ms: 1500,
    retries: 1,
    hosts_in_parallel: 64,
  }

  const created = await store.launch(1, body)

  expect(calls[1]?.body).toEqual(body)
  expect(created.id).toBe(2)
  expect(store.scans.map((item) => item.id)).toEqual([2, 1])
})

test('a rejected launch adds nothing and carries the rejection of each target', async () => {
  stubApi({
    'POST /projects/1/scans': errorReply(422, 'targets_rejected', 'The scan was not created.', [
      { field: 'targets.0', code: 'out_of_scope', message: 'not inside the project scope' },
    ]),
  })
  const store = useScansStore()

  const error = await store
    .launch(1, { targets: ['9.9.9.9'] } as never)
    .catch((caught: unknown) => caught)

  expect(error).toBeInstanceOf(ApiError)
  expect((error as ApiError).fieldMessage('targets.0')).toBe('not inside the project scope')
  expect(store.scans).toEqual([])
})

test('estimate asks the server', async () => {
  const estimate = {
    hosts: 256,
    ports: 1000,
    probes: 256000,
    max_probes: 512000,
    seconds: 256,
    max_seconds: 512,
  }
  const calls = stubApi({ 'POST /scans/estimate': { body: estimate } })
  const request = {
    targets: ['8.8.8.0/24'],
    port_profile: 'top-1000' as const,
    custom_ports: null,
    pps: 1000,
    retries: 1,
  }

  expect(await useScansStore().estimate(request)).toEqual(estimate)
  expect(calls[0]?.body).toEqual(request)
})

test('cancel replaces the scan with what the server returns', async () => {
  stubApi({
    'GET /projects/1/scans': page([scan(1, { state: 'running' })]),
    'POST /scans/1/cancel': { body: scan(1, { state: 'running', cancel_requested: true }) },
  })
  const store = useScansStore()
  await store.fetchScans(1)

  await store.cancel(1)

  expect(store.scans[0]?.cancel_requested).toBe(true)
})

test('remove sends the explicit confirmation and drops the scan', async () => {
  const calls = stubApi({
    'GET /projects/1/scans': page([scan(2), scan(1)]),
    'DELETE /scans/2': { status: 204 },
  })
  const store = useScansStore()
  await store.fetchScans(1)

  await store.remove(2)

  expect(calls[1]).toMatchObject({ key: 'DELETE /scans/2', query: { confirm: 'true' } })
  expect(store.scans.map((item) => item.id)).toEqual([1])
})

test('a progress event updates the scan it is about and no other', async () => {
  stubApi({ 'GET /projects/1/scans': page([scan(2), scan(1)]) })
  const store = useScansStore()
  await store.fetchScans(1)

  store.applyEvent({
    type: 'scan_progress',
    scan_id: 2,
    project_id: 1,
    state: 'running',
    phase: 'port_scan',
    chunks_done: 3,
    chunks_total: 28,
    probes_sent: 29952,
  })
  // An event for a scan that is not in the list, and one of another kind.
  store.applyEvent({ type: 'scan_progress', scan_id: 99, project_id: 5, state: 'failed' })
  store.applyEvent({ type: 'hosts_changed', scan_id: 1, project_id: 1, hosts: [] })

  expect(store.scans[0]).toMatchObject({
    state: 'running',
    phase: 'port_scan',
    chunks_done: 3,
    chunks_total: 28,
    probes_sent: 29952,
  })
  expect(store.scans[1]).toEqual(scan(1))
})

// --- Scan actions (Phase 2) ---

test('retrying the failed phase asks the server and shows the request', async () => {
  const failed = scan(4, { state: 'failed', failed_phase: 'enrichment' })
  const calls = stubApi({
    'GET /projects/1/scans': page([failed]),
    'POST /scans/4/retry-phase': { status: 202, body: { ...failed, retry_requested: true } },
  })
  const store = useScansStore()
  await store.fetchScans(1)

  await store.retryPhase(4)

  expect(calls.at(-1)?.key).toBe('POST /scans/4/retry-phase')
  expect(store.scans[0]?.retry_requested).toBe(true)
})

test('a refused retry is reported and changes nothing', async () => {
  stubApi({
    'GET /projects/1/scans': page([scan(4, { state: 'completed' })]),
    'POST /scans/4/retry-phase': errorReply(409, 'not_retryable', 'Only a scan that failed…'),
  })
  const store = useScansStore()
  await store.fetchScans(1)

  await expect(store.retryPhase(4)).rejects.toBeInstanceOf(ApiError)
  expect(store.scans[0]?.retry_requested).toBe(false)
})

test('running enrichment again asks the server and shows the request', async () => {
  const done = scan(5, { state: 'completed' })
  const calls = stubApi({
    'GET /projects/1/scans': page([done]),
    'POST /scans/5/enrich': { status: 202, body: { ...done, enrichment_requested: true } },
  })
  const store = useScansStore()
  await store.fetchScans(1)

  await store.rerunEnrichment(5)

  expect(calls.at(-1)?.key).toBe('POST /scans/5/enrich')
  expect(store.scans[0]?.enrichment_requested).toBe(true)
})

test('when a scan ends, it is read again: how it ended is not in the event', async () => {
  const running = scan(6, { state: 'running', phase: 'enrichment' })
  const calls = stubApi({
    'GET /projects/1/scans': page([running]),
    'GET /scans/6': {
      body: { ...running, state: 'failed', phase: null, failed_phase: 'enrichment' },
    },
  })
  const store = useScansStore()
  await store.fetchScans(1)

  store.applyEvent({
    type: 'scan_progress',
    scan_id: 6,
    project_id: 1,
    state: 'failed',
    phase: null,
    chunks_done: 4,
    chunks_total: 4,
    probes_sent: 40000,
  })
  await new Promise((resolve) => setTimeout(resolve, 0))

  expect(calls.at(-1)?.key).toBe('GET /scans/6')
  expect(store.scans[0]?.failed_phase).toBe('enrichment')
})
