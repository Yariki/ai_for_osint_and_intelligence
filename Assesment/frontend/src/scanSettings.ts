// The parameters of a scan without its targets, as a form edits them and as
// the API takes them. A scan profile and a schedule store this set
// (FR-CFG-8); the launch and schedule dialogs edit it the same way.

import type { Schemas } from './api/client'

export type ScanSettings = Schemas['ScanSettings']

export interface SettingsForm {
  skipDiscovery: boolean
  icmpEcho: boolean
  discoveryPorts: string
  scanAllTargets: boolean
  portProfile: ScanSettings['port_profile']
  customPorts: string
  technique: ScanSettings['technique']
  pps: number
  timeoutMs: number
  retries: number
  hostsInParallel: number
}

export function defaultForm(): SettingsForm {
  return {
    skipDiscovery: false,
    icmpEcho: true,
    discoveryPorts: '80,443',
    scanAllTargets: false,
    portProfile: 'top-1000',
    customPorts: '',
    technique: 'automatic',
    pps: 1000,
    timeoutMs: 1500,
    retries: 1,
    hostsInParallel: 64,
  }
}

/** The API's parameter set from the form. */
export function toSettings(form: SettingsForm): ScanSettings {
  return {
    discovery: form.skipDiscovery
      ? null
      : {
          icmp_echo: form.icmpEcho,
          tcp_ports: form.discoveryPorts
            .split(',')
            .filter((port) => port.trim() !== '')
            .map(Number),
        },
    scan_all_targets: form.scanAllTargets,
    port_profile: form.portProfile,
    custom_ports: form.portProfile === 'custom' ? form.customPorts : null,
    technique: form.technique,
    pps: Number(form.pps),
    timeout_ms: Number(form.timeoutMs),
    retries: Number(form.retries),
    hosts_in_parallel: Number(form.hostsInParallel),
  }
}

/** Fill the form with a stored parameter set, such as a profile's. */
export function applySettings(form: SettingsForm, settings: ScanSettings): void {
  form.skipDiscovery = settings.discovery === null
  form.icmpEcho = settings.discovery?.icmp_echo ?? true
  form.discoveryPorts = (settings.discovery?.tcp_ports ?? [80, 443]).join(',')
  form.scanAllTargets = settings.scan_all_targets
  form.portProfile = settings.port_profile
  form.customPorts = settings.custom_ports ?? ''
  form.technique = settings.technique
  form.pps = settings.pps
  form.timeoutMs = settings.timeout_ms
  form.retries = settings.retries
  form.hostsInParallel = settings.hosts_in_parallel
}
