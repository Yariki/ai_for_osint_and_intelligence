<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import {
  mdiArrowRight,
  mdiRefresh,
  mdiRss,
  mdiTextBoxOutline,
  mdiHistory,
  mdiAlertCircleOutline,
} from '@mdi/js'
import { api, dateTime, messageOf } from '../api'
import type { Job, Page, Source, Stats } from '../types'
import JobBadge from '../components/JobBadge.vue'
const stats = ref<Stats | null>(null)
const jobs = ref<Job[]>([])
const sources = ref<Source[]>([])
const error = ref('')
const busy = ref(false)
const updated = ref<string | null>(null)
let timer: ReturnType<typeof setInterval> | undefined
let alive = true
async function load() {
  if (busy.value) return
  busy.value = true
  try {
    const [s, j, feeds] = await Promise.all([
      api<Stats>('/stats'),
      api<Page<Job>>('/jobs?page_size=6'),
      api<Source[]>('/sources'),
    ])
    if (!alive) return
    stats.value = s
    jobs.value = j.items
    sources.value = feeds
    updated.value = new Date().toISOString()
    error.value = ''
  } catch (err) {
    if (alive) error.value = messageOf(err)
  } finally {
    busy.value = false
  }
}
onMounted(() => {
  void load()
  timer = setInterval(() => void load(), 15000)
})
onUnmounted(() => {
  alive = false
  clearInterval(timer)
})
const sourceName = (id: number) => sources.value.find((s) => s.id === id)?.name ?? `Source ${id}`
</script>
<template>
  <div class="page-heading">
    <div>
      <h1>Your news, on the grid.</h1>
      <p class="subtitle">A clear view of your feeds, articles, and every crawl in between.</p>
    </div>
    <v-btn variant="outlined" :prepend-icon="mdiRefresh" :loading="busy" @click="load">
      Refresh
    </v-btn>
  </div>
  <v-alert v-if="error" type="error">{{ error }}</v-alert>
  <v-row class="mb-5">
    <v-col
      v-for="card in [
        {
          label: 'RSS sources',
          value: stats?.sources,
          note: `${stats?.enabled_sources ?? 0} scheduled sources`,
          icon: mdiRss,
        },
        {
          label: 'Articles collected',
          value: stats?.articles,
          note: 'Unique articles across all feeds',
          icon: mdiTextBoxOutline,
        },
        {
          label: 'Crawl jobs',
          value: stats?.jobs,
          note: `${stats?.active_jobs ?? 0} queued, running or retrying`,
          icon: mdiHistory,
        },
        {
          label: 'Jobs with errors',
          value: stats?.error_jobs,
          note: `${stats?.invalid_entries ?? 0} invalid entries encountered`,
          icon: mdiAlertCircleOutline,
        },
      ]"
      :key="card.label"
      cols="12"
      sm="6"
      xl="3"
      lg="3"
    >
      <v-card class="pa-5 h-100">
        <div class="d-flex align-center justify-space-between">
          <span class="stat-label">{{ card.label }}</span>
          <v-icon :icon="card.icon" size="19" color="primary" />
        </div>
        <div class="stat-value">{{ card.value?.toLocaleString() ?? '—' }}</div>
        <div class="stat-note">{{ card.note }}</div>
      </v-card>
    </v-col>
  </v-row>
  <v-card class="table-card">
    <div class="table-tools">
      <div>
        <h2 class="section-label">Recent activity</h2>
        <p class="subtitle mt-1">Latest crawl jobs across your workspace</p>
      </div>
      <v-btn to="/jobs" variant="text" size="small" :append-icon="mdiArrowRight">
        View all jobs
      </v-btn>
    </div>
    <v-progress-linear v-if="busy" indeterminate color="primary" />
    <v-table>
      <thead>
        <tr>
          <th>Source</th>
          <th>Status</th>
          <th>New articles</th>
          <th>Started</th>
          <th>Attempt</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="job in jobs" :key="job.id">
          <td>{{ sourceName(job.source_id) }}</td>
          <td><JobBadge :status="job.status" /></td>
          <td>{{ job.new_count }}</td>
          <td>{{ dateTime(job.started_at ?? job.created_at) }}</td>
          <td>{{ job.attempts }}</td>
        </tr>
      </tbody>
    </v-table>
    <div v-if="!busy && !jobs.length" class="empty-state">
      Your first crawl will appear here. Sources are checked automatically, or you can run one from
      Sources.
    </div>
  </v-card>
  <v-row class="mt-3">
    <v-col cols="12" md="7">
      <v-card class="pa-6 h-100">
        <h2 class="section-label mb-2">Keep the stories. Skip the duplicates.</h2>
        <p class="subtitle mb-4">
          Articles shared across feeds are collected once, with their categories kept as reusable
          tags.
        </p>
        <v-btn to="/articles" color="primary" :append-icon="mdiArrowRight">Explore articles</v-btn>
      </v-card>
    </v-col>
    <v-col cols="12" md="5">
      <v-card class="pa-6 h-100">
        <h2 class="section-label mb-2">A steady reading rhythm</h2>
        <p class="subtitle">
          Each source follows its own schedule. Publisher rules and rate limits can postpone a
          crawl; its job will show you why.
        </p>
        <p class="stat-note mt-4">Updated {{ dateTime(updated) }} · Refreshes every 15 seconds</p>
      </v-card>
    </v-col>
  </v-row>
</template>
