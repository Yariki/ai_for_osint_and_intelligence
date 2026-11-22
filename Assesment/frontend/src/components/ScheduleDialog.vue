<script setup lang="ts">
// Create or edit a schedule: targets, scan parameters, and an interval or a
// cron expression with its time zone (FR-SCH-1).
//
// Like the launch dialog it checks nothing itself: the server checks the
// targets against the scope and the recurrence against the operator's
// minimum, and its rejections are shown on the field they concern.
import { computed, reactive, ref, watch } from 'vue'

import { ApiError } from '../api/client'
import { applySettings, defaultForm, type ScanSettings, toSettings } from '../scanSettings'
import { type Schedule, type ScheduleIn, useSchedulesStore } from '../stores/schedules'
import ScanProfileSelect from './ScanProfileSelect.vue'

const open = defineModel<boolean>({ required: true })
const props = defineProps<{ projectId: number; schedule?: Schedule | null }>()
const emit = defineEmits<{ saved: [schedule: Schedule] }>()

const store = useSchedulesStore()

const PORT_PROFILES: ScanSettings['port_profile'][] = ['top-100', 'top-1000', 'full', 'custom']
const TECHNIQUES = [
  { value: 'automatic', title: 'Automatic (SYN if available)' },
  { value: 'syn', title: 'SYN' },
  { value: 'connect', title: 'Connect' },
]

/** The time zone of the browser, the natural default for a cron expression. */
function localZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
}

const form = reactive({
  name: '',
  targets: '',
  kind: 'interval' as 'interval' | 'cron',
  hours: 24,
  cron: '0 2 * * *',
  timeZone: localZone(),
  enabled: true,
  ...defaultForm(),
})
const error = ref<ApiError | null>(null)
const busy = ref(false)

const editing = computed(() => props.schedule != null)
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

/** Fields the server's details point at that this form shows beside an input. */
const SHOWN = ['name', 'interval_seconds', 'cron', 'time_zone', 'parameters.']
const generalError = computed(() => {
  if (!error.value) return ''
  const other = error.value.details.find(
    (detail) =>
      !detail.field ||
      !(detail.field.startsWith('targets.') || SHOWN.some((name) => detail.field?.startsWith(name))),
  )
  if (error.value.details.length > 0 && !other) return ''
  return other?.message ?? error.value.message
})

function fieldError(field: string): string | undefined {
  return error.value?.fieldMessage(field)
}

watch(open, (isOpen) => {
  if (!isOpen) return
  error.value = null
  const schedule = props.schedule
  if (!schedule) {
    Object.assign(form, {
      name: '',
      targets: '',
      kind: 'interval',
      hours: 24,
      cron: '0 2 * * *',
      timeZone: localZone(),
      enabled: true,
      ...defaultForm(),
    })
    return
  }
  form.name = schedule.name
  form.targets = schedule.targets.join('\n')
  form.kind = schedule.cron ? 'cron' : 'interval'
  form.hours = schedule.interval_seconds ? schedule.interval_seconds / 3600 : 24
  form.cron = schedule.cron ?? '0 2 * * *'
  form.timeZone = schedule.time_zone
  form.enabled = schedule.enabled
  applySettings(form, schedule.parameters)
})

function body(): ScheduleIn {
  const byInterval = form.kind === 'interval'
  return {
    name: form.name,
    targets: targets.value,
    parameters: toSettings(form),
    interval_seconds: byInterval ? Math.round(Number(form.hours) * 3600) : null,
    cron: byInterval ? null : form.cron,
    time_zone: byInterval ? 'UTC' : form.timeZone,
    enabled: form.enabled,
  }
}

async function save(): Promise<void> {
  busy.value = true
  error.value = null
  try {
    const saved = props.schedule
      ? await store.updateSchedule(props.schedule.id, body())
      : await store.createSchedule(props.projectId, body())
    open.value = false
    emit('saved', saved)
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
    <v-card :title="editing ? 'Edit schedule' : 'New schedule'">
      <v-card-text>
        <v-alert v-if="generalError" type="error" class="mb-4" data-test="schedule-error">
          {{ generalError }}
        </v-alert>
        <v-alert v-if="schedule?.flag_reason" type="warning" variant="tonal" class="mb-4">
          The scheduler stopped this schedule ({{ schedule.flag_reason }}). Saving it again lets it
          run.
        </v-alert>

        <v-text-field
          v-model="form.name"
          label="Name"
          density="compact"
          data-test="schedule-name"
          :error-messages="fieldError('name')"
        />
        <v-textarea
          v-model="form.targets"
          label="Targets, one per line"
          hint="Inside the project scope; checked again before every run."
          persistent-hint
          rows="3"
          data-test="schedule-targets"
          :error-messages="targetErrors"
        />

        <h3 class="text-subtitle-1 mt-4">When</h3>
        <v-radio-group v-model="form.kind" inline hide-details data-test="schedule-kind">
          <v-radio value="interval" label="Every few hours" />
          <v-radio value="cron" label="Cron expression" />
        </v-radio-group>
        <v-text-field
          v-if="form.kind === 'interval'"
          v-model="form.hours"
          type="number"
          label="Every … hours"
          density="compact"
          hint="At least the operator's minimum interval, one hour unless changed."
          persistent-hint
          data-test="schedule-hours"
          :error-messages="fieldError('interval_seconds')"
        />
        <div v-else class="d-flex ga-2">
          <v-text-field
            v-model="form.cron"
            label="Cron expression"
            hint="minute hour day month weekday, for example 0 2 * * *"
            persistent-hint
            density="compact"
            data-test="schedule-cron"
            :error-messages="fieldError('cron')"
          />
          <v-text-field
            v-model="form.timeZone"
            label="Time zone"
            hint="For example Europe/Kyiv"
            persistent-hint
            density="compact"
            data-test="schedule-zone"
            :error-messages="fieldError('time_zone')"
          />
        </div>
        <v-checkbox v-model="form.enabled" label="Enabled" density="compact" hide-details />

        <h3 class="text-subtitle-1 mt-4">Scan parameters</h3>
        <ScanProfileSelect :current="() => toSettings(form)" @apply="(s) => applySettings(form, s)" />
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
        <div class="d-flex flex-wrap ga-2 mt-2">
          <v-select
            v-model="form.portProfile"
            :items="PORT_PROFILES"
            label="Ports"
            density="compact"
            data-test="schedule-ports"
          />
          <v-text-field
            v-if="form.portProfile === 'custom'"
            v-model="form.customPorts"
            label="Ports and ranges"
            density="compact"
            data-test="schedule-custom-ports"
            :error-messages="fieldError('parameters.custom_ports') ?? fieldError('parameters')"
          />
          <v-select
            v-model="form.technique"
            :items="TECHNIQUES"
            label="Technique"
            density="compact"
          />
        </div>
        <div class="d-flex flex-wrap ga-2">
          <v-text-field
            v-model="form.pps"
            type="number"
            label="Packets per second"
            density="compact"
            data-test="schedule-pps"
            :error-messages="fieldError('parameters.pps')"
          />
          <v-text-field
            v-model="form.timeoutMs"
            type="number"
            label="Probe timeout, ms"
            density="compact"
            :error-messages="fieldError('parameters.timeout_ms')"
          />
          <v-text-field
            v-model="form.retries"
            type="number"
            label="Retries"
            density="compact"
            :error-messages="fieldError('parameters.retries')"
          />
          <v-text-field
            v-model="form.hostsInParallel"
            type="number"
            label="Hosts in parallel"
            density="compact"
            :error-messages="fieldError('parameters.hosts_in_parallel')"
          />
        </div>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn data-test="schedule-cancel" @click="open = false">Cancel</v-btn>
        <v-btn
          color="primary"
          data-test="schedule-save"
          :loading="busy"
          :disabled="targets.length === 0 || !form.name.trim()"
          @click="save"
        >
          Save
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
