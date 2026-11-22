// Test data shared by several specs.

import type { Scan } from './stores/scans'

export function scan(id: number, changes: Partial<Scan> = {}): Scan {
  return {
    id,
    project_id: 1,
    state: 'queued',
    phase: null,
    partial: false,
    failure_reason: null,
    failed_phase: null,
    technique_requested: 'automatic',
    technique_used: null,
    icmp_discovery_unavailable: false,
    targets: ['8.8.8.1', '8.8.8.2'],
    port_profile: 'top-1000',
    port_count: 1000,
    parameters: {},
    attestation_id: 3,
    vantage_point: null,
    created_at: '2026-11-15T10:00:00Z',
    started_at: null,
    finished_at: null,
    cancel_requested: false,
    retry_requested: false,
    enrichment_requested: false,
    classification_requested: false,
    dataset_versions: {},
    chunks_done: 0,
    chunks_total: 0,
    probes_sent: 0,
    receive_drops: 0,
    hosts_reachable: 0,
    open_ports: 0,
    ...changes,
  }
}

export const CONFIG = {
  limits: {
    max_targets: 256,
    max_concurrent_scans: 3,
    pps: { min: 1, max: 5000, default: 1000 },
    timeout_ms: { min: 100, max: 10000, default: 1500 },
    retries: { min: 0, max: 3, default: 1 },
    hosts_in_parallel: { min: 1, max: 256, default: 64 },
  },
  port_profiles: [
    { name: 'top-100', ports: 100 },
    { name: 'top-1000', ports: 1000 },
    { name: 'full', ports: 65535 },
  ],
  scanner: { vantage_point: 'default', alive: true, syn_capable: true, icmp_capable: true },
  tiles: null,
}

type Listener = (event: { data?: string }) => void

/** Stands in for the browser's EventSource; the test plays the server and the network. */
export class FakeEventSource {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  static instances: FakeEventSource[] = []

  readyState = FakeEventSource.CONNECTING
  private listeners = new Map<string, Listener[]>()

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }

  close(): void {
    this.readyState = FakeEventSource.CLOSED
  }

  private emit(type: string, data?: string): void {
    for (const listener of this.listeners.get(type) ?? []) listener({ data })
  }

  /** The connection is established. */
  open(): void {
    this.readyState = FakeEventSource.OPEN
    this.emit('open')
  }

  /** The server sends an event. */
  send(type: string, body: unknown): void {
    this.emit(type, typeof body === 'string' ? body : JSON.stringify(body))
  }

  /** The connection is lost. The browser retries by itself, unless it has given up. */
  drop(givenUp = false): void {
    this.readyState = givenUp ? FakeEventSource.CLOSED : FakeEventSource.CONNECTING
    this.emit('error')
  }

  static latest(): FakeEventSource {
    const source = FakeEventSource.instances.at(-1)
    if (!source) throw new Error('no EventSource was opened')
    return source
  }
}

type HostRow = import('./stores/workspace').HostRow
type HostDetail = import('./stores/workspace').HostDetail
type MapPoint = import('./stores/workspace').MapPoint

/** A row of the host table: a discovered host about which nothing else is known yet. */
export function hostRow(address: string, openPorts = 0, changes: Partial<HostRow> = {}): HostRow {
  return {
    address,
    scan_id: 7,
    reachable: true,
    open_port_count: openPorts,
    closed_count: 0,
    filtered_count: 0,
    ptr_names: [],
    asn: null,
    as_holder: null,
    organization: null,
    country: null,
    city: null,
    latitude: null,
    longitude: null,
    accuracy_radius_km: null,
    device_type: null,
    sub_role: null,
    confidence: null,
    type_override: null,
    effective_type: null,
    tags: [],
    ...changes,
  }
}

export function mapPoint(
  address: string,
  latitude: number,
  longitude: number,
  changes: Partial<MapPoint> = {},
): MapPoint {
  return {
    address,
    scan_id: 7,
    open_port_count: 1,
    latitude,
    longitude,
    accuracy_radius_km: 50,
    ...changes,
  }
}

export const ENRICHMENT: NonNullable<HostDetail['enrichment']> = {
  run: 1,
  created_at: '2026-11-15T10:00:05Z',
  asn: 64496,
  as_holder: 'EXAMPLE-DNS',
  network_name: 'EXAMPLE-DNS',
  organization: 'Example DNS LLC',
  organization_source: 'registry',
  rir: 'arin',
  registration_country: 'US',
  country: 'US',
  city: 'Mountain View',
  latitude: 37.4056,
  longitude: -122.0775,
  accuracy_radius_km: 1000,
  unavailable: [],
  dataset_versions: { 'geolite2-city': '2026-11-13', 'rir-delegation-arin': '2026-11-16' },
}

export function hostDetail(address: string, changes: Partial<HostDetail> = {}): HostDetail {
  return {
    scan_id: 7,
    address,
    reachable: true,
    evidence: [{ probe: 'icmp', at: '2026-11-15T10:00:00Z', rtt_ms: 1.5 }],
    open_ports: [{ port: 443, nominal_service: 'https' }],
    closed_count: 0,
    filtered_count: 0,
    enrichment: ENRICHMENT,
    reverse_dns: { names: ['dns.example.net'], status: 'ok' },
    first_seen_at: '2026-11-15T10:00:00Z',
    last_seen_at: '2026-11-15T10:00:00Z',
    timeline: [],
    classification: null,
    type_override: null,
    effective_type: null,
    tags: [],
    notes: null,
    ...changes,
  }
}

/** A scan profile: custom ports, connect scan, no discovery. */
export function profile(id: number, name = `Profile ${id}`) {
  return {
    id,
    name,
    parameters: {
      port_profile: 'custom' as const,
      custom_ports: '22,80,443,8000-8010',
      discovery: null,
      scan_all_targets: false,
      technique: 'connect' as const,
      pps: 2000,
      timeout_ms: 1000,
      retries: 2,
      hosts_in_parallel: 32,
    },
    port_count: 14,
    created_at: '2026-11-17T09:00:00Z',
    updated_at: '2026-11-17T09:00:00Z',
  }
}

/** A daily schedule of project 1 that has not run yet. */
export function schedule(id: number, values: Record<string, unknown> = {}) {
  return {
    id,
    project_id: 1,
    name: `Schedule ${id}`,
    targets: ['8.8.8.0/24'],
    parameters: profile(0).parameters,
    interval_seconds: 86400,
    cron: null,
    time_zone: 'UTC',
    enabled: true,
    flag_reason: null,
    next_run_at: '2026-11-18T02:00:00Z',
    last_run: null,
    created_at: '2026-11-17T09:00:00Z',
    ...values,
  }
}
