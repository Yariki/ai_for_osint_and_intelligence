<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { ApiError } from '../api/client'
import ScopeEditor from '../components/ScopeEditor.vue'
import { type Project, type Scope, useProjectsStore } from '../stores/projects'

const props = defineProps<{ id: number }>()

const store = useProjectsStore()

const project = ref<Project | null>(null)
const scope = ref<Scope | null>(null)
const history = ref<Scope[]>([])
const message = ref('')
const saved = ref(false)

const scopeEntries = computed(() => scope.value?.entries.map((entry) => entry.text) ?? [])

async function load(): Promise<void> {
  message.value = ''
  saved.value = false
  try {
    project.value = await store.fetchOne(props.id)
    scope.value = await store.fetchScope(props.id)
    history.value = await store.fetchScopeHistory(props.id)
  } catch (caught) {
    if (!(caught instanceof ApiError)) throw caught
    project.value = null
    message.value = caught.message
  }
}

watch(() => props.id, load, { immediate: true })

async function onSaved(newScope: Scope): Promise<void> {
  scope.value = newScope
  saved.value = true
  history.value = await store.fetchScopeHistory(props.id)
}

/** Timestamps are stored in UTC and shown in the analyst's local time zone. */
function localTime(timestamp: string | null): string {
  return timestamp ? new Date(timestamp).toLocaleString() : ''
}
</script>

<template>
  <v-container>
    <v-alert v-if="message" type="error" class="mb-4">{{ message }}</v-alert>

    <template v-if="project">
      <div class="d-flex align-center">
        <h1 class="text-h5">{{ project.name }}</h1>
        <v-spacer />
        <v-btn
          :to="{ name: 'workspace', params: { id: project.id } }"
          color="primary"
          variant="text"
          data-test="open-workspace"
        >
          Workspace
        </v-btn>
        <v-btn :to="{ name: 'scans', params: { id: project.id } }" color="primary" variant="text">
          Scans
        </v-btn>
        <v-btn
          :to="{ name: 'schedules', params: { id: project.id } }"
          color="primary"
          variant="text"
          data-test="open-schedules"
        >
          Schedules
        </v-btn>
      </div>
      <p class="mb-2">{{ project.description }}</p>
      <v-alert v-if="project.state === 'archived'" type="info" class="mb-4">
        This project is archived and read-only.
      </v-alert>

      <v-card title="Authorized scope" class="mb-6">
        <v-card-subtitle v-if="scope?.attestation_id">
          Attested by {{ scope.attested_by }} on {{ localTime(scope.attested_at) }}
        </v-card-subtitle>
        <v-card-text>
          <v-alert v-if="saved" type="success" class="mb-3">The scope was saved.</v-alert>
          <ScopeEditor
            :project-id="project.id"
            :entries="scopeEntries"
            :readonly="project.state === 'archived'"
            @saved="onSaved"
          />
        </v-card-text>
      </v-card>

      <v-card title="Scope history">
        <v-card-text>
          <p v-if="history.length === 0">No scope has been saved yet.</p>
          <v-table v-else density="compact">
            <thead>
              <tr>
                <th>Attested</th>
                <th>By</th>
                <th>Entries</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="version in history" :key="version.attestation_id ?? 0">
                <td>{{ localTime(version.attested_at) }}</td>
                <td>{{ version.attested_by }}</td>
                <td>
                  <code v-for="(entry, index) in version.entries" :key="index" class="mr-2">
                    {{ entry.text }}
                  </code>
                  <em v-if="version.entries.length === 0">empty</em>
                </td>
              </tr>
            </tbody>
          </v-table>
        </v-card-text>
      </v-card>
    </template>
  </v-container>
</template>
