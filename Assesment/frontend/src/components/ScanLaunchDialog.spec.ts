import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import vuetify from '../plugins/vuetify'
import { CONFIG, profile, scan } from '../test-fixtures'
import { type Call, errorReply, type Reply, stubApi } from '../test-setup'
import ScanLaunchDialog from './ScanLaunchDialog.vue'

const ESTIMATE: Reply = {
  body: {
    hosts: 256,
    ports: 1000,
    probes: 256000,
    max_probes: 512000,
    seconds: 256,
    max_seconds: 512,
  },
}

let wrapper: VueWrapper

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  wrapper.unmount()
  document.body.innerHTML = ''
  vi.useRealTimers()
})

/** Let the estimate delay pass and the requests settle. */
async function settle(): Promise<void> {
  await vi.advanceTimersByTimeAsync(400)
  await flushPromises()
}

async function openDialog(replies: Parameters<typeof stubApi>[0] = {}): Promise<Call[]> {
  const calls = stubApi({
    'GET /config': { body: CONFIG },
    'POST /scans/estimate': ESTIMATE,
    ...replies,
  })
  wrapper = mount(ScanLaunchDialog, {
    props: { projectId: 1, modelValue: false },
    global: { plugins: [createPinia(), vuetify] },
    attachTo: document.body,
  })
  await wrapper.setProps({ modelValue: true })
  await settle()
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
  await settle()
}

async function choose(group: string, value: string): Promise<void> {
  inPage(`[data-test="${group}"] input[value="${value}"]`).click()
  await settle()
}

function estimates(calls: Call[]): Array<Record<string, unknown>> {
  return calls
    .filter((call) => call.key === 'POST /scans/estimate')
    .map((call) => call.body as Record<string, unknown>)
}

const TARGETS = '[data-test="launch-targets"] textarea'

// --- The estimate comes from the server and follows the parameters ---

test('the estimate is shown once targets are entered', async () => {
  const calls = await openDialog()
  expect(inPage('[data-test="launch-estimate"]').textContent).toContain('Enter targets')
  expect(estimates(calls)).toEqual([])

  await type(TARGETS, '8.8.8.0/24')

  expect(estimates(calls)).toEqual([
    {
      targets: ['8.8.8.0/24'],
      port_profile: 'top-1000',
      custom_ports: null,
      pps: 1000,
      retries: 1,
    },
  ])
  const text = inPage('[data-test="launch-estimate"]').textContent ?? ''
  expect(text).toContain('256 hosts')
  expect(text).toContain('256,000 probes')
  expect(text).toContain('4 min')
  expect(text).toContain('512,000 probes')
})

test('the estimate is refreshed when a parameter changes', async () => {
  const calls = await openDialog()
  await type(TARGETS, '8.8.8.0/24')

  await choose('launch-profile', 'top-100')
  expect(estimates(calls).at(-1)).toMatchObject({ port_profile: 'top-100' })

  await type('input[type="number"]', '2500') // packets per second
  expect(estimates(calls).at(-1)).toMatchObject({ port_profile: 'top-100', pps: 2500 })

  await type(TARGETS, '8.8.8.0/24\n8.8.9.1')
  expect(estimates(calls).at(-1)).toMatchObject({ targets: ['8.8.8.0/24', '8.8.9.1'] })

  expect(estimates(calls)).toHaveLength(4)
})

test('several quick changes ask for one estimate', async () => {
  const calls = await openDialog()
  const input = inPage(TARGETS) as HTMLTextAreaElement

  for (const text of ['8', '8.8', '8.8.8.8']) {
    input.value = text
    input.dispatchEvent(new Event('input'))
    await vi.advanceTimersByTimeAsync(50)
  }
  await settle()

  expect(estimates(calls).map((body) => body.targets)).toEqual([['8.8.8.8']])
})

test('a rejected estimate shows the reason', async () => {
  await openDialog({
    'POST /scans/estimate': errorReply(422, 'too_many_targets', 'A scan probes at most 256.'),
  })

  await type(TARGETS, '8.8.8.0/23')

  expect(inPage('[data-test="launch-estimate"]').textContent).toContain('at most 256')
})

// --- Scanner capabilities ---

test('the limits and the scanner capabilities are shown', async () => {
  await openDialog()

  const text = document.body.textContent ?? ''
  expect(inPage('[data-test="launch-scanner"]').textContent).toContain('SYN available')
  expect(text).toContain('1 to 5000')
  expect(text).toContain('100 to 10000')
  expect(text).toContain('top-1000 (1,000)')
})

test('SYN cannot be forced when the capability is absent', async () => {
  const withoutSyn = { ...CONFIG, scanner: { ...CONFIG.scanner, syn_capable: false } }
  const calls = await openDialog({
    'GET /config': { body: withoutSyn },
    'POST /projects/1/scans': { status: 201, body: scan(5) },
  })

  expect(inPage('[data-test="launch-scanner"]').textContent).toContain('SYN not available')
  const syn = inPage('[data-test="launch-technique"] input[value="syn"]') as HTMLInputElement
  expect(syn.disabled).toBe(true)

  // Clicking it changes nothing: the scan is launched with the automatic technique.
  syn.click()
  await type(TARGETS, '8.8.8.8')
  inPage('[data-test="launch-start"]').click()
  await settle()

  const launch = calls.find((call) => call.key === 'POST /projects/1/scans')
  expect(launch?.body).toMatchObject({ technique: 'automatic' })
})

test('SYN can be chosen when the scanner has the capability', async () => {
  const calls = await openDialog({ 'POST /projects/1/scans': { status: 201, body: scan(5) } })
  const syn = inPage('[data-test="launch-technique"] input[value="syn"]') as HTMLInputElement
  expect(syn.disabled).toBe(false)

  await choose('launch-technique', 'syn')
  await type(TARGETS, '8.8.8.8')
  inPage('[data-test="launch-start"]').click()
  await settle()

  expect(calls.find((call) => call.key === 'POST /projects/1/scans')?.body).toMatchObject({
    technique: 'syn',
  })
})

test('without a scanner report the dialog says so and SYN is not offered', async () => {
  await openDialog({ 'GET /config': { body: { ...CONFIG, scanner: null } } })

  expect(inPage('[data-test="launch-scanner"]').textContent).toContain('No scanner has reported')
  const syn = inPage('[data-test="launch-technique"] input[value="syn"]') as HTMLInputElement
  expect(syn.disabled).toBe(true)
})

// --- Launch ---

test('the scan is launched with the targets as typed and the form values', async () => {
  const calls = await openDialog({ 'POST /projects/1/scans': { status: 201, body: scan(5) } })
  await type(TARGETS, '8.8.8.0/30\n\n 8.8.8.9')

  inPage('[data-test="launch-start"]').click()
  await settle()

  // Blank lines are not targets; nothing else is changed. The server validates.
  expect(calls.find((call) => call.key === 'POST /projects/1/scans')?.body).toEqual({
    targets: ['8.8.8.0/30', ' 8.8.8.9'],
    discovery: { icmp_echo: true, tcp_ports: [80, 443] },
    scan_all_targets: false,
    port_profile: 'top-1000',
    custom_ports: null,
    technique: 'automatic',
    pps: 1000,
    timeout_ms: 1500,
    retries: 1,
    hosts_in_parallel: 64,
  })
  expect(wrapper.emitted('launched')).toHaveLength(1)
  expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([false])
})

test('a rejected target is shown as an error', async () => {
  const message = "'9.9.9.9' is rejected: it is not inside the project scope"
  await openDialog({
    'POST /projects/1/scans': errorReply(422, 'targets_rejected', 'The scan was not created.', [
      { field: 'targets.1', code: 'out_of_scope', message },
    ]),
  })
  await type(TARGETS, '8.8.8.8\n9.9.9.9')

  inPage('[data-test="launch-start"]').click()
  await settle()

  const field = inPage('[data-test="launch-targets"]')
  expect(field.textContent).toContain(`9.9.9.9: ${message}`)
  // The dialog stays open with what was typed, so the target can be corrected.
  expect(wrapper.emitted('launched')).toBeUndefined()
  expect((inPage(TARGETS) as HTMLTextAreaElement).value).toBe('8.8.8.8\n9.9.9.9')
})

test('a rejection that concerns no target is shown at the top', async () => {
  await openDialog({
    'POST /projects/1/scans': errorReply(409, 'syn_unavailable', 'The scanner has no SYN capability.'),
  })
  await type(TARGETS, '8.8.8.8')

  inPage('[data-test="launch-start"]').click()
  await settle()

  expect(inPage('[data-test="launch-error"]').textContent).toContain('no SYN capability')
})

test('nothing can be launched without targets', async () => {
  await openDialog()

  expect((inPage('[data-test="launch-start"]') as HTMLButtonElement).disabled).toBe(true)
})

// --- Scan profiles ---

test('choosing a profile fills the dialog with its parameters', async () => {
  const chosen = profile(7, 'Web edge')
  const calls = await openDialog({
    'GET /scan-profiles': { body: [chosen] },
    'POST /projects/1/scans': { status: 201, body: scan(5) },
  })
  await type(TARGETS, '8.8.8.8')

  wrapper.findComponent({ name: 'VSelect' }).vm.$emit('update:modelValue', 7)
  await settle()
  inPage('[data-test="launch-start"]').click()
  await settle()

  const launch = calls.find((call) => call.key === 'POST /projects/1/scans')
  expect(launch?.body).toEqual({ targets: ['8.8.8.8'], ...chosen.parameters })
})
