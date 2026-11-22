import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { FakeEventSource } from '../test-fixtures'
import { type StreamEvent, useEventStream } from './useEventStream'

const PROGRESS = { type: 'scan_progress', scan_id: 7, project_id: 3, state: 'running' }
const HOSTS = { type: 'hosts_changed', scan_id: 7, project_id: 3, hosts: [] }

const stream = useEventStream()
let stops: Array<() => void> = []

function listen(): StreamEvent[] {
  const received: StreamEvent[] = []
  stops.push(stream.on((event) => received.push(event)))
  return received
}

function countReconnects(): { count: number } {
  const counter = { count: 0 }
  stops.push(stream.onReconnect(() => (counter.count += 1)))
  return counter
}

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.useFakeTimers()
})

afterEach(() => {
  stream.close()
  for (const stop of stops) stop()
  stops = []
  vi.useRealTimers()
})

// --- Connection ---

test('connect opens one stream, however often it is called', () => {
  stream.connect()
  stream.connect()

  expect(FakeEventSource.instances).toHaveLength(1)
  expect(FakeEventSource.latest().url).toBe('/api/v1/events')
})

test('connected follows the state of the stream', () => {
  stream.connect()
  expect(stream.connected.value).toBe(false)

  FakeEventSource.latest().open()
  expect(stream.connected.value).toBe(true)

  FakeEventSource.latest().drop()
  expect(stream.connected.value).toBe(false)
})

test('close ends the stream and nothing is tried again', async () => {
  stream.connect()
  const source = FakeEventSource.latest()
  source.open()

  stream.close()
  await vi.advanceTimersByTimeAsync(60_000)

  expect(source.readyState).toBe(FakeEventSource.CLOSED)
  expect(stream.connected.value).toBe(false)
  expect(FakeEventSource.instances).toHaveLength(1)
})

// --- Dispatch ---

test('events are handed to every listener', () => {
  const first = listen()
  const second = listen()
  stream.connect()
  FakeEventSource.latest().open()

  FakeEventSource.latest().send('scan_progress', PROGRESS)
  FakeEventSource.latest().send('hosts_changed', HOSTS)

  expect(first).toEqual([PROGRESS, HOSTS])
  expect(second).toEqual([PROGRESS, HOSTS])
})

test('a listener that was removed gets nothing more', () => {
  const received: StreamEvent[] = []
  const stop = stream.on((event) => received.push(event))
  stream.connect()
  FakeEventSource.latest().send('scan_progress', PROGRESS)

  stop()
  FakeEventSource.latest().send('scan_progress', PROGRESS)

  expect(received).toHaveLength(1)
})

test('an event that cannot be read is ignored', () => {
  const received = listen()
  stream.connect()

  FakeEventSource.latest().send('scan_progress', 'not json')
  FakeEventSource.latest().send('scan_progress', PROGRESS)

  expect(received).toEqual([PROGRESS])
})

// --- Reconnect: events are not replayed, so views must load again ---

test('the first connection is not a reconnect', () => {
  const reconnects = countReconnects()
  stream.connect()

  FakeEventSource.latest().open()

  expect(reconnects.count).toBe(0)
})

test('when the browser re-establishes the stream, the reconnect is announced', () => {
  const reconnects = countReconnects()
  stream.connect()
  const source = FakeEventSource.latest()
  source.open()

  source.drop() // the browser retries by itself
  expect(FakeEventSource.instances).toHaveLength(1)
  source.open()

  expect(reconnects.count).toBe(1)
  expect(stream.connected.value).toBe(true)
})

test('when the browser gives up, a new stream is opened after a pause', async () => {
  const reconnects = countReconnects()
  const received = listen()
  stream.connect()
  FakeEventSource.latest().open()

  FakeEventSource.latest().drop(true) // the server answered with an error
  await vi.advanceTimersByTimeAsync(4000)
  expect(FakeEventSource.instances).toHaveLength(1)
  await vi.advanceTimersByTimeAsync(1500)
  expect(FakeEventSource.instances).toHaveLength(2)

  // The new stream works like the first, and its opening is a reconnect.
  FakeEventSource.latest().open()
  FakeEventSource.latest().send('hosts_changed', HOSTS)
  expect(reconnects.count).toBe(1)
  expect(received).toEqual([HOSTS])
})

test('a stream that never opened is tried again without announcing a reconnect', async () => {
  const reconnects = countReconnects()
  stream.connect()

  FakeEventSource.latest().drop(true)
  await vi.advanceTimersByTimeAsync(6000)
  FakeEventSource.latest().open()

  expect(FakeEventSource.instances).toHaveLength(2)
  expect(reconnects.count).toBe(0)
})
