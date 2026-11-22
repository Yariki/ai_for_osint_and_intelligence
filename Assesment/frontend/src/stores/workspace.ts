import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api, type Schemas } from '../api/client'
import type { StreamEvent } from '../composables/useEventStream'

export type Scan = Schemas['ScanOut']
export type HostRow = Schemas['HostRowOut']
export type HostDetail = Schemas['HostDetailOut']
export type MapPoint = Schemas['MapPointOut']
export type UnlocatedHost = Schemas['UnlocatedHostOut']
export type SortKey =
  | 'address'
  | 'reachable'
  | 'open_ports'
  | 'closed'
  | 'filtered'
  | 'country'
  | 'asn'
  | 'organization'
  | 'device_type'

/** The filters of the host table and the map. The server applies them; the client only names them. */
export interface HostFilters {
  open_port?: number
  asn?: number
  organization?: string
  country?: string
  ptr?: string
  /** The effective device type: the override if set, otherwise the automatic one. */
  device_type?: string
  tag?: string
  /** Change status relative to the previous scan (FR-MAP-6). Needs a scan, not the project view. */
  change?: ChangeStatus
}

export const CHANGE_STATUSES = ['changed', 'appeared', 'disappeared', 'unchanged'] as const
export type ChangeStatus = (typeof CHANGE_STATUSES)[number]

export const PAGE_SIZE = 50
/** Wait before data is loaded again after events, so that a burst of events causes one request. */
export const RELOAD_DELAY_MS = 500
const FINAL_STATES = ['completed', 'cancelled', 'failed']

interface SlimHost {
  address: string
  reachable: boolean
  open_port_count: number
  latitude: number | null
  longitude: number | null
}

/**
 * The workspace of a project: the hosts of one scan, or the latest known
 * state of the whole project, as a table page and as map points.
 *
 * Table and map are always loaded with the same filters, so they show the
 * same hosts. Filtering, sorting and paging are done by the server.
 */
export const useWorkspaceStore = defineStore('workspace', () => {
  const projectId = ref<number | null>(null)
  /** The scans of the project, newest first, for the scan selector. */
  const scans = ref<Scan[]>([])
  /** The scan that is shown. Null in the project-wide view. */
  const scan = ref<Scan | null>(null)
  /** True when the latest state of the whole project is shown instead of one scan. */
  const projectView = ref(false)
  const filters = ref<HostFilters>({})
  const hosts = ref<HostRow[]>([])
  const total = ref(0)
  const sort = ref<SortKey>('address')
  const order = ref<'asc' | 'desc'>('asc')
  const page = ref(1)
  const located = ref<MapPoint[]>([])
  const unlocated = ref<UnlocatedHost[]>([])
  const selected = ref<HostDetail | null>(null)

  const filtersActive = computed(() => Object.keys(filters.value).length > 0)

  let reloadTimer: ReturnType<typeof setTimeout> | undefined

  /** Where the hosts come from: the scan, or the project as a whole. */
  function source(): string | null {
    if (scan.value) return `/scans/${scan.value.id}`
    if (projectView.value && projectId.value !== null) return `/projects/${projectId.value}`
    return null
  }

  /**
   * The filters as the server takes them, the same for table, map and export.
   * Table and map show the hosts that were discovered; with a change status
   * they show the hosts that disappeared as well. The project view has no
   * change status: it is relative to one scan.
   */
  function query(): Record<string, string | number | boolean | undefined> {
    const { change, ...others } = filters.value
    if (projectView.value || change === undefined) return { reachable: true, ...others }
    return { ...others, change }
  }

  /** What the export menu of the table downloads: every host the table shows, in its order. */
  const exportTarget = computed(() => {
    const base = source()
    if (!base) return null
    return {
      path: `${base}/hosts`,
      query: { ...query(), sort: sort.value, order: order.value },
    }
  })

  async function loadHosts(): Promise<void> {
    const base = source()
    if (!base) return
    const result = await api.get<Schemas['Page_HostRowOut_']>(`${base}/hosts`, {
      ...query(),
      sort: sort.value,
      order: order.value,
      limit: PAGE_SIZE,
      offset: (page.value - 1) * PAGE_SIZE,
    })
    hosts.value = result.items
    total.value = result.total
  }

  async function loadPoints(): Promise<void> {
    const base = source()
    if (!base) return
    const result = await api.get<Schemas['MapPointsOut']>(`${base}/map-points`, query())
    located.value = result.located
    unlocated.value = result.unlocated
  }

  function reset(): void {
    clearTimeout(reloadTimer)
    hosts.value = []
    total.value = 0
    located.value = []
    unlocated.value = []
    page.value = 1
    selected.value = null
  }

  /** Show one scan. */
  async function open(scanId: number): Promise<void> {
    const opened = await api.get<Scan>(`/scans/${scanId}`)
    reset()
    scan.value = opened
    projectId.value = opened.project_id
    projectView.value = false
    await Promise.all([loadHosts(), loadPoints()])
  }

  /** Show the latest known state of every address of the project (FR-MAP-5). */
  async function openProjectView(id: number): Promise<void> {
    reset()
    scan.value = null
    projectId.value = id
    projectView.value = true
    await Promise.all([loadHosts(), loadPoints()])
  }

  /** Load the scans of a project for the selector. Returns the latest one, if any. */
  async function loadScans(id: number): Promise<Scan | null> {
    const result = await api.get<Schemas['Page_ScanOut_']>(`/projects/${id}/scans`, { limit: 200 })
    projectId.value = id
    scans.value = result.items
    return result.items[0] ?? null
  }

  /** Load everything on screen again, as the server has it now. */
  async function reload(): Promise<void> {
    if (scan.value) scan.value = await api.get<Scan>(`/scans/${scan.value.id}`)
    await Promise.all([loadHosts(), loadPoints()])
    if (selected.value) await select(selected.value.address, selected.value.scan_id)
  }

  async function setFilters(next: HostFilters): Promise<void> {
    if (JSON.stringify(next) === JSON.stringify(filters.value)) return
    filters.value = next
    page.value = 1
    await Promise.all([loadHosts(), loadPoints()])
  }

  async function setSort(key: SortKey, direction: 'asc' | 'desc'): Promise<void> {
    sort.value = key
    order.value = direction
    page.value = 1
    await loadHosts()
  }

  async function setPage(number: number): Promise<void> {
    page.value = number
    await loadHosts()
  }

  /** Open the detail of a host. In the project-wide view each host has a scan of its own. */
  async function select(address: string | null, scanId?: number): Promise<void> {
    const of = scanId ?? scan.value?.id
    if (address === null || of === undefined) {
      selected.value = null
      return
    }
    selected.value = await api.get<HostDetail>(`/scans/${of}/hosts/${address}`)
  }

  /**
   * Apply an event of the live stream.
   *
   * With no filter active, the changed hosts of the scan on screen are applied
   * directly: rows in place, points on the map. With a filter active, or in
   * the project-wide view, the client cannot know whether a changed host
   * belongs on screen; it asks the server again, once per burst of events.
   */
  function applyEvent(event: StreamEvent): void {
    if (event.type === 'scan_progress') {
      applyProgress(event)
      return
    }
    if (event.type !== 'hosts_changed') return

    if (projectView.value) {
      if (event.project_id === projectId.value) scheduleReload()
      return
    }
    if (!scan.value || event.scan_id !== scan.value.id) return
    if (filtersActive.value) {
      scheduleReload()
      return
    }

    let unseen = false
    for (const changed of event.hosts as SlimHost[]) {
      const row = hosts.value.find((host) => host.address === changed.address)
      if (row) {
        row.reachable = changed.reachable
        row.open_port_count = changed.open_port_count
      } else if (changed.reachable) {
        unseen = true
      }
      if (changed.reachable) placeOnMap(changed, scan.value.id)
      if (selected.value?.address === changed.address) void select(changed.address)
    }
    // A host that is not on the table page: the server decides order and page.
    if (unseen) scheduleReload()
  }

  function applyProgress(event: StreamEvent): void {
    const state = event.state as string
    const listed = scans.value.find((item) => item.id === event.scan_id)
    for (const target of [listed, scan.value?.id === event.scan_id ? scan.value : null]) {
      if (!target) continue
      target.state = state
      target.phase = event.phase as string | null
      target.chunks_done = event.chunks_done as number
      target.chunks_total = event.chunks_total as number
      target.probes_sent = event.probes_sent as number
    }
    const shown = scan.value?.id === event.scan_id
    const inProject = projectView.value && event.project_id === projectId.value
    // Closed and filtered counts are summed when the scan ends.
    if ((shown || inProject) && FINAL_STATES.includes(state)) scheduleReload()
  }

  /** Put a changed host on the map, or into the list of hosts without a location. */
  function placeOnMap(host: SlimHost, scanId: number): void {
    const point = located.value.find((item) => item.address === host.address)
    const listed = unlocated.value.find((item) => item.address === host.address)
    if (host.latitude === null || host.longitude === null) {
      if (point) {
        // It has a location already; an event without one does not take it away.
        point.open_port_count = host.open_port_count
        return
      }
      if (listed) listed.open_port_count = host.open_port_count
      else {
        unlocated.value.push({
          address: host.address,
          scan_id: scanId,
          open_port_count: host.open_port_count,
        })
      }
      return
    }
    if (listed) unlocated.value = unlocated.value.filter((item) => item !== listed)
    if (point) {
      point.open_port_count = host.open_port_count
      point.latitude = host.latitude
      point.longitude = host.longitude
    } else {
      located.value.push({
        address: host.address,
        scan_id: scanId,
        open_port_count: host.open_port_count,
        latitude: host.latitude,
        longitude: host.longitude,
        accuracy_radius_km: null,
      })
    }
  }

  /**
   * Set or, with null, clear the analyst's device type of the selected host.
   * It applies to the address in every scan of the project (FR-CLS-5).
   */
  async function setOverride(deviceType: string | null): Promise<void> {
    const host = selected.value
    if (!host || projectId.value === null) return
    const path = `/projects/${projectId.value}/hosts/${host.address}/override`
    if (deviceType === null) await api.delete(path)
    else await api.put(path, { device_type: deviceType })
    await reload()
  }

  /** Replace the tags and notes of the selected host in this project. */
  async function saveAnnotations(tags: string[], notes: string): Promise<void> {
    const host = selected.value
    if (!host || projectId.value === null) return
    await api.put(`/projects/${projectId.value}/hosts/${host.address}/annotations`, {
      tags,
      notes: notes.trim() || null,
    })
    await reload()
  }

  function scheduleReload(): void {
    clearTimeout(reloadTimer)
    reloadTimer = setTimeout(() => void reload(), RELOAD_DELAY_MS)
  }

  return {
    projectId,
    scans,
    scan,
    projectView,
    filters,
    filtersActive,
    exportTarget,
    hosts,
    total,
    sort,
    order,
    page,
    located,
    unlocated,
    selected,
    open,
    openProjectView,
    loadScans,
    reload,
    setFilters,
    setSort,
    setPage,
    select,
    setOverride,
    saveAnnotations,
    applyEvent,
  }
})
