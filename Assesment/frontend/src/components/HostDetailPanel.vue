<script setup lang="ts">
// Detail of the selected host: reachability evidence, open ports, enrichment
// with the dataset versions behind it, reverse DNS, and the timeline of the
// address across the scans of the project (FR-MAP-9).

import { computed, ref, watch } from 'vue'

import { useTraceroutesStore } from '../stores/traceroutes'
import { useWorkspaceStore } from '../stores/workspace'
import ClassificationCard from './ClassificationCard.vue'
import HostAnnotations from './HostAnnotations.vue'
import HostTimeline from './HostTimeline.vue'
import TracerouteDialog from './TracerouteDialog.vue'
import TracerouteHistory from './TracerouteHistory.vue'
import TracerouteHops from './TracerouteHops.vue'

const workspace = useWorkspaceStore()
const traceroutes = useTraceroutesStore()
const tracing = ref(false)

// The traceroute history belongs to the host that is open.
watch(
  () => [workspace.projectId, workspace.selected?.address] as const,
  ([projectId, address]) => {
    traceroutes.clear()
    if (projectId !== null && address) void traceroutes.fetchHistory(projectId, address)
  },
  { immediate: true },
)

const UNAVAILABLE = 'unavailable'

const PTR_STATUS: Record<string, string> = {
  no_name: 'The resolver has no name for this address.',
  failed: 'The lookup failed: the resolver did not answer.',
  disabled: 'Reverse DNS is switched off in this installation.',
}

/**
 * The rows of the enrichment table. A field whose dataset was missing or
 * stale is unknown, and is shown as "unavailable"; a field that the dataset
 * simply has no value for is shown as a dash.
 */
const enrichment = computed(() => {
  const found = workspace.selected?.enrichment
  if (!found) return []
  const missing = new Set(found.unavailable)
  const shown = (field: string, value: string | number | null | undefined): string =>
    missing.has(field) ? UNAVAILABLE : value === null || value === undefined ? '—' : String(value)

  const coordinates =
    found.latitude !== null && found.longitude !== null
      ? `${found.latitude.toFixed(4)}, ${found.longitude.toFixed(4)}`
      : null
  const source =
    found.organization_source === 'as_holder'
      ? ' (the AS holder: the registry has no record)'
      : found.organization_source === 'registry'
        ? ' (from the registry)'
        : ''
  return [
    { field: 'asn', label: 'ASN', value: shown('asn', found.asn) },
    { field: 'as_holder', label: 'AS holder (ISP)', value: shown('as_holder', found.as_holder) },
    {
      field: 'organization',
      label: 'Organization',
      value: shown('organization', found.organization) + (found.organization ? source : ''),
    },
    {
      field: 'network_name',
      label: 'Network name',
      value: shown('network_name', found.network_name),
    },
    { field: 'rir', label: 'Registry', value: shown('rir', found.rir?.toUpperCase()) },
    {
      field: 'registration_country',
      label: 'Registration country',
      value: shown('registration_country', found.registration_country),
    },
    { field: 'country', label: 'Country (approximate)', value: shown('country', found.country) },
    { field: 'city', label: 'City (approximate)', value: shown('city', found.city) },
    {
      field: 'location',
      label: 'Coordinates (approximate)',
      value: shown('location', coordinates),
    },
    {
      field: 'accuracy_radius_km',
      label: 'Accuracy radius',
      value: shown(
        'accuracy_radius_km',
        found.accuracy_radius_km === null ? null : `${found.accuracy_radius_km} km`,
      ),
    },
  ]
})

const versions = computed(() =>
  Object.entries(workspace.selected?.enrichment?.dataset_versions ?? {}).sort(([a], [b]) =>
    a.localeCompare(b),
  ),
)
</script>

<template>
  <v-card v-if="workspace.selected" :title="workspace.selected.address" data-test="host-detail">
    <template #append>
      <v-btn variant="text" size="small" @click="workspace.select(null)">Close</v-btn>
    </template>
    <v-card-text>
      <h3 class="text-subtitle-2">Reachability evidence</h3>
      <p v-if="workspace.selected.evidence.length === 0">The host did not answer.</p>
      <v-table v-else density="compact" class="mb-4">
        <thead>
          <tr>
            <th>Probe</th>
            <th>Reply received</th>
            <th>Round trip</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in workspace.selected.evidence" :key="item.probe" data-test="evidence">
            <td>{{ item.probe }}</td>
            <td>{{ new Date(item.at).toLocaleString() }}</td>
            <td>{{ item.rtt_ms.toFixed(1) }} ms</td>
          </tr>
        </tbody>
      </v-table>

      <h3 class="text-subtitle-2">Open ports</h3>
      <p v-if="workspace.selected.open_ports.length === 0">No open port was found.</p>
      <template v-else>
        <v-table density="compact">
          <thead>
            <tr>
              <th>Port</th>
              <th>Nominal service</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in workspace.selected.open_ports" :key="item.port" data-test="port">
              <td>{{ item.port }}/tcp</td>
              <td>{{ item.nominal_service ?? '—' }}</td>
            </tr>
          </tbody>
        </v-table>
        <p class="text-caption mb-4" data-test="nominal-note">
          The service name is the IANA assignment of the port number. It is nominal: no service
          was identified on the host.
        </p>
      </template>

      <p class="mb-4" data-test="counts">
        Closed ports: {{ workspace.selected.closed_count }}. Filtered ports (no answer):
        {{ workspace.selected.filtered_count }}.
      </p>

      <h3 class="text-subtitle-2">Registration and location</h3>
      <p v-if="!workspace.selected.enrichment" class="mb-4" data-test="enrichment-none">
        Nothing was looked up for this host: it did not answer, or its scan is still running.
      </p>
      <template v-else>
        <v-table density="compact">
          <tbody>
            <tr v-for="row in enrichment" :key="row.field" :data-test="`enrichment-${row.field}`">
              <th scope="row" class="text-left" style="width: 45%">{{ row.label }}</th>
              <td :class="{ 'text-medium-emphasis font-italic': row.value === UNAVAILABLE }">
                {{ row.value }}
              </td>
            </tr>
          </tbody>
        </v-table>
        <p class="text-caption mt-1" data-test="location-note">
          The location is approximate: it is the centre of an area with the radius shown, not the
          place of the host.
          <template v-if="workspace.selected.enrichment.unavailable.length">
            "unavailable" means the dataset for the field was missing or out of date when the
            snapshot was made; enrichment can be run again from the list of scans.
          </template>
        </p>
        <p class="text-caption mb-4" data-test="dataset-versions">
          Snapshot {{ workspace.selected.enrichment.run }}, made
          {{ new Date(workspace.selected.enrichment.created_at).toLocaleString() }}. Datasets:
          <template v-if="versions.length === 0">none.</template>
          <template v-for="([name, version], index) in versions" :key="name">
            {{ index ? ', ' : '' }}{{ name }} {{ version }}
          </template>
        </p>
      </template>

      <h3 class="text-subtitle-2">Reverse DNS</h3>
      <p class="mb-4" data-test="reverse-dns">
        <template v-if="workspace.selected.reverse_dns.names.length">
          {{ workspace.selected.reverse_dns.names.join(', ') }}
        </template>
        <template v-else-if="workspace.selected.reverse_dns.status">
          {{ PTR_STATUS[workspace.selected.reverse_dns.status] ?? 'No name.' }}
        </template>
        <template v-else>Not looked up.</template>
      </p>

      <h3 class="text-subtitle-2">Device type</h3>
      <ClassificationCard />

      <h3 class="text-subtitle-2">Tags and notes</h3>
      <HostAnnotations />

      <div class="d-flex align-center mt-4">
        <h3 class="text-subtitle-2">Traceroutes</h3>
        <v-spacer />
        <v-btn
          v-if="workspace.projectId !== null"
          size="small"
          variant="tonal"
          data-test="traceroute-open"
          @click="tracing = true"
        >
          Trace route
        </v-btn>
      </div>
      <TracerouteHistory />
      <TracerouteHops class="mb-4" />
      <TracerouteDialog
        v-if="workspace.projectId !== null"
        v-model="tracing"
        :project-id="workspace.projectId"
        :address="workspace.selected.address"
      />

      <h3 class="text-subtitle-2">Across scans</h3>
      <HostTimeline
        :entries="workspace.selected.timeline"
        :current="workspace.selected.scan_id"
        :first-seen-at="workspace.selected.first_seen_at"
        :last-seen-at="workspace.selected.last_seen_at"
      />
    </v-card-text>
  </v-card>
</template>
