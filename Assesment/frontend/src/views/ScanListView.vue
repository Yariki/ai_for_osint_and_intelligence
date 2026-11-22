<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'

import { ApiError } from '../api/client'
import ScanLaunchDialog from '../components/ScanLaunchDialog.vue'
import { useEventStream } from '../composables/useEventStream'
import { useProjectsStore } from '../stores/projects'
import { FINAL_STATES, type Scan, useScansStore } from '../stores/scans'

const props = defineProps<{ id: number }>()

const store = useScansStore()
const projects = useProjectsStore()
const stream = useEventStream()

const projectName = ref('')
const archived = ref(false)
const message = ref('')
const launching = ref(false)
const deleting = ref<Scan | null>(null)

async function run(action: () => Promise<unknown>): Promise<void> {
  message.value = ''
  try {
    await action()
  } catch (caught) {
    if (!(caught instanceof ApiError)) throw caught
    message.value = caught.message
  }
}

async function load(): Promise<void> {
  await run(async () => {
    const project = await projects.fetchOne(props.id)
    projectName.value = project.name
    archived.value = project.state === 'archived'
    await store.fetchScans(props.id)
  })
}

watch(() => props.id, load, { immediate: true })

// Live progress. Events are not replayed, so the list is loaded again after a reconnect.
const stopEvents = stream.on(store.applyEvent)
const stopReconnect = stream.onReconnect(load)
onMounted(() => stream.connect())
onUnmounted(() => {
  stopEvents()
  stopReconnect()
})

function progress(scan: Scan): number {
  return scan.chunks_total ? Math.round((100 * scan.chunks_done) / scan.chunks_total) : 0
}

async function confirmDelete(): Promise<void> {
  const scan = deleting.value
  deleting.value = null
  if (scan) await run(() => store.remove(scan.id))
}
</script>

<template>
  <v-container>
    <div class="d-flex align-center mb-4">
      <h1 class="text-h5">Scans of {{ projectName }}</h1>
      <v-spacer />
      <v-btn :to="{ name: 'project', params: { id } }" variant="text">Scope</v-btn>
      <v-btn :to="{ name: 'schedules', params: { id } }" variant="text">Schedules</v-btn>
      <v-btn :to="{ name: 'compare', params: { id } }" variant="text" data-test="scan-compare">
        Compare
      </v-btn>
      <v-btn color="primary" :disabled="archived" data-test="scan-new" @click="launching = true">
        New scan
      </v-btn>
    </div>

    <v-alert v-if="message" type="error" class="mb-4">{{ message }}</v-alert>

    <p v-if="store.scans.length === 0">No scans yet.</p>
    <v-table v-else>
      <thead>
        <tr>
          <th>Scan</th>
          <th>State</th>
          <th>Progress</th>
          <th>Targets</th>
          <th>Ports</th>
          <th>Technique</th>
          <th>Found</th>
          <th />
        </tr>
      </thead>
      <tbody>
        <tr v-for="scan in store.scans" :key="scan.id" data-test="scan-row">
          <td>
            <router-link :to="{ name: 'scan', params: { id: scan.id } }">
              #{{ scan.id }}
            </router-link>
            <div class="text-caption">{{ new Date(scan.created_at).toLocaleString() }}</div>
          </td>
          <td>
            {{ scan.state }}<template v-if="scan.phase">, {{ scan.phase }}</template>
            <v-chip v-if="scan.partial" size="x-small" class="ml-1">partial</v-chip>
            <div v-if="scan.failure_reason" class="text-caption">{{ scan.failure_reason }}</div>
            <div v-if="scan.failed_phase" class="text-caption" data-test="scan-failed-phase">
              Probing was complete; the {{ scan.failed_phase }} phase failed. What was observed is
              kept.
            </div>
          </td>
          <td style="min-width: 140px">
            <v-progress-linear :model-value="progress(scan)" height="8" />
            <span class="text-caption">
              {{ scan.chunks_done }} / {{ scan.chunks_total }} chunks,
              {{ scan.probes_sent.toLocaleString('en') }} probes
            </span>
          </td>
          <td>{{ scan.targets.length }}</td>
          <td>{{ scan.port_profile }} ({{ scan.port_count.toLocaleString('en') }})</td>
          <td>{{ scan.technique_used ?? scan.technique_requested }}</td>
          <td>{{ scan.hosts_reachable }} hosts, {{ scan.open_ports }} open ports</td>
          <td class="text-right">
            <v-btn
              v-if="!FINAL_STATES.includes(scan.state)"
              variant="text"
              size="small"
              :disabled="scan.cancel_requested"
              data-test="scan-cancel"
              @click="run(() => store.cancel(scan.id))"
            >
              {{ scan.cancel_requested ? 'Cancelling' : 'Cancel' }}
            </v-btn>
            <v-btn
              v-if="scan.state === 'failed' && scan.failed_phase"
              variant="text"
              size="small"
              :disabled="scan.retry_requested"
              data-test="scan-retry"
              @click="run(() => store.retryPhase(scan.id))"
            >
              {{ scan.retry_requested ? 'Retry requested' : `Retry ${scan.failed_phase}` }}
            </v-btn>
            <v-btn
              v-if="FINAL_STATES.includes(scan.state)"
              variant="text"
              size="small"
              :disabled="scan.enrichment_requested"
              data-test="scan-enrich"
              @click="run(() => store.rerunEnrichment(scan.id))"
            >
              {{ scan.enrichment_requested ? 'Enrichment requested' : 'Enrich again' }}
            </v-btn>
            <v-btn
              v-if="FINAL_STATES.includes(scan.state)"
              variant="text"
              size="small"
              :disabled="scan.classification_requested"
              data-test="scan-classify"
              @click="run(() => store.rerunClassification(scan.id))"
            >
              {{ scan.classification_requested ? 'Classification requested' : 'Classify again' }}
            </v-btn>
            <v-btn variant="text" size="small" color="error" @click="deleting = scan">Delete</v-btn>
          </td>
        </tr>
      </tbody>
    </v-table>

    <ScanLaunchDialog v-model="launching" :project-id="id" />

    <v-dialog :model-value="deleting !== null" max-width="520" @update:model-value="deleting = null">
      <v-card title="Delete scan">
        <v-card-text>
          Delete scan <strong>#{{ deleting?.id }}</strong> and its results? This cannot be undone.
          The audit log keeps its entries.
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn @click="deleting = null">Cancel</v-btn>
          <v-btn color="error" data-test="scan-delete-confirm" @click="confirmDelete">
            Delete scan
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-container>
</template>
