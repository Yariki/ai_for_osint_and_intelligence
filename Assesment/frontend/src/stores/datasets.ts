import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, type Schemas } from '../api/client'

export type Dataset = Schemas['DatasetOut']

/** What each dataset is, for the analyst. The names are the server's. */
export function describe(name: string): string {
  if (name === 'geolite2-city') return 'GeoLite2 City: country, city, coordinates'
  if (name === 'geolite2-asn') return 'GeoLite2 ASN: ASN and AS holder'
  const registry = name.split('-').at(-1)?.toUpperCase() ?? ''
  if (name.startsWith('rir-delegation-')) {
    return `${registry} delegation statistics: registry and registration country`
  }
  if (name.startsWith('rir-objects-')) {
    return `${registry} registration records: network name and organization`
  }
  return name
}

/** An age in words: "3 days", "5 hours", "under an hour". */
export function age(seconds: number | null): string {
  if (seconds === null) return '—'
  const days = Math.floor(seconds / 86400)
  if (days >= 1) return `${days} ${days === 1 ? 'day' : 'days'}`
  const hours = Math.floor(seconds / 3600)
  if (hours >= 1) return `${hours} ${hours === 1 ? 'hour' : 'hours'}`
  return 'under an hour'
}

/** The reference datasets of the installation: versions, ages and status (FR-ENR-6). */
export const useDatasetsStore = defineStore('datasets', () => {
  const datasets = ref<Dataset[]>([])

  async function fetchDatasets(): Promise<void> {
    datasets.value = await api.get<Dataset[]>('/datasets')
  }

  /** Ask for an update now. The orchestrator carries it out within seconds. */
  async function requestUpdate(name: string): Promise<void> {
    const updated = await api.post<Dataset>(`/datasets/${name}/update`)
    const index = datasets.value.findIndex((dataset) => dataset.name === name)
    if (index !== -1) datasets.value[index] = updated
  }

  return { datasets, fetchDatasets, requestUpdate }
})
