<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { mdiRefresh } from '@mdi/js'
import { api, dateTime, messageOf } from '../api'
import type { Job, JobStatus, Page, Source } from '../types'
import JobBadge from '../components/JobBadge.vue'
import PageControls from '../components/PageControls.vue'
const jobs = ref<Job[]>([])
const sources = ref<Source[]>([])
const sourceId = ref<number | null>(null)
const status = ref<JobStatus | null>(null)
const statuses: JobStatus[] = [
  'queued',
  'running',
  'retrying',
  'successful',
  'partial',
  'failed',
  'blocked',
]
const page = ref(1)
const total = ref(0)
const error = ref('')
const busy = ref(false)
const pageSize = 20
let timer: ReturnType<typeof setInterval> | undefined
let sequence = 0
let alive = true
async function load(nextPage = page.value) {
  const request = ++sequence
  page.value = nextPage
  busy.value = true
  const query = new URLSearchParams({ page: String(page.value), page_size: String(pageSize) })
  if (sourceId.value) query.set('source_id', String(sourceId.value))
  if (status.value) query.set('status', status.value)
  try {
    const result = await api<Page<Job>>(`/jobs?${query}`)
    if (alive && request === sequence) {
      jobs.value = result.items
      total.value = result.total
      error.value = ''
    }
  } catch (err) {
    if (alive && request === sequence) error.value = messageOf(err)
  } finally {
    if (request === sequence) busy.value = false
  }
}
function duration(job: Job) {
  if (!job.started_at) return '—'
  const seconds = Math.max(
    0,
    Math.round(
      ((job.finished_at ? new Date(job.finished_at) : new Date()).getTime() -
        new Date(job.started_at).getTime()) /
        1000,
    ),
  )
  return `${seconds}s`
}
onMounted(async () => {
  try {
    sources.value = await api<Source[]>('/sources')
    await load()
  } catch (err) {
    error.value = messageOf(err)
  }
  if (alive) timer = setInterval(() => void load(), 15000)
})
onUnmounted(() => {
  alive = false
  clearInterval(timer)
})
</script>
<template>
  <div class="page-heading">
    <div>
      <h1>Every crawl, accounted for.</h1>
      <p class="subtitle">
        Follow queued jobs, new discoveries, retries, and anything that needs attention.
      </p>
    </div>
    <v-btn variant="outlined" :prepend-icon="mdiRefresh" :loading="busy" @click="load()">
      Refresh
    </v-btn>
  </div>
  <v-alert v-if="error" type="error">{{ error }}</v-alert>
  <v-card class="table-card">
    <div class="filters">
      <v-row dense>
        <v-col cols="12" sm="6" md="4">
          <v-select
            v-model="sourceId"
            :items="sources"
            item-title="name"
            item-value="id"
            label="All sources"
            clearable
            @update:model-value="load(1)"
          />
        </v-col>
        <v-col cols="12" sm="6" md="3">
          <v-select
            v-model="status"
            :items="statuses"
            label="All statuses"
            clearable
            @update:model-value="load(1)"
          />
        </v-col>
        <v-col cols="12" md="5" class="subtitle pt-4 text-md-right">
          Refreshes every 15 seconds
        </v-col>
      </v-row>
    </div>
    <v-progress-linear v-if="busy" indeterminate color="primary" />
    <v-table>
      <thead>
        <tr>
          <th>Job / source</th>
          <th>Status</th>
          <th>New / seen / invalid</th>
          <th>Started / duration</th>
          <th>Details</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="job in jobs" :key="job.id">
          <td>
            <strong>#{{ job.id }}</strong>
            ·
            {{ sources.find((s) => s.id === job.source_id)?.name ?? `Source ${job.source_id}` }}
            <div class="stat-note mt-1">{{ job.trigger }} · attempt {{ job.attempts }}</div>
          </td>
          <td>
            <JobBadge :status="job.status" />
            <div v-if="job.status === 'retrying'" class="stat-note mt-2">
              After {{ dateTime(job.next_attempt_at) }}
            </div>
          </td>
          <td>{{ job.new_count }} / {{ job.duplicate_count }} / {{ job.invalid_count }}</td>
          <td class="text-no-wrap">
            {{ dateTime(job.started_at ?? job.created_at) }}
            <div class="stat-note mt-1">{{ duration(job) }}</div>
          </td>
          <td style="min-width: 200px">
            <span v-if="job.http_status" class="stat-note">HTTP {{ job.http_status }}</span>
            <details v-if="job.errors.length" class="mt-1">
              <summary>{{ job.errors.length }} recorded issue(s)</summary>
              <p v-for="(issue, i) in job.errors" :key="i" class="stat-note mt-2">
                {{ issue.code }}: {{ issue.message }}
              </p>
            </details>
            <span v-else class="stat-note">
              {{ job.status === 'successful' ? 'Completed' : '—' }}
            </span>
          </td>
        </tr>
      </tbody>
    </v-table>
    <div v-if="!busy && !jobs.length" class="empty-state">No jobs match these filters.</div>
    <PageControls :page="page" :total="total" :page-size="pageSize" @change="load" />
  </v-card>
</template>
