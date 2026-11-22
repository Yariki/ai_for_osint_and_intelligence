import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { RouteLocationRaw } from 'vue-router'

import { api, type Schemas } from '../api/client'
import type { StreamEvent } from '../composables/useEventStream'

export type Alert = Schemas['AlertOut']

/** Where an alert leads: its stored diff, its scan, or its schedule. */
export function alertRoute(alert: Alert): RouteLocationRaw | null {
  const link = alert.link as Record<string, number | undefined>
  if (link.diff_id !== undefined) return { name: 'diff', params: { id: link.diff_id } }
  if (link.scan_id !== undefined) return { name: 'scan', params: { id: link.scan_id } }
  if (link.schedule_id !== undefined && alert.project_id !== null) {
    return { name: 'schedules', params: { id: alert.project_id } }
  }
  return null
}

/**
 * Alerts and the unread count of the indicator on every page (FR-HIS-8).
 *
 * The count is the server's. A new alert arrives as an event and raises it
 * at once; after a lost connection the count is loaded again.
 */
export const useAlertsStore = defineStore('alerts', () => {
  const unread = ref(0)
  /** The alerts the list shows, newest first. */
  const alerts = ref<Alert[]>([])
  const total = ref(0)
  /** Whether a list is on screen, and which one: null when none is. */
  let listedUnreadOnly: boolean | null = null

  async function fetchUnread(): Promise<number> {
    unread.value = (await api.get<Schemas['UnreadOut']>('/alerts/unread-count')).unread
    return unread.value
  }

  async function fetchAlerts(unreadOnly = false): Promise<void> {
    const page = await api.get<Schemas['Page_AlertOut_']>('/alerts', {
      unread: unreadOnly || undefined,
      limit: 200,
    })
    alerts.value = page.items
    total.value = page.total
    listedUnreadOnly = unreadOnly
  }

  async function markRead(id: number): Promise<void> {
    const known = alerts.value.find((alert) => alert.id === id)
    if (known?.read_at) return
    const read = await api.post<Alert>(`/alerts/${id}/read`)
    const index = alerts.value.findIndex((alert) => alert.id === id)
    if (index !== -1) alerts.value[index] = read
    await fetchUnread()
  }

  async function markAllRead(): Promise<void> {
    await api.post('/alerts/read-all')
    const now = new Date().toISOString()
    alerts.value = alerts.value.map((alert) => ({ ...alert, read_at: alert.read_at ?? now }))
    await fetchUnread()
  }

  /** A live event: a new alert counts at once, without a reload. A list on
   *  screen is loaded again, so that the new alert comes with its link. */
  function applyEvent(event: StreamEvent): void {
    if (event.type !== 'alert_created') return
    unread.value += 1
    if (listedUnreadOnly !== null) void fetchAlerts(listedUnreadOnly).catch(() => undefined)
  }

  /** Stop following the list, when the list view closes. */
  function forgetList(): void {
    listedUnreadOnly = null
  }

  return {
    unread,
    alerts,
    total,
    fetchUnread,
    fetchAlerts,
    markRead,
    markAllRead,
    applyEvent,
    forgetList,
  }
})
