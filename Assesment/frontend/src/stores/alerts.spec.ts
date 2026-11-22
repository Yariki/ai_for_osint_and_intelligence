import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, test } from 'vitest'

import { stubApi } from '../test-setup'
import { type Alert, alertRoute, useAlertsStore } from './alerts'

function alert(id: number, values: Partial<Alert> = {}): Alert {
  return {
    id,
    project_id: 1,
    project_name: 'Edge',
    type: 'changes_detected',
    summary: `Alert ${id}`,
    link: { diff_id: 40 + id, scan_id: 9, base_scan_id: 8 },
    created_at: '2026-11-17T02:00:00Z',
    read_at: null,
    ...values,
  }
}

function page(items: Alert[]) {
  return { body: { items, total: items.length, limit: 200, offset: 0 } }
}

const CREATED = {
  type: 'alert_created',
  project_id: 1,
  alert_id: 3,
  alert_type: 'scheduled_run_skipped',
  summary: 'Nightly: skipped',
}

beforeEach(() => {
  setActivePinia(createPinia())
})

test('the unread count follows the read state', async () => {
  let unread = 2
  const calls = stubApi({
    'GET /alerts/unread-count': () => ({ body: { unread } }),
    'GET /alerts': page([alert(2), alert(1)]),
    'POST /alerts/2/read': () => {
      unread = 1
      return { body: alert(2, { read_at: '2026-11-17T10:00:00Z' }) }
    },
    'POST /alerts/read-all': () => {
      unread = 0
      return { body: { marked: 1 } }
    },
  })
  const store = useAlertsStore()
  await store.fetchUnread()
  await store.fetchAlerts()
  expect(store.unread).toBe(2)

  await store.markRead(2)
  expect(store.unread).toBe(1)
  expect(store.alerts[0]?.read_at).not.toBeNull()

  // Marking it again asks nothing of the server.
  const before = calls.length
  await store.markRead(2)
  expect(calls.length).toBe(before)

  await store.markAllRead()
  expect(store.unread).toBe(0)
  expect(store.alerts.every((item) => item.read_at !== null)).toBe(true)
})

test('an alert-created event raises the count at once and reloads a list on screen', async () => {
  let listed = [alert(1)]
  const calls = stubApi({
    'GET /alerts/unread-count': { body: { unread: 1 } },
    'GET /alerts': () => page(listed),
  })
  const store = useAlertsStore()
  await store.fetchUnread()

  store.applyEvent(CREATED)
  expect(store.unread).toBe(2)
  expect(calls.filter((call) => call.key === 'GET /alerts')).toHaveLength(0)

  await store.fetchAlerts(true)
  listed = [alert(3), alert(1)]
  store.applyEvent(CREATED)
  await new Promise((resolve) => setTimeout(resolve, 0))

  expect(store.unread).toBe(3)
  expect(store.alerts.map((item) => item.id)).toEqual([3, 1])
  expect(calls.filter((call) => call.key === 'GET /alerts').at(-1)?.query).toMatchObject({
    unread: 'true',
  })

  store.forgetList()
  store.applyEvent({ ...CREATED, type: 'scan_progress' })
  expect(store.unread).toBe(3)
})

test('an alert leads to its stored diff, its scan or its schedules', () => {
  expect(alertRoute(alert(1))).toEqual({ name: 'diff', params: { id: 41 } })
  expect(alertRoute(alert(2, { type: 'scheduled_run_failed', link: { scan_id: 9 } }))).toEqual({
    name: 'scan',
    params: { id: 9 },
  })
  expect(alertRoute(alert(3, { link: { schedule_id: 5 } }))).toEqual({
    name: 'schedules',
    params: { id: 1 },
  })
  expect(alertRoute(alert(4, { project_id: null, link: {} }))).toBeNull()
})
