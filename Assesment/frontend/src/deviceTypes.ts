// The device types of classification ruleset 1 (FR-CLS-1), with their labels.
// The server checks an override against the ruleset; this list only names the
// choices. "unknown" is what the ruleset says below its confidence threshold.

export const DEVICE_TYPES: Array<{ value: string; title: string }> = [
  { value: 'router', title: 'Router' },
  { value: 'gateway', title: 'Gateway, firewall or VPN endpoint' },
  { value: 'dns_server', title: 'DNS server' },
  { value: 'camera', title: 'IP camera or DVR' },
  { value: 'server', title: 'Server' },
  { value: 'unknown', title: 'Unknown' },
]

export const SUB_ROLES: Record<string, string> = {
  web: 'web',
  mail: 'mail',
  database: 'database',
  remote_access: 'remote access',
  file: 'file',
}

export function typeLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return DEVICE_TYPES.find((type) => type.value === value)?.title ?? value
}
