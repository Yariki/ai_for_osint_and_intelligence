// The one place that talks to the API.
//
// It sends the session cookie, turns the API's single error shape into an
// ApiError, and reports a lost session to whoever registered for it.

import type { components } from './schema'

export type Schemas = components['schemas']
export type ErrorDetail = Schemas['ErrorDetail']

const BASE = '/api/v1'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: ErrorDetail[]
  /** Seconds to wait before trying again, when the server says so (HTTP 429). */
  readonly retryAfter: number | null

  constructor(
    status: number,
    code: string,
    message: string,
    details: ErrorDetail[] = [],
    retryAfter: number | null = null,
  ) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
    this.retryAfter = retryAfter
  }

  /** The message for one request field, such as "password" or "entries.2". */
  fieldMessage(field: string): string | undefined {
    return this.details.find((detail) => detail.field === field)?.message
  }
}

let onSessionLost: (() => void) | null = null

/** Register what happens when any call finds that there is no valid session. */
export function setSessionLostHandler(handler: (() => void) | null): void {
  onSessionLost = handler
}

type Query = Record<string, string | number | boolean | null | undefined>

function url(path: string, query?: Query): string {
  const search = new URLSearchParams()
  for (const [name, value] of Object.entries(query ?? {})) {
    if (value !== null && value !== undefined && value !== '') search.set(name, String(value))
  }
  const text = search.toString()
  return BASE + path + (text ? `?${text}` : '')
}

async function toError(response: Response): Promise<ApiError> {
  const retryAfter = Number(response.headers.get('Retry-After')) || null
  try {
    const body = (await response.json()) as Schemas['ErrorResponse']
    const { code, message, details } = body.error
    return new ApiError(response.status, code, message, details ?? [], retryAfter)
  } catch {
    // Not the API's error shape: the proxy answered, or the body was cut off.
    return new ApiError(response.status, 'unexpected_response', 'The server could not be reached.')
  }
}

async function request<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  let response: Response
  try {
    response = await fetch(url(path, query), {
      method,
      credentials: 'same-origin',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'network_error', 'The server could not be reached.')
  }

  if (!response.ok) {
    const error = await toError(response)
    if (error.code === 'not_authenticated') onSessionLost?.()
    throw error
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>('GET', path, undefined, query),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body),
  delete: <T>(path: string, query?: Query) => request<T>('DELETE', path, undefined, query),
}
