// Runs before every Vitest file (see vite.config.ts). Also holds the helper
// that specs use to stand in for the API.

import { afterEach, vi } from 'vitest'

// jsdom has no ResizeObserver, and Vuetify's layout components need one.
// Tests do not measure layout, so an observer that never fires is enough.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

globalThis.ResizeObserver = ResizeObserverStub

// jsdom has no visual viewport either; Vuetify's dialogs position themselves by it.
Object.defineProperty(globalThis, 'visualViewport', {
  configurable: true,
  value: Object.assign(new EventTarget(), {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    pageLeft: 0,
    pageTop: 0,
    scale: 1,
  }),
})

afterEach(() => {
  vi.unstubAllGlobals()
})

export interface Reply {
  status?: number
  body?: unknown
  headers?: Record<string, string>
}

export interface Call {
  /** "METHOD /path", the path without the /api/v1 prefix and without the query. */
  key: string
  query: Record<string, string>
  body: unknown
}

/**
 * Replace `fetch` with canned API replies, keyed by "METHOD /path".
 * A reply can be a function of the request body. Returns the list of calls
 * made, which grows as the test runs. A call with no reply answers 500.
 */
export function stubApi(replies: Record<string, Reply | ((body: unknown) => Reply)>): Call[] {
  const calls: Call[] = []
  vi.stubGlobal('fetch', async (input: string, init?: RequestInit): Promise<Response> => {
    const target = new URL(input, 'https://ndm.test')
    const key = `${init?.method ?? 'GET'} ${target.pathname.replace('/api/v1', '')}`
    const body: unknown = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ key, query: Object.fromEntries(target.searchParams), body })

    const found = replies[key]
    const reply: Reply =
      found === undefined
        ? errorReply(500, 'no_reply_stubbed', `the test has no reply for ${key}`)
        : typeof found === 'function'
          ? found(body)
          : found
    const status = reply.status ?? 200
    return new Response(status === 204 ? null : JSON.stringify(reply.body ?? {}), {
      status,
      headers: reply.headers,
    })
  })
  return calls
}

/** A reply in the API's one error shape. */
export function errorReply(
  status: number,
  code: string,
  message = 'An error.',
  details: Array<{ field: string | null; code: string; message: string }> = [],
): Reply {
  return { status, body: { error: { code, message, details } } }
}

export const NOT_AUTHENTICATED = errorReply(401, 'not_authenticated', 'Authentication is required.')
