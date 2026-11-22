<script setup lang="ts">
// The launch dialog of a scan.
//
// It shows what the scanner can do and what the server says. It does no scope
// validation of its own: the targets go to the server as typed, and the
// server's rejection is shown beside the target it concerns. The probe and
// duration estimate comes from the server too. A scan profile fills the
// parameters; the targets are always the analyst's own.
import { computed, reactive, ref, watch } from 'vue'

import { ApiError } from '../api/client'
import { applySettings, type ScanSettings, toSettings } from '../scanSettings'
import { type Estimate, type Scan, type ScanCreate, useScansStore } from '../stores/scans'
import ScanProfileSelect from './ScanProfileSelect.vue'

const open = defineModel<boolean>({ required: true })
const props = defineProps<{ projectId: number }>()
const emit = defineEmits<{ launched: [scan: Scan] }>()

const store = useScansStore()

const ESTIMATE_DELAY_MS = 300

const form = reactive({
  targets: '',
  skipDiscovery: false,
  icmpEcho: true,
  discoveryPorts: '80,443',
  scanAllTargets: false,
  portProfile: 'top-1000' as ScanCreate['port_profile'],
  customPorts: '',
  technique: 'automatic' as ScanCreate['technique'],
  pps: 1000,
  timeoutMs: 1500,
  retries: 1,
  hostsInParallel: 64,
})
const estimate = ref<Estimate | null>(null)
const estimateMessage = ref('')
const error = ref<ApiError | null>(null)
const busy = ref(false)

const limits = computed(() => store.config?.limits ?? null)
const scanner = computed(() => store.config?.scanner ?? null)
/** SYN can be forced only when the scanner reports the capability. */
const synAvailable = computed(() => scanner.value?.syn_capable === true)

/** One target per line, as typed. Blank lines are not targets. */
const targets = computed(() => form.targets.split('\n').filter((line) => line.trim() !== ''))

/** Rejections of single targets, as "line: message". */
const targetErrors = computed(() =>
  (error.value?.details ?? [])
    .filter((detail) => detail.field?.startsWith('targets.'))
    .map((detail) => {
      const index = Number(detail.field?.split('.')[1])
      return `${targets.value[index] ?? `target ${index + 1}`}: ${detail.message}`
    }),
)

function ports() {
  return {
    port_profile: form.portProfile,
    custom_ports: form.portProfile === 'custom' ? form.customPorts : null,
  }
}

function body(): ScanCreate {
  return { targets: targets.value, ...toSettings(form) }
}

/** A profile was chosen: its parameters replace those in the form. */
function apply(settings: ScanSettings): void {
  applySettings(form, settings)
  if (!synAvailable.value && form.technique === 'syn') form.technique = 'automatic'
}

// --- Estimate: asked from the server again whenever a parameter that affects it changes.

let estimateTimer: ReturnType<typeof setTimeout> | undefined

async function refreshEstimate(): Promise<void> {
  estimateMessage.value = ''
  if (targets.value.length === 0) {
    estimate.value = null
    return
  }
  try {
    estimate.value = await store.estimate({
      targets: targets.value,
      ...ports(),
      pps: Number(form.pps),
      retries: Number(form.retries),
    })
  } catch (caught) {
    if (!(caught instanceof ApiError)) throw caught
    estimate.value = null
    estimateMessage.value = caught.details[0]?.message ?? caught.message
  }
}

watch(
  () => [form.targets, form.portProfile, form.customPorts, form.pps, form.retries],
  () => {
    clearTimeout(estimateTimer)
    estimateTimer = setTimeout(refreshEstimate, ESTIMATE_DELAY_MS)
  },
)

// --- Opening: defaults from the server's limits, and what the scanner can do now.

watch(open, async (isOpen) => {
  if (!isOpen) return
  error.value = null
  const config = await store.fetchConfig()
  form.pps = config.limits.pps.default
  form.timeoutMs = config.limits.timeout_ms.default
  form.retries = config.limits.retries.default
  form.hostsInParallel = config.limits.hosts_in_parallel.default
  if (!synAvailable.value && form.technique === 'syn') form.technique = 'automatic'
})

function duration(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)} s`
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`
  return `${(seconds / 3600).toFixed(1)} h`
}

async function launch(): Promise<void> {
  busy.value = true
  error.value = null
  try {
    const scan = await store.launch(props.projectId, body())
    open.value = false
    emit('launched', scan)
  } catch (caught) {
    if (!(caught instanceof ApiError)) throw caught
    error.value = caught
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <v-dialog v-model="open" max-width="760" scrollable>
    <v-card title="New scan">
      <v-card-text>
        <v-alert
          v-if="error && targetErrors.length === 0"
          type="error"
          class="mb-4"
          data-test="launch-error"
        >
          {{ error.details[0]?.message ?? error.message }}
        </v-alert>

        <v-textarea
          v-model="form.targets"
          label="Targets, one per line"
          hint="Addresses, CIDR blocks or start-end ranges inside the project scope; at most 256 addresses."
          persistent-hint
          rows="3"
          data-test="launch-targets"
          :error-messages="targetErrors"
        />

        <ScanProfileSelect class="mt-4" :current="() => toSettings(form)" @apply="apply" />

        <h3 class="text-subtitle-1 mt-4">Host discovery</h3>
        <v-checkbox
          v-model="form.skipDiscovery"
          label="Skip discovery and port-scan every target"
          density="compact"
          hide-details
        />
        <template v-if="!form.skipDiscovery">
          <v-checkbox v-model="form.icmpEcho" label="ICMP echo" density="compact" hide-details />
          <v-text-field
            v-model="form.discoveryPorts"
            label="TCP probe ports"
            density="compact"
            hide-details
          />
          <v-checkbox
            v-model="form.scanAllTargets"
            label="Port-scan all targets, also those that do not answer discovery"
            density="compact"
            hide-details
          />
        </template>

        <h3 class="text-subtitle-1 mt-4">Ports</h3>
        <v-radio-group v-model="form.portProfile" inline hide-details data-test="launch-profile">
          <v-radio
            v-for="profile in store.config?.port_profiles ?? []"
            :key="profile.name"
            :value="profile.name"
            :label="`${profile.name} (${profile.ports.toLocaleString('en')})`"
          />
          <v-radio value="custom" label="custom" />
        </v-radio-group>
        <v-text-field
          v-if="form.portProfile === 'custom'"
          v-model="form.customPorts"
          label="Ports and ranges, for example 22,80,8000-8100"
          density="compact"
          :error-messages="error?.fieldMessage('custom_ports')"
        />

        <h3 class="text-subtitle-1 mt-4">Technique</h3>
        <p class="text-caption" data-test="launch-scanner">
          <template v-if="!scanner">No scanner has reported yet.</template>
          <template v-else>
            Scanner {{ scanner.vantage_point }}: {{ scanner.alive ? 'connected' : 'not connected' }};
            SYN {{ scanner.syn_capable ? 'available' : 'not available' }}; ICMP
            {{ scanner.icmp_capable ? 'available' : 'not available' }}.
          </template>
        </p>
        <v-radio-group v-model="form.technique" inline hide-details data-test="launch-technique">
          <v-radio value="automatic" label="Automatic (SYN if available, otherwise connect)" />
          <v-radio value="syn" label="SYN" :disabled="!synAvailable" />
          <v-radio value="connect" label="Connect" />
        </v-radio-group>

        <h3 class="text-subtitle-1 mt-4">Parameters</h3>
        <div v-if="limits" class="d-flex flex-wrap ga-2">
          <v-text-field
            v-model="form.pps"
            type="number"
            label="Packets per second"
            density="compact"
            :hint="`1 to ${limits.pps.max}`"
            persistent-hint
            :error-messages="error?.fieldMessage('pps')"
          />
          <v-text-field
            v-model="form.timeoutMs"
            type="number"
            label="Probe timeout, ms"
            density="compact"
            :hint="`${limits.timeout_ms.min} to ${limits.timeout_ms.max}`"
            persistent-hint
            :error-messages="error?.fieldMessage('timeout_ms')"
          />
          <v-text-field
            v-model="form.retries"
            type="number"
            label="Retries"
            density="compact"
            :hint="`${limits.retries.min} to ${limits.retries.max}`"
            persistent-hint
            :error-messages="error?.fieldMessage('retries')"
          />
          <v-text-field
            v-model="form.hostsInParallel"
            type="number"
            label="Hosts in parallel"
            density="compact"
            :hint="`${limits.hosts_in_parallel.min} to ${limits.hosts_in_parallel.max}`"
            persistent-hint
            :error-messages="error?.fieldMessage('hosts_in_parallel')"
          />
        </div>

        <v-alert type="info" variant="tonal" class="mt-4" data-test="launch-estimate">
          <template v-if="estimate">
            {{ estimate.hosts }} hosts × {{ estimate.ports.toLocaleString('en') }} ports:
            {{ estimate.probes.toLocaleString('en') }} probes, about
            {{ duration(estimate.seconds) }}.
            <template v-if="estimate.max_probes > estimate.probes">
              Up to {{ estimate.max_probes.toLocaleString('en') }} probes and
              {{ duration(estimate.max_seconds) }} if nothing answers.
            </template>
          </template>
          <template v-else-if="estimateMessage">{{ estimateMessage }}</template>
          <template v-else>Enter targets to see the number of probes and the duration.</template>
        </v-alert>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn data-test="launch-cancel" @click="open = false">Cancel</v-btn>
        <v-btn
          color="primary"
          data-test="launch-start"
          :loading="busy"
          :disabled="targets.length === 0"
          @click="launch"
        >
          Start scan
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
