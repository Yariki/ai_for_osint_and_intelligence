// The host filters, mirrored in the URL.
//
// The address bar is the one place the filters are kept: a reload, a
// bookmark or a shared link shows the same view. Which hosts pass a filter is
// decided by the server alone; nothing here looks at a host.

import { computed, type ComputedRef } from 'vue'
import { type LocationQuery, useRoute, useRouter } from 'vue-router'

import { CHANGE_STATUSES, type ChangeStatus, type HostFilters } from '../stores/workspace'

const NUMBERS = ['open_port', 'asn'] as const
const TEXTS = ['organization', 'country', 'ptr', 'device_type', 'tag'] as const
export const FILTER_NAMES = [...NUMBERS, ...TEXTS, 'change'] as const

function single(value: LocationQuery[string] | undefined): string {
  const first = Array.isArray(value) ? value[0] : value
  return (first ?? '').trim()
}

/** The filters a URL query holds. What is not a valid value is left out. */
export function filtersFromQuery(query: LocationQuery): HostFilters {
  const filters: HostFilters = {}
  for (const name of NUMBERS) {
    const text = single(query[name])
    if (/^\d{1,10}$/.test(text)) filters[name] = Number(text)
  }
  for (const name of TEXTS) {
    const text = single(query[name])
    if (text) filters[name] = text
  }
  if (filters.open_port !== undefined && (filters.open_port < 1 || filters.open_port > 65535)) {
    delete filters.open_port
  }
  if (filters.device_type !== undefined && !/^[a-z_]{1,32}$/.test(filters.device_type)) {
    delete filters.device_type
  }
  if (filters.country !== undefined) {
    if (/^[A-Za-z]{2}$/.test(filters.country)) filters.country = filters.country.toUpperCase()
    else delete filters.country
  }
  const change = single(query.change)
  if ((CHANGE_STATUSES as readonly string[]).includes(change)) {
    filters.change = change as ChangeStatus
  }
  return filters
}

/** The query parameters of a set of filters. An empty filter has no parameter. */
export function filtersToQuery(filters: HostFilters): Record<string, string> {
  const query: Record<string, string> = {}
  for (const name of FILTER_NAMES) {
    const value = filters[name]
    if (value !== undefined && String(value).trim() !== '') query[name] = String(value).trim()
  }
  return query
}

export function useHostFilters(): {
  filters: ComputedRef<HostFilters>
  active: ComputedRef<boolean>
  set: (filters: HostFilters) => Promise<void>
  clear: () => Promise<void>
} {
  const route = useRoute()
  const router = useRouter()

  const filters = computed(() => filtersFromQuery(route.query))
  const active = computed(() => Object.keys(filters.value).length > 0)

  /** Replace the filters. Other query parameters, such as the scan, are kept. */
  async function set(next: HostFilters): Promise<void> {
    const others = Object.fromEntries(
      Object.entries(route.query).filter(
        ([name]) => !(FILTER_NAMES as readonly string[]).includes(name),
      ),
    )
    await router.replace({ query: { ...others, ...filtersToQuery(filtersFromQueryLike(next)) } })
  }

  return { filters, active, set, clear: () => set({}) }
}

/** Pass filters through the same checks a URL goes through. */
function filtersFromQueryLike(filters: HostFilters): HostFilters {
  return filtersFromQuery(filtersToQuery(filters))
}
