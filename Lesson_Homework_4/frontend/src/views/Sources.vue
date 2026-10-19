<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { mdiPlus, mdiPlayOutline, mdiPencilOutline, mdiRefresh } from '@mdi/js'
import { api, dateTime, messageOf } from '../api'
import type { Job, Source, SourceDraft } from '../types'
import SourceForm from '../components/SourceForm.vue'
const sources = ref<Source[]>([])
const error = ref('')
const notice = ref('')
const loading = ref(false)
const saving = ref(false)
const dialog = ref(false)
const editing = ref<Source>()
const defaultPeriod = ref(60)
const running = ref<number[]>([])
async function load() {
  loading.value = true
  try {
    const [feeds, settings] = await Promise.all([
      api<Source[]>('/sources'),
      api<{ default_period_minutes: number }>('/settings'),
    ])
    sources.value = feeds
    defaultPeriod.value = settings.default_period_minutes
    error.value = ''
  } catch (err) {
    error.value = messageOf(err)
  } finally {
    loading.value = false
  }
}
function open(source?: Source) {
  editing.value = source
  dialog.value = true
  error.value = ''
}
async function save(draft: SourceDraft) {
  saving.value = true
  try {
    const payload = editing.value
      ? { name: draft.name, period_minutes: draft.period_minutes, enabled: draft.enabled }
      : { name: draft.name, url: draft.url, period_minutes: draft.period_minutes }
    await api<Source>(editing.value ? `/sources/${editing.value.id}` : '/sources', {
      method: editing.value ? 'PUT' : 'POST',
      body: JSON.stringify(payload),
    })
    dialog.value = false
    notice.value = 'Source saved.'
    await load()
  } catch (err) {
    error.value = messageOf(err)
  } finally {
    saving.value = false
  }
}
async function crawl(id: number) {
  running.value.push(id)
  try {
    const job = await api<Job>(`/sources/${id}/crawl`, { method: 'POST' })
    notice.value = `Job #${job.id} is ${job.status}. Open Crawl jobs to follow its progress.`
    await load()
  } catch (err) {
    error.value = messageOf(err)
  } finally {
    running.value = running.value.filter((value) => value !== id)
  }
}
onMounted(() => void load())
</script>
<template>
  <div class="page-heading">
    <div>
      <h1>Your sources.</h1>
      <p class="subtitle">Give each feed a name and a rhythm. We’ll take it from there.</p>
    </div>
    <v-btn color="primary" :prepend-icon="mdiPlus" @click="open()">Add source</v-btn>
  </div>
  <v-alert v-if="error && !dialog" type="error">{{ error }}</v-alert>
  <v-alert v-if="notice" type="success" closable @click:close="notice = ''">{{ notice }}</v-alert>
  <v-card class="table-card">
    <div class="table-tools">
      <h2 class="section-label">{{ sources.length }} RSS sources</h2>
      <v-btn
        variant="text"
        :prepend-icon="mdiRefresh"
        :loading="loading"
        size="small"
        @click="load"
      >
        Refresh
      </v-btn>
    </div>
    <v-progress-linear v-if="loading" indeterminate color="primary" />
    <v-table>
      <thead>
        <tr>
          <th>Name / feed</th>
          <th>Schedule</th>
          <th>Status</th>
          <th>Last success</th>
          <th>Next scheduled</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="source in sources" :key="source.id">
          <td>
            <strong>{{ source.name }}</strong>
            <div class="source-url" :title="source.url">{{ source.url }}</div>
          </td>
          <td>Every {{ source.period_minutes }} min</td>
          <td>
            <v-chip size="x-small" :color="source.enabled ? 'success' : 'secondary'" label>
              {{ source.enabled ? 'Enabled' : 'Paused' }}
            </v-chip>
          </td>
          <td>{{ dateTime(source.last_success_at) }}</td>
          <td>{{ source.enabled ? dateTime(source.next_run_at) : 'Paused' }}</td>
          <td class="text-no-wrap">
            <v-btn
              variant="tonal"
              color="primary"
              size="small"
              :prepend-icon="mdiPlayOutline"
              :loading="running.includes(source.id)"
              @click="crawl(source.id)"
            >
              Run now
            </v-btn>
            <v-btn
              class="ml-1"
              size="small"
              variant="text"
              :icon="mdiPencilOutline"
              :aria-label="`Edit ${source.name}`"
              @click="open(source)"
            />
          </td>
        </tr>
      </tbody>
    </v-table>
    <div v-if="!loading && !sources.length" class="empty-state">
      Add your first RSS or Atom source to start collecting articles.
    </div>
  </v-card>
  <v-dialog v-model="dialog" max-width="520" :persistent="saving">
    <div>
      <v-alert v-if="error" type="error">{{ error }}</v-alert>
      <SourceForm
        v-if="dialog"
        :key="editing?.id ?? 'new'"
        :source="editing"
        :default-period="defaultPeriod"
        :busy="saving"
        @save="save"
        @cancel="dialog = false"
      />
    </div>
  </v-dialog>
</template>
