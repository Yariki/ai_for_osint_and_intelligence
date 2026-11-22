import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, test } from 'vitest'

import { api, ApiError, setSessionLostHandler } from '../api/client'
import { errorReply, NOT_AUTHENTICATED, stubApi } from '../test-setup'
import { useSessionStore } from './session'

const SESSION = { body: { username: 'analyst', expires_at: '2026-11-15T12:00:00Z' } }
const SETUP_DONE = { body: { setup_required: false } }

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  setSessionLostHandler(null)
})

// --- What the server says at start ---

test('load: an existing session makes the store authenticated', async () => {
  stubApi({ 'GET /setup': SETUP_DONE, 'GET /session': SESSION })
  const session = useSessionStore()
  expect(session.loaded).toBe(false)

  await session.load()

  expect(session.loaded).toBe(true)
  expect(session.setupRequired).toBe(false)
  expect(session.isAuthenticated).toBe(true)
  expect(session.username).toBe('analyst')
})

test('load: without a session the store is not authenticated', async () => {
  stubApi({ 'GET /setup': SETUP_DONE, 'GET /session': NOT_AUTHENTICATED })
  const session = useSessionStore()

  await session.load()

  expect(session.loaded).toBe(true)
  expect(session.isAuthenticated).toBe(false)
})

test('load: while no account exists, setup is required and the session is not asked for', async () => {
  const calls = stubApi({ 'GET /setup': { body: { setup_required: true } } })
  const session = useSessionStore()

  await session.load()

  expect(session.setupRequired).toBe(true)
  expect(session.isAuthenticated).toBe(false)
  expect(calls.map((call) => call.key)).toEqual(['GET /setup'])
})

test('load: a server failure is not mistaken for "logged out"', async () => {
  stubApi({ 'GET /setup': SETUP_DONE, 'GET /session': errorReply(500, 'internal_error') })
  const session = useSessionStore()

  await expect(session.load()).rejects.toMatchObject({ code: 'internal_error' })
  expect(session.loaded).toBe(false)
})

// --- Setup, login, logout ---

test('setup sends the token and clears the setup state', async () => {
  const calls = stubApi({ 'POST /setup': { status: 201, body: { setup_required: false } } })
  const session = useSessionStore()
  session.setupRequired = true

  await session.setup('one-time-token', 'analyst', 'a long enough password')

  expect(calls[0]?.body).toEqual({
    token: 'one-time-token',
    username: 'analyst',
    password: 'a long enough password',
  })
  expect(session.setupRequired).toBe(false)
  // Setup does not log in.
  expect(session.isAuthenticated).toBe(false)
})

test('login makes the store authenticated', async () => {
  const calls = stubApi({ 'POST /login': SESSION })
  const session = useSessionStore()

  await session.login('analyst', 'the password')

  expect(calls[0]?.body).toEqual({ username: 'analyst', password: 'the password' })
  expect(session.isAuthenticated).toBe(true)
  expect(session.username).toBe('analyst')
})

test('a failed login leaves the store unauthenticated and reports the error', async () => {
  stubApi({ 'POST /login': errorReply(401, 'invalid_credentials', 'Wrong.') })
  const session = useSessionStore()

  const error = await session.login('analyst', 'wrong').catch((caught: unknown) => caught)

  expect(error).toBeInstanceOf(ApiError)
  expect(error).toMatchObject({ status: 401, code: 'invalid_credentials', message: 'Wrong.' })
  expect(session.isAuthenticated).toBe(false)
})

test('a refused login says how long to wait', async () => {
  stubApi({
    'POST /login': { ...errorReply(429, 'too_many_attempts'), headers: { 'Retry-After': '120' } },
  })
  const session = useSessionStore()

  await expect(session.login('analyst', 'any')).rejects.toMatchObject({
    status: 429,
    retryAfter: 120,
  })
})

test('logout makes the store unauthenticated', async () => {
  stubApi({ 'POST /login': SESSION, 'POST /logout': { status: 204 } })
  const session = useSessionStore()
  await session.login('analyst', 'the password')

  await session.logout()

  expect(session.isAuthenticated).toBe(false)
  expect(session.username).toBeNull()
})

// --- Expiry ---

test('expiry: a 401 from any call ends the session in the store', async () => {
  stubApi({ 'POST /login': SESSION, 'GET /projects': NOT_AUTHENTICATED })
  const session = useSessionStore()
  setSessionLostHandler(() => session.expire())
  await session.login('analyst', 'the password')

  await expect(api.get('/projects')).rejects.toMatchObject({ status: 401 })

  expect(session.isAuthenticated).toBe(false)
})

test('wrong credentials at login are not treated as a lost session', async () => {
  let lost = 0
  setSessionLostHandler(() => (lost += 1))
  stubApi({ 'POST /login': errorReply(401, 'invalid_credentials') })

  await expect(useSessionStore().login('analyst', 'wrong')).rejects.toBeInstanceOf(ApiError)

  expect(lost).toBe(0)
})

// --- Password change ---

test('password change sends both passwords and maps a field error', async () => {
  const calls = stubApi({
    'POST /password': errorReply(422, 'validation_error', 'The request is not valid.', [
      { field: 'new_password', code: 'string_too_short', message: 'Too short.' },
    ]),
  })
  const session = useSessionStore()

  const error = await session.changePassword('old one', 'short').catch((caught: unknown) => caught)

  expect(calls[0]?.body).toEqual({ current_password: 'old one', new_password: 'short' })
  expect(error).toBeInstanceOf(ApiError)
  expect((error as ApiError).fieldMessage('new_password')).toBe('Too short.')
  expect((error as ApiError).fieldMessage('current_password')).toBeUndefined()
})
