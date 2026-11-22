import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, type Schemas } from '../api/client'
import type { StreamEvent } from '../composables/useEventStream'

export type Scan = Schemas['ScanOut']
export type ScanCreate = Schemas['ScanCreate']
export type Config = Schemas['ConfigOut']
export type Estimate = Schemas['EstimateOut']
export type EstimateRequest = Schemas['EstimateRequest']

export const FINAL_STATES = ['completed', 'cancelled', 'failed']

export const useScansStore = defineStore('scans', () => {
  /** The scans of the project that is open, newest first. */
  const scans = ref<Scan[]>([])
  /** Parameter ceilings and what the scanner can do. */
  const config = ref<Config | null>(null)

  function put(scan: Scan): Scan {
    const index = scans.value.findIndex((known) => known.id === scan.id)
    if (index === -1) scans.value.unshift(scan)
    else scans.value[index] = scan
    return scan
  }

  async function fetchConfig(): Promise<Config> {
    config.value = await api.get<Config>('/config')
    return config.value
  }

  async function fetchScans(projectId: number): Promise<void> {
    const page = await api.get<Schemas['Page_ScanOut_']>(`/projects/${projectId}/scans`, {
      limit: 200,
    })
    scans.value = page.items
  }

  /** Probes and duration a configuration implies. Computed by the server. */
  async function estimate(body: EstimateRequest): Promise<Estimate> {
    return api.post<Estimate>('/scans/estimate', body)
  }

  async function launch(projectId: number, body: ScanCreate): Promise<Scan> {
    return put(await api.post<Scan>(`/projects/${projectId}/scans`, body))
  }

  async function cancel(id: number): Promise<Scan> {
    return put(await api.post<Scan>(`/scans/${id}/cancel`))
  }

  /** Run the failed phase of a scan again. Nothing is probed again. */
  async function retryPhase(id: number): Promise<Scan> {
    return put(await api.post<Scan>(`/scans/${id}/retry-phase`))
  }

  /** Enrich the hosts of a finished scan again. The result is an additional snapshot. */
  async function rerunEnrichment(id: number): Promise<Scan> {
    return put(await api.post<Scan>(`/scans/${id}/enrich`))
  }

  /** Classify the hosts of a finished scan again with the current ruleset. */
  async function rerunClassification(id: number): Promise<Scan> {
    return put(await api.post<Scan>(`/scans/${id}/classify`))
  }

  /** Delete a scan. Call this only after the analyst has confirmed the deletion. */
  async function remove(id: number): Promise<void> {
    await api.delete(`/scans/${id}`, { confirm: true })
    scans.value = scans.value.filter((scan) => scan.id !== id)
  }

  /** Apply an event of the live stream: progress goes to the scan it is about. */
  function applyEvent(event: StreamEvent): void {
    if (event.type !== 'scan_progress') return
    const scan = scans.value.find((known) => known.id === event.scan_id)
    if (!scan) return
    scan.state = event.state as string
    scan.phase = event.phase as string | null
    scan.chunks_done = event.chunks_done as number
    scan.chunks_total = event.chunks_total as number
    scan.probes_sent = event.probes_sent as number
    // How a scan ended, and what can be done about it, is not in the event.
    if (FINAL_STATES.includes(scan.state)) void refresh(scan.id)
  }

  async function refresh(id: number): Promise<void> {
    try {
      put(await api.get<Scan>(`/scans/${id}`))
    } catch {
      // The list is loaded again on the next visit; the event has been applied.
    }
  }

  return {
    scans,
    config,
    fetchConfig,
    fetchScans,
    estimate,
    launch,
    cancel,
    retryPhase,
    rerunEnrichment,
    rerunClassification,
    remove,
    applyEvent,
  }
})
