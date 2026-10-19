export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body: { error?: { message?: string } } = await response.json()
      message = body.error?.message || message
    } catch {
      /* A reverse proxy may return a non-JSON error. */
    }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export const dateTime = (value: string | null): string =>
  value
    ? new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
    : '—'

export function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : 'The request could not be completed'
}
