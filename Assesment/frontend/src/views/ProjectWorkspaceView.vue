<script setup lang="ts">
// The workspace of a project: map, host table, filter bar and scan selector.
//
// It shows one scan, by default the latest of the project, or the latest
// known state of the whole project. Which one, and the filters, are in the
// URL, so the view survives a reload.
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '../api/client'
import FilterBar from '../components/FilterBar.vue'
import HostDetailPanel from '../components/HostDetailPanel.vue'
import HostTable from '../components/HostTable.vue'
import ScanSelector from '../components/ScanSelector.vue'
import UnlocatedPanel from '../components/UnlocatedPanel.vue'
import WorkspaceMap from '../components/WorkspaceMap.vue'
import { useEventStream } from '../composables/useEventStream'
import { filtersToQuery, useHostFilters } from '../composables/useHostFilters'
import { useTraceroutesStore } from '../stores/traceroutes'
import { type HostFilters, useWorkspaceStore } from '../stores/workspace'

const props = defineProps<{ projectId?: number; scanId?: number }>()

const route = useRoute()
const router = useRouter()
const workspace = useWorkspaceStore()
const stream = useEventStream()
const hostFilters = useHostFilters()
const message = ref('')
const empty = ref(false)

const progress = computed(() => {
  const scan = workspace.scan
  return scan?.chunks_total ? Math.round((100 * scan.chunks_done) / scan.chunks_total) : 0
})
const selection = computed(() => (workspace.projectView ? 'project' : (workspace.scan?.id ?? null)))

async function guarded(action: () => Promise<unknown>): Promise<void> {
  message.value = ''
  try {
    await action()
  } catch (caught) {
    if (!(caught instanceof ApiError)) throw caught
    message.value = caught.message
  }
}

/** Load what the URL names: a scan, the whole project, or the project's latest scan. */
async function show(): Promise<void> {
  empty.value = false
  if (props.scanId !== undefined) {
    await workspace.open(props.scanId)
    if (workspace.projectId !== null) await workspace.loadScans(workspace.projectId)
    return
  }
  if (props.projectId === undefined) return
  const latest = await workspace.loadScans(props.projectId)
  const wanted = String(route.query.scan ?? '')
  if (route.query.view === 'project') await workspace.openProjectView(props.projectId)
  else if (/^\d+$/.test(wanted)) await workspace.open(Number(wanted))
  else if (latest) await workspace.open(latest.id)
  else empty.value = true
}

// The filters first: what is loaded next is loaded with them.
watch(
  hostFilters.filters,
  (filters) => guarded(() => workspace.setFilters(filters)),
  { immediate: true },
)
// What is shown is chosen by these alone. A change of the filters keeps it.
watch(
  () => [props.projectId, props.scanId, route.query.scan, route.query.view].join('|'),
  () => guarded(show),
  { immediate: true },
)

function choose(selected: number | 'project'): void {
  if (workspace.projectId === null) return
  void router.push({
    name: 'workspace',
    params: { id: workspace.projectId },
    query: {
      ...filtersToQuery(hostFilters.filters.value),
      ...(selected === 'project' ? { view: 'project' } : { scan: String(selected) }),
    },
  })
}

function filter(filters: HostFilters): void {
  void hostFilters.set(filters)
}

// Live updates. Events are not replayed: after a reconnect the view is loaded again.
const traceroutes = useTraceroutesStore()
const stopEvents = stream.on(workspace.applyEvent)
const stopTraceroutes = stream.on(traceroutes.applyEvent)
const stopReconnect = stream.onReconnect(() => void guarded(workspace.reload))
onMounted(() => stream.connect())
onUnmounted(() => {
  stopEvents()
  stopTraceroutes()
  stopReconnect()
})
</script>

<template>
  <v-container fluid>
    <v-alert v-if="message" type="error" class="mb-4">{{ message }}</v-alert>

    <div class="d-flex flex-wrap align-center ga-3 mb-3">
      <h1 class="text-h5">Workspace</h1>
      <ScanSelector :scans="workspace.scans" :selected="selection" @select="choose" />
      <v-chip v-if="!stream.connected.value" color="warning" data-test="offline">
        live updates paused
      </v-chip>
      <v-spacer />
      <v-btn
        v-if="workspace.projectId !== null"
        :to="{ name: 'scans', params: { id: workspace.projectId } }"
        variant="text"
      >
        All scans
      </v-btn>
    </div>

    <p v-if="empty" data-test="workspace-empty">
      This project has no scan yet. Start one from the list of scans.
    </p>

    <template v-if="workspace.scan">
      <div class="d-flex align-center mb-1">
        <span class="text-subtitle-1">Scan #{{ workspace.scan.id }}</span>
        <v-chip class="ml-3" size="small" data-test="scan-state">
          {{ workspace.scan.state
          }}<template v-if="workspace.scan.phase">, {{ workspace.scan.phase }}</template>
        </v-chip>
        <v-chip v-if="workspace.scan.partial" class="ml-2" size="small" color="warning">
          partial
        </v-chip>
      </div>
      <v-progress-linear :model-value="progress" height="6" class="mb-1" />
      <p class="text-caption mb-3" data-test="scan-progress">
        {{ workspace.scan.chunks_done }} of {{ workspace.scan.chunks_total }} chunks,
        {{ workspace.scan.probes_sent.toLocaleString('en') }} probes sent. Technique:
        {{ workspace.scan.technique_used ?? workspace.scan.technique_requested }}.
        <template v-if="workspace.scan.failure_reason">
          Reason: {{ workspace.scan.failure_reason }}.
        </template>
        <template v-if="workspace.scan.receive_drops > 0">
          Warning: {{ workspace.scan.receive_drops }} replies were dropped at the scanner; results
          may be incomplete.
        </template>
      </p>
      <v-alert
        v-if="workspace.scan.icmp_discovery_unavailable"
        type="warning"
        variant="tonal"
        density="compact"
        class="mb-3"
        data-test="icmp-unavailable"
      >
        ICMP discovery was unavailable: the scanner cannot send echo requests (it has no raw-socket
        capability), so hosts were found by the TCP discovery probes alone. A host that answers
        only ping was not found and not port-scanned. To include such hosts, scan all targets
        regardless of discovery.
      </v-alert>
    </template>
    <p v-else-if="workspace.projectView" class="text-caption mb-3" data-test="project-view-note">
      Every address of the project, as the latest scan that targeted it saw it.
    </p>

    <template v-if="workspace.scan || workspace.projectView">
      <FilterBar
        :filters="hostFilters.filters.value"
        :change-available="!workspace.projectView"
        @change="filter"
      />

      <v-row class="mb-2">
        <v-col cols="12" md="9">
          <WorkspaceMap />
        </v-col>
        <v-col cols="12" md="3">
          <UnlocatedPanel />
        </v-col>
      </v-row>

      <v-row>
        <v-col cols="12" :md="workspace.selected ? 7 : 12">
          <HostTable />
        </v-col>
        <v-col v-if="workspace.selected" cols="12" md="5">
          <HostDetailPanel />
        </v-col>
      </v-row>
    </template>
  </v-container>
</template>
