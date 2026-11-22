// The live event stream: one server-sent event connection for the whole
// application.
//
// The server does not keep events and does not replay them. So after the
// connection was lost and came back, every view that shows live data loads
// its state again through REST: that is what `onReconnect` is for.

import { ref } from 'vue'

export interface StreamEvent {
  type: string
  /** Set on the events of a scan; a traceroute event has a run_id instead. */
  scan_id?: number
  /** Null on an alert about the installation, such as a dead-lettered message. */
  project_id: number | null
  [field: string]: unknown
}

type EventHandler = (event: StreamEvent) => void
type ReconnectHandler = () => void

const URL = '/api/v1/events'
const EVENT_TYPES = ['scan_progress', 'hosts_changed', 'traceroute_hop', 'alert_created']
/** Wait before a new connection is tried after the browser gave one up. */
const RETRY_MS = 5000

const connected = ref(false)
const eventHandlers = new Set<EventHandler>()
const reconnectHandlers = new Set<ReconnectHandler>()

let source: EventSource | null = null
let retryTimer: ReturnType<typeof setTimeout> | undefined
/** True once a connection has been open: the next "open" is a reconnect. */
let wasOpen = false

function dispatch(message: MessageEvent<string>): void {
  let event: StreamEvent
  try {
    event = JSON.parse(message.data) as StreamEvent
  } catch {
    return
  }
  for (const handler of eventHandlers) handler(event)
}

function connect(): void {
  if (source) return
  clearTimeout(retryTimer)
  const opened = new EventSource(URL)
  source = opened

  opened.addEventListener('open', () => {
    connected.value = true
    // Whatever happened while the connection was away was not seen.
    if (wasOpen) for (const handler of reconnectHandlers) handler()
    wasOpen = true
  })
  for (const type of EVENT_TYPES) {
    opened.addEventListener(type, (message) => dispatch(message as MessageEvent<string>))
  }
  opened.addEventListener('error', () => {
    connected.value = false
    // While the browser retries by itself, there is nothing to do. When it
    // has given up (the server answered with an error), try again later.
    if (opened.readyState === EventSource.CLOSED) {
      source = null
      retryTimer = setTimeout(connect, RETRY_MS)
    }
  })
}

function close(): void {
  clearTimeout(retryTimer)
  source?.close()
  source = null
  connected.value = false
  wasOpen = false
}

export function useEventStream() {
  return {
    /** True while the stream is open. */
    connected,
    /** Open the stream. Does nothing if it is open already. */
    connect,
    /** Close the stream, at logout. */
    close,
    /** Be told about every event. Returns the function that ends it. */
    on(handler: EventHandler): () => void {
      eventHandlers.add(handler)
      return () => eventHandlers.delete(handler)
    },
    /** Be told when the stream is back after a loss. Returns the function that ends it. */
    onReconnect(handler: ReconnectHandler): () => void {
      reconnectHandlers.add(handler)
      return () => reconnectHandlers.delete(handler)
    },
  }
}
