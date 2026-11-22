import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, test } from 'vitest'

import { ApiError } from '../api/client'
import { profile, schedule } from '../test-fixtures'
import { errorReply, stubApi } from '../test-setup'
import { type ScheduleIn, useSchedulesStore } from './schedules'

const BODY: ScheduleIn = {
  name: 'Nightly',
  targets: ['8.8.8.0/24'],
  parameters: profile(0).parameters,
  interval_seconds: 86400,
  cron: null,
  time_zone: 'UTC',
  enabled: true,
}

function page<T>(items: T[]) {
  return { body: { items, total: items.length, limit: 200, offset: 0 } }
}

beforeEach(() => {
  setActivePinia(createPinia())
})

test('the schedules of a project are loaded with their next run and last outcome', async () => {
  const ran = schedule(2, {
    last_run: {
      planned_at: '2026-11-17T02:00:00Z',
      outcome: 'started',
      reason: null,
      scan_id: 9,
      scan_state: 'completed',
      created_at: '2026-11-17T02:00:05Z',
    },
  })
  const calls = stubApi({ 'GET /projects/1/schedules': page([schedule(1), ran]) })
  const store = useSchedulesStore()

  await store.fetchSchedules(1)

  expect(store.schedules.map((item) => item.next_run_at)).toEqual([
    '2026-11-18T02:00:00Z',
    '2026-11-18T02:00:00Z',
  ])
  expect(store.schedules[1]?.last_run?.scan_state).toBe('completed')
  expect(calls[0]?.query).toEqual({ limit: '200' })
})

test('a created schedule is added, and disabling and enabling replace it', async () => {
  const calls = stubApi({
    'POST /projects/1/schedules': { status: 201, body: schedule(3) },
    'POST /schedules/3/disable': { body: schedule(3, { enabled: false }) },
    'POST /schedules/3/enable': { body: schedule(3, { next_run_at: '2026-11-19T02:00:00Z' }) },
  })
  const store = useSchedulesStore()

  await store.createSchedule(1, BODY)
  expect(calls[0]?.body).toEqual(BODY)
  expect(store.schedules.map((item) => item.id)).toEqual([3])

  await store.setEnabled(3, false)
  expect(store.schedules[0]?.enabled).toBe(false)

  await store.setEnabled(3, true)
  expect(store.schedules).toHaveLength(1)
  expect(store.schedules[0]).toMatchObject({ enabled: true, next_run_at: '2026-11-19T02:00:00Z' })
})

test('an edited schedule replaces the one in the list; a deleted one goes', async () => {
  stubApi({
    'GET /projects/1/schedules': page([schedule(1), schedule(2)]),
    'PUT /schedules/2': { body: schedule(2, { name: 'Renamed' }) },
    'DELETE /schedules/1': { status: 204 },
  })
  const store = useSchedulesStore()
  await store.fetchSchedules(1)

  await store.updateSchedule(2, { ...BODY, name: 'Renamed' })
  await store.deleteSchedule(1)

  expect(store.schedules.map((item) => item.name)).toEqual(['Renamed'])
})

test('a rejection keeps the field it concerns', async () => {
  stubApi({
    'POST /projects/1/schedules': errorReply(422, 'validation_error', 'Not valid.', [
      {
        field: 'interval_seconds',
        code: 'below_minimum_interval',
        message: 'Runs would be 30 minutes apart; the minimum is 1 hour.',
      },
    ]),
  })
  const store = useSchedulesStore()

  const caught = await store.createSchedule(1, BODY).catch((error: unknown) => error)

  expect(caught).toBeInstanceOf(ApiError)
  expect((caught as ApiError).fieldMessage('interval_seconds')).toContain('minimum is 1 hour')
  expect(store.schedules).toEqual([])
})

test('profiles are created and changed, then listed again from the server', async () => {
  const calls = stubApi({
    'POST /scan-profiles': { status: 201, body: profile(4, 'Web') },
    'PUT /scan-profiles/4': { body: profile(4, 'Web, fast') },
    'GET /scan-profiles': { body: [profile(4, 'Web')] },
    'DELETE /scan-profiles/4': { status: 204 },
  })
  const store = useSchedulesStore()

  await store.createProfile({ name: 'Web', parameters: profile(0).parameters })
  expect(store.profiles.map((item) => item.name)).toEqual(['Web'])
  await store.updateProfile(4, { name: 'Web, fast', parameters: profile(0).parameters })
  await store.deleteProfile(4)

  expect(calls.map((call) => call.key)).toEqual([
    'POST /scan-profiles',
    'GET /scan-profiles',
    'PUT /scan-profiles/4',
    'GET /scan-profiles',
    'DELETE /scan-profiles/4',
  ])
  expect(store.profiles).toEqual([])
})
