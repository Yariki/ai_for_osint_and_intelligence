import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, test } from 'vitest'

import { ApiError } from '../api/client'
import { errorReply, stubApi } from '../test-setup'
import { type Project, useProjectsStore } from './projects'

function project(id: number, changes: Partial<Project> = {}): Project {
  return {
    id,
    name: `Project ${id}`,
    description: '',
    state: 'active',
    created_at: '2026-11-15T10:00:00Z',
    ...changes,
  }
}

const SCOPE = {
  attestation_id: 7,
  attested_at: '2026-11-15T10:05:00Z',
  attested_by: 'analyst',
  entries: [{ kind: 'cidr', text: '8.8.8.0/24', start: '8.8.8.0', end: '8.8.8.255' }],
}

beforeEach(() => {
  setActivePinia(createPinia())
})

test('fetchAll loads the projects', async () => {
  const calls = stubApi({
    'GET /projects': { body: { items: [project(1), project(2)], total: 2, limit: 200, offset: 0 } },
  })
  const store = useProjectsStore()

  await store.fetchAll()

  expect(store.projects.map((item) => item.id)).toEqual([1, 2])
  expect(calls[0]?.query).toEqual({ limit: '200' })
})

test('create adds the new project to the list', async () => {
  const calls = stubApi({ 'POST /projects': { status: 201, body: project(3, { name: 'Edge' }) } })
  const store = useProjectsStore()

  const created = await store.create('Edge', 'Quarterly review')

  expect(calls[0]?.body).toEqual({ name: 'Edge', description: 'Quarterly review' })
  expect(created.id).toBe(3)
  expect(store.projects).toEqual([project(3, { name: 'Edge' })])
})

test('update and archive replace the project in the list', async () => {
  const calls = stubApi({
    'GET /projects': { body: { items: [project(1), project(2)], total: 2, limit: 200, offset: 0 } },
    'PATCH /projects/2': { body: project(2, { name: 'Core', description: 'Backbone' }) },
    'POST /projects/2/archive': { body: project(2, { name: 'Core', state: 'archived' }) },
  })
  const store = useProjectsStore()
  await store.fetchAll()

  await store.update(2, 'Core', 'Backbone')
  expect(calls[1]?.body).toEqual({ name: 'Core', description: 'Backbone' })
  expect(store.projects.map((item) => item.name)).toEqual(['Project 1', 'Core'])

  await store.archive(2)
  expect(store.projects.map((item) => item.state)).toEqual(['active', 'archived'])
})

test('remove sends the explicit confirmation and drops the project', async () => {
  const calls = stubApi({
    'GET /projects': { body: { items: [project(1), project(2)], total: 2, limit: 200, offset: 0 } },
    'DELETE /projects/1': { status: 204 },
  })
  const store = useProjectsStore()
  await store.fetchAll()

  await store.remove(1)

  expect(calls[1]).toMatchObject({ key: 'DELETE /projects/1', query: { confirm: 'true' } })
  expect(store.projects.map((item) => item.id)).toEqual([2])
})

test('a rejected change leaves the list as it was', async () => {
  stubApi({
    'GET /projects': { body: { items: [project(1)], total: 1, limit: 200, offset: 0 } },
    'POST /projects/1/archive': errorReply(409, 'project_archived', 'Read-only.'),
  })
  const store = useProjectsStore()
  await store.fetchAll()

  await expect(store.archive(1)).rejects.toMatchObject({ code: 'project_archived' })

  expect(store.projects).toEqual([project(1)])
})

test('saveScope sends the entries as typed, with the attestation', async () => {
  const calls = stubApi({ 'PUT /projects/1/scope': { body: SCOPE } })
  const store = useProjectsStore()

  const scope = await store.saveScope(1, [' 8.8.8.0/24', '08.8.8.8'], true)

  // No trimming and no rewriting: the server decides what is valid.
  expect(calls[0]?.body).toEqual({ entries: [' 8.8.8.0/24', '08.8.8.8'], authorized: true })
  expect(scope.attestation_id).toBe(7)
})

test('saveScope sends nothing without the attestation', async () => {
  const calls = stubApi({ 'PUT /projects/1/scope': { body: SCOPE } })
  const store = useProjectsStore()

  await expect(store.saveScope(1, ['8.8.8.0/24'], false)).rejects.toThrow(/attestation/)

  expect(calls).toEqual([])
})

test('a rejected scope carries the rejection of each entry', async () => {
  stubApi({
    'PUT /projects/1/scope': errorReply(422, 'scope_rejected', 'The scope was not saved.', [
      { field: 'entries.1', code: 'non_public', message: 'It overlaps 192.168.0.0/16.' },
    ]),
  })
  const store = useProjectsStore()

  const error = await store
    .saveScope(1, ['8.8.8.0/24', '192.168.1.0/24'], true)
    .catch((caught: unknown) => caught)

  expect(error).toBeInstanceOf(ApiError)
  expect((error as ApiError).fieldMessage('entries.1')).toBe('It overlaps 192.168.0.0/16.')
})

test('scope and history are read from the project', async () => {
  stubApi({
    'GET /projects/1/scope': { body: SCOPE },
    'GET /projects/1/scope/history': {
      body: { items: [SCOPE, { ...SCOPE, attestation_id: 6 }], total: 2, limit: 200, offset: 0 },
    },
  })
  const store = useProjectsStore()

  expect((await store.fetchScope(1)).entries[0]?.text).toBe('8.8.8.0/24')
  expect((await store.fetchScopeHistory(1)).map((item) => item.attestation_id)).toEqual([7, 6])
})
