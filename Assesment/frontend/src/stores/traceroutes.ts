import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, type Schemas } from '../api/client'
import type { StreamEvent } from '../composables/useEventStream'

export type Traceroute = Schemas['TracerouteOut']
export type TracerouteDetail = Schemas['TracerouteDetailOut']
export type Hop = Schemas['HopOut']
export type TracerouteCreate = Schemas['TracerouteCreate']

export const FINAL_TRACEROUTE_STATES = ['completed', 'failed']

interface HopEventResponder {
  address: string
  rtt_ms: Array<number | null>
  unlocatable: boolean
  latitude: number | null
  longitude: number | null
}

/**
 * The traceroutes of the selected host: its history, and the run that is
 * open, whose hops arrive live while it runs (FR-TRC-6, FR-TRC-7).
 */
export const useTraceroutesStore = defineStore('traceroutes', () => {
  /** The host the history is about. */
  const host = ref<{ projectId: number; address: string } | null>(null)
  const history = ref<Traceroute[]>([])
  /** The run that is open: its parameters and its hops in TTL order. */
  const current = ref<TracerouteDetail | null>(null)

  async function fetchHistory(projectId: number, address: string): Promise<void> {
    host.value = { projectId, address }
    const page = await api.get<Schemas['Page_TracerouteOut_']>(
      `/projects/${projectId}/traceroutes`,
      { target: address, limit: 50 },
    )
    history.value = page.items
  }

  /** Start a traceroute. The server checks the target against the scope. */
  async function start(projectId: number, body: TracerouteCreate): Promise<Traceroute> {
    const created = await api.post<Traceroute>(`/projects/${projectId}/traceroutes`, body)
    history.value = [created, ...history.value.filter((run) => run.id !== created.id)]
    current.value = { ...created, hops: [] }
    return created
  }

  async function open(runId: number): Promise<void> {
    current.value = await api.get<TracerouteDetail>(`/traceroutes/${runId}`)
  }

  function close(): void {
    current.value = null
  }

  function clear(): void {
    host.value = null
    history.value = []
    current.value = null
  }

  /**
   * Apply an event of the live stream. A hop is put in its place by TTL, so
   * hops are shown in TTL order whatever order their events arrive in. When
   * the run ends, it is read again: the hops' enrichment and reverse DNS are
   * complete only then.
   */
  function applyEvent(event: StreamEvent): void {
    if (event.type !== 'traceroute_hop') return
    const runId = event.run_id as number
    const state = event.state as string
    const listed = history.value.find((run) => run.id === runId)
    if (listed) listed.state = state
    const run = current.value
    if (!run || run.id !== runId) return
    run.state = state

    const ttl = event.ttl as number | null
    if (ttl !== null) {
      const responders = (event.responders as HopEventResponder[]).map((responder) => ({
        address: responder.address,
        rtt_ms: responder.rtt_ms,
        unlocatable: responder.unlocatable,
        ptr_names: [],
        ptr_status: null,
        latitude: responder.latitude,
        longitude: responder.longitude,
      }))
      const lost = event.lost as number
      const answered = responders.reduce(
        (count, responder) => count + responder.rtt_ms.filter((rtt) => rtt !== null).length,
        0,
      )
      const hop: Hop = { ttl, responders, probes: [], lost, silent: answered === 0 }
      const hops = run.hops.filter((known) => known.ttl !== ttl)
      hops.push(hop)
      hops.sort((a, b) => a.ttl - b.ttl)
      run.hops = hops
    }
    if (FINAL_TRACEROUTE_STATES.includes(state)) void open(runId)
  }

  return { host, history, current, fetchHistory, start, open, close, clear, applyEvent }
})
