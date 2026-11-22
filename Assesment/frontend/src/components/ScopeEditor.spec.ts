import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, expect, test } from 'vitest'

import vuetify from '../plugins/vuetify'
import { type Call, errorReply, type Reply, stubApi } from '../test-setup'
import ScopeEditor from './ScopeEditor.vue'

const SAVED: Reply = {
  body: {
    attestation_id: 7,
    attested_at: '2026-11-15T10:05:00Z',
    attested_by: 'analyst',
    entries: [{ kind: 'cidr', text: '8.8.8.0/24', start: '8.8.8.0', end: '8.8.8.255' }],
  },
}

let wrapper: VueWrapper

afterEach(() => {
  wrapper.unmount()
  document.body.innerHTML = ''
})

function mountEditor(entries: string[], reply: Reply, readonly = false): Call[] {
  const calls = stubApi({ 'PUT /projects/1/scope': reply })
  wrapper = mount(ScopeEditor, {
    props: { projectId: 1, entries, readonly },
    global: { plugins: [createPinia(), vuetify] },
    attachTo: document.body,
  })
  return calls
}

/** The attestation dialog is rendered outside the component, in the page body. */
function inPage(selector: string): HTMLElement | null {
  return document.body.querySelector<HTMLElement>(selector)
}

async function click(selector: string): Promise<void> {
  const element = inPage(selector)
  if (!element) throw new Error(`nothing matches ${selector}`)
  element.click()
  await flushPromises()
}

async function tickAttestation(): Promise<void> {
  const box = inPage('[data-test="attest-check"] input') as HTMLInputElement
  box.click()
  await flushPromises()
}

function rowInputs(): HTMLInputElement[] {
  return [...document.body.querySelectorAll<HTMLInputElement>('[data-test="scope-row"] input')]
}

async function type(index: number, text: string): Promise<void> {
  const input = rowInputs()[index]
  if (!input) throw new Error(`no entry row ${index}`)
  input.value = text
  input.dispatchEvent(new Event('input'))
  await flushPromises()
}

// --- Nothing is sent until the attestation is confirmed ---

test('saving opens the attestation and sends nothing by itself', async () => {
  const calls = mountEditor(['8.8.8.0/24'], SAVED)
  expect(inPage('[data-test="attest-confirm"]')).toBeNull()

  await click('[data-test="scope-save"]')

  expect(inPage('[data-test="attest-entries"]')?.textContent).toContain('8.8.8.0/24')
  expect(calls).toEqual([])
})

test('the confirm button does nothing until the box is ticked', async () => {
  const calls = mountEditor(['8.8.8.0/24'], SAVED)
  await click('[data-test="scope-save"]')

  const confirm = inPage('[data-test="attest-confirm"]') as HTMLButtonElement
  expect(confirm.disabled).toBe(true)
  await click('[data-test="attest-confirm"]')

  expect(calls).toEqual([])
})

test('cancelling the attestation sends nothing', async () => {
  const calls = mountEditor(['8.8.8.0/24'], SAVED)
  await click('[data-test="scope-save"]')
  await tickAttestation()

  await click('[data-test="attest-cancel"]')

  expect(calls).toEqual([])
  expect(wrapper.emitted('saved')).toBeUndefined()
})

test('the box is not ticked in advance when the dialog is opened again', async () => {
  const calls = mountEditor(['8.8.8.0/24'], SAVED)
  await click('[data-test="scope-save"]')
  await tickAttestation()
  await click('[data-test="attest-cancel"]')

  await click('[data-test="scope-save"]')

  expect((inPage('[data-test="attest-confirm"]') as HTMLButtonElement).disabled).toBe(true)
  expect(calls).toEqual([])
})

test('after the attestation is confirmed the scope is sent once, as typed', async () => {
  const calls = mountEditor(['8.8.8.0/24'], SAVED)
  await click('[data-test="scope-add"]')
  await type(1, ' 08.8.8.8 ')

  await click('[data-test="scope-save"]')
  await tickAttestation()
  await click('[data-test="attest-confirm"]')

  expect(calls).toHaveLength(1)
  // No validation and no normalization here: spaces and the leading zero go to the server.
  expect(calls[0]?.body).toEqual({ entries: ['8.8.8.0/24', ' 08.8.8.8 '], authorized: true })
  expect(wrapper.emitted('saved')).toHaveLength(1)
})

// --- The server's rejection is shown on the entry concerned ---

test('a rejected private range is shown as an error on its entry', async () => {
  const message = "'192.168.1.0/24' is rejected: it overlaps the non-public range 192.168.0.0/16"
  mountEditor(
    ['8.8.8.0/24', '192.168.1.0/24', '1.1.1.1'],
    errorReply(422, 'scope_rejected', 'The scope was not saved.', [
      { field: 'entries.1', code: 'non_public', message },
    ]),
  )

  await click('[data-test="scope-save"]')
  await tickAttestation()
  await click('[data-test="attest-confirm"]')

  const rows = [...document.body.querySelectorAll('[data-test="scope-row"]')]
  expect(rows[1]?.textContent).toContain(message)
  expect(rows[0]?.textContent).not.toContain('rejected')
  expect(rows[2]?.textContent).not.toContain('rejected')
  // The entries stay as typed, so the analyst can correct the one that was rejected.
  expect(rowInputs().map((input) => input.value)).toEqual([
    '8.8.8.0/24',
    '192.168.1.0/24',
    '1.1.1.1',
  ])
  expect(wrapper.emitted('saved')).toBeUndefined()
})

test('a rejection that concerns no single entry is shown above the entries', async () => {
  mountEditor(
    ['8.8.0.0/15'],
    errorReply(422, 'scope_rejected', 'The scope was not saved.', [
      { field: null, code: 'ceiling_exceeded', message: 'the scope holds 131072 addresses' },
    ]),
  )

  await click('[data-test="scope-save"]')
  await tickAttestation()
  await click('[data-test="attest-confirm"]')

  expect(inPage('[data-test="scope-error"]')?.textContent).toContain('131072 addresses')
})

// --- Archived projects ---

test('a read-only editor offers no way to change or save', async () => {
  const calls = mountEditor(['8.8.8.0/24'], SAVED, true)

  expect(inPage('[data-test="scope-save"]')).toBeNull()
  expect(inPage('[data-test="scope-add"]')).toBeNull()
  expect(inPage('[data-test="scope-remove"]')).toBeNull()
  expect(rowInputs()[0]?.readOnly).toBe(true)
  expect(calls).toEqual([])
})
