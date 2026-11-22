<script setup lang="ts">
// The schedules of a project: when each runs next, how its last run went, and
// why the scheduler stopped it if it did (FR-SCH-2).
import { ref, watch } from 'vue'

import { ApiError } from '../api/client'
import ScheduleDialog from '../components/ScheduleDialog.vue'
import { useProjectsStore } from '../stores/projects'
import { type Schedule, useSchedulesStore } from '../stores/schedules'

const props = defineProps<{ id: number }>()

const store = useSchedulesStore()
const projects = useProjectsStore()

const projectName = ref('')
const archived = ref(false)
const message = ref('')
const editing = ref(false)
const chosen = ref<Schedule | null>(null)
const deleting = ref<Schedule | null>(null)

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
    await store.fetchSchedules(props.id)
  })
}

watch(() => props.id, load, { immediate: true })

function edit(schedule: Schedule | null): void {
  chosen.value = schedule
  editing.value = true
}

function when(schedule: Schedule): string {
  if (schedule.cron) return `${schedule.cron} (${schedule.time_zone})`
  const hours = (schedule.interval_seconds ?? 0) / 3600
  return hours === 1 ? 'every hour' : `every ${hours} hours`
}

function localTime(timestamp: string): string {
  return new Date(timestamp).toLocaleString()
}

/** The last run as one phrase: started and how the scan went, skipped, or rejected. */
function lastRun(schedule: Schedule): string {
  const last = schedule.last_run
  if (!last) return 'not run yet'
  const at = localTime(last.planned_at)
  if (last.outcome === 'started') return `${at}: scan ${last.scan_id} ${last.scan_state ?? 'deleted'}`
  return `${at}: ${last.outcome}${last.reason ? ` (${last.reason})` : ''}`
}

async function confirmDelete(): Promise<void> {
  const schedule = deleting.value
  deleting.value = null
  if (schedule) await run(() => store.deleteSchedule(schedule.id))
}
</script>

<template>
  <v-container>
    <div class="d-flex align-center mb-4">
      <h1 class="text-h5">Schedules of {{ projectName }}</h1>
      <v-spacer />
      <v-btn :to="{ name: 'scans', params: { id } }" variant="text">Scans</v-btn>
      <v-btn color="primary" :disabled="archived" data-test="schedule-new" @click="edit(null)">
        New schedule
      </v-btn>
    </div>
    <v-alert v-if="message" type="error" class="mb-4">{{ message }}</v-alert>
    <v-alert v-if="archived" type="info" class="mb-4">
      This project is archived: its schedules do not run and cannot be changed.
    </v-alert>

    <p v-if="store.schedules.length === 0" data-test="schedules-empty">
      This project has no schedule yet.
    </p>
    <v-table v-else density="compact" data-test="schedules">
      <thead>
        <tr>
          <th>Name</th>
          <th>When</th>
          <th>Next run</th>
          <th>Last run</th>
          <th>Enabled</th>
          <th />
        </tr>
      </thead>
      <tbody>
        <tr v-for="schedule in store.schedules" :key="schedule.id" :data-test="`schedule-${schedule.id}`">
          <td>
            {{ schedule.name }}
            <v-chip
              v-if="schedule.flag_reason"
              color="warning"
              size="small"
              class="ml-2"
              data-test="schedule-flag"
            >
              stopped: {{ schedule.flag_reason }}
            </v-chip>
          </td>
          <td>{{ when(schedule) }}</td>
          <td data-test="schedule-next">
            {{ schedule.enabled && !schedule.flag_reason ? localTime(schedule.next_run_at) : '—' }}
          </td>
          <td data-test="schedule-last">
            <router-link
              v-if="schedule.last_run?.scan_id && schedule.last_run.scan_state"
              :to="{ name: 'scan', params: { id: schedule.last_run.scan_id } }"
            >
              {{ lastRun(schedule) }}
            </router-link>
            <template v-else>{{ lastRun(schedule) }}</template>
          </td>
          <td>
            <v-switch
              :model-value="schedule.enabled"
              :disabled="archived"
              density="compact"
              hide-details
              color="primary"
              data-test="schedule-enabled"
              @update:model-value="(on) => run(() => store.setEnabled(schedule.id, Boolean(on)))"
            />
          </td>
          <td class="text-right">
            <v-btn variant="text" size="small" :disabled="archived" @click="edit(schedule)">
              Edit
            </v-btn>
            <v-btn
              variant="text"
              size="small"
              color="error"
              :disabled="archived"
              @click="deleting = schedule"
            >
              Delete
            </v-btn>
          </td>
        </tr>
      </tbody>
    </v-table>

    <ScheduleDialog v-model="editing" :project-id="id" :schedule="chosen" />

    <v-dialog :model-value="deleting !== null" max-width="480" @update:model-value="deleting = null">
      <v-card title="Delete the schedule?">
        <v-card-text>
          The schedule stops for good. The scans it started stay in the project.
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn @click="deleting = null">Cancel</v-btn>
          <v-btn color="error" data-test="schedule-delete-confirm" @click="confirmDelete">
            Delete
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-container>
</template>
