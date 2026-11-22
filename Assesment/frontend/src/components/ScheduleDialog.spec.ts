import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, expect, test } from 'vitest'

import vuetify from '../plugins/vuetify'
import { profile, schedule } from '../test-fixtures'
import { type Call, errorReply, type Reply, stubApi } from '../test-setup'
import ScheduleDialog from './ScheduleDialog.vue'

let wrapper: VueWrapper

afterEach(() => {
  wrapper.unmount()
  document.body.innerHTML = ''
})

async function openDialog(
  replies: Record<string, Reply> = {},
  existing: ReturnType<typeof schedule> | null = null,
): Promise<Call[]> {
  const calls = stubApi({ 'GET /scan-profiles': { body: [profile(7, 'Web edge')] }, ...replies })
  wrapper = mount(ScheduleDialog, {
    props: { projectId: 1, modelValue: false, schedule: existing },
    global: { plugins: [createPinia(), vuetify] },
    attachTo: document.body,
  })
  await wrapper.setProps({ modelValue: true })
  await flushPromises()
  return calls
}

function inPage(selector: string): HTMLElement {
  const element = document.body.querySelector<HTMLElement>(selector)
  if (!element) throw new Error(`nothing matches ${selector}`)
  return element
}

async function type(selector: string, text: string): Promise<void> {
  const input = inPage(selector) as HTMLInputElement
  input.value = text
  input.dispatchEvent(new Event('input'))
  await flushPromises()
}

async function save(): Promise<void> {
  inPage('[data-test="schedule-save"]').click()
  await flushPromises()
}

function sent(calls: Call[], key: string): Record<string, unknown> {
  const call = calls.find((candidate) => candidate.key === key)
  if (!call) throw new Error(`${key} was not called`)
  return call.body as Record<string, unknown>
}

async function fillIn(): Promise<void> {
  await type('[data-test="schedule-name"] input', 'Nightly')
  await type('[data-test="schedule-targets"] textarea', '8.8.8.0/24\n8.8.9.1')
}

test('a new schedule is sent with its targets, interval and default parameters', async () => {
  const calls = await openDialog({ 'POST /projects/1/schedules': { status: 201, body: schedule(1) } })
  await fillIn()
  await type('[data-test="schedule-hours"] input', '6')

  await save()

  expect(sent(calls, 'POST /projects/1/schedules')).toMatchObject({
    name: 'Nightly',
    targets: ['8.8.8.0/24', '8.8.9.1'],
    interval_seconds: 6 * 3600,
    cron: null,
    enabled: true,
    parameters: { port_profile: 'top-1000', technique: 'automatic', pps: 1000 },
  })
  expect(wrapper.emitted('saved')?.[0]?.[0]).toMatchObject({ id: 1 })
})

test('choosing a profile fills the schedule with its parameters', async () => {
  const calls = await openDialog({ 'POST /projects/1/schedules': { status: 201, body: schedule(1) } })
  await fillIn()

  wrapper.findComponent({ name: 'VSelect' }).vm.$emit('update:modelValue', 7)
  await flushPromises()
  await save()

  expect(sent(calls, 'POST /projects/1/schedules').parameters).toEqual(profile(7).parameters)
})

test('an interval below the minimum is shown on the interval field', async () => {
  const message = 'Runs would be 30 minutes apart; the minimum is 1 hour.'
  await openDialog({
    'POST /projects/1/schedules': errorReply(422, 'validation_error', 'Not valid.', [
      { field: 'interval_seconds', code: 'below_minimum_interval', message },
    ]),
  })
  await fillIn()
  await type('[data-test="schedule-hours"] input', '0.5')

  await save()

  expect(inPage('[data-test="schedule-hours"]').textContent).toContain(message)
  expect(wrapper.emitted('saved')).toBeUndefined()
  expect(document.body.querySelector('[data-test="schedule-error"]')).toBeNull()
})

test('a cron expression is sent with its time zone, and its rejection is shown there', async () => {
  const message = 'Runs would be 30 minutes apart; the minimum is 1 hour.'
  const calls = await openDialog({
    'POST /projects/1/schedules': errorReply(422, 'validation_error', 'Not valid.', [
      { field: 'cron', code: 'below_minimum_interval', message },
    ]),
  })
  await fillIn()
  inPage('[data-test="schedule-kind"] input[value="cron"]').click()
  await flushPromises()
  await type('[data-test="schedule-cron"] input', '*/30 * * * *')
  await type('[data-test="schedule-zone"] input', 'Europe/Kyiv')

  await save()

  expect(sent(calls, 'POST /projects/1/schedules')).toMatchObject({
    interval_seconds: null,
    cron: '*/30 * * * *',
    time_zone: 'Europe/Kyiv',
  })
  expect(inPage('[data-test="schedule-cron"]').textContent).toContain(message)
})

test('a target outside the scope is shown on the targets', async () => {
  await openDialog({
    'POST /projects/1/schedules': errorReply(422, 'targets_rejected', 'Not saved.', [
      { field: 'targets.1', code: 'out_of_scope', message: "'8.8.9.1' cannot be scheduled." },
    ]),
  })
  await fillIn()

  await save()

  expect(inPage('[data-test="schedule-targets"]').textContent).toContain(
    "8.8.9.1: '8.8.9.1' cannot be scheduled.",
  )
})

test('editing fills the form from the schedule and saves with PUT', async () => {
  const existing = schedule(4, {
    name: 'Weekly',
    cron: '0 3 * * 1',
    interval_seconds: null,
    time_zone: 'Europe/Kyiv',
    flag_reason: 'targets_out_of_scope',
  })
  const calls = await openDialog({ 'PUT /schedules/4': { body: existing } }, existing)

  expect((inPage('[data-test="schedule-name"] input') as HTMLInputElement).value).toBe('Weekly')
  expect((inPage('[data-test="schedule-cron"] input') as HTMLInputElement).value).toBe('0 3 * * 1')
  expect(document.body.textContent).toContain('The scheduler stopped this schedule')

  await save()

  expect(sent(calls, 'PUT /schedules/4')).toMatchObject({
    name: 'Weekly',
    cron: '0 3 * * 1',
    time_zone: 'Europe/Kyiv',
    targets: ['8.8.8.0/24'],
    parameters: profile(0).parameters,
  })
})
