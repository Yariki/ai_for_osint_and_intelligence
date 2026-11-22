<script setup lang="ts">
// The reference datasets: what enrichment looks hosts up in. Version, age and
// status of each, and a way to ask for an update (FR-ENR-6).
import { onMounted, onUnmounted, ref } from 'vue'

import { ApiError } from '../api/client'
import { MAXMIND_ATTRIBUTION } from '../composables/useMap'
import { age, describe, useDatasetsStore } from '../stores/datasets'

const store = useDatasetsStore()
const message = ref('')

/** While an update is pending, the list is read again to show its result. */
const REFRESH_MS = 5000
let timer: ReturnType<typeof setInterval> | undefined

const STATUS: Record<string, { text: string; color: string }> = {
  ok: { text: 'in use', color: 'success' },
  missing: { text: 'no data', color: 'grey' },
  stale: { text: 'too old, not used', color: 'warning' },
}

async function run(action: () => Promise<unknown>): Promise<void> {
  message.value = ''
  try {
    await action()
  } catch (caught) {
    if (!(caught instanceof ApiError)) throw caught
    message.value = caught.message
  }
}

onMounted(() => {
  void run(store.fetchDatasets)
  timer = setInterval(() => {
    if (store.datasets.some((dataset) => dataset.update_requested)) void run(store.fetchDatasets)
  }, REFRESH_MS)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <v-container>
    <h1 class="text-h5 mb-2">Reference datasets</h1>
    <p class="mb-4">
      Hosts are looked up in these datasets, which are kept on this installation. No address is
      sent to another service for it. A dataset without data leaves its fields "unavailable";
      scanning does not depend on it.
    </p>

    <v-alert v-if="message" type="error" class="mb-4">{{ message }}</v-alert>

    <p v-if="store.datasets.length === 0" data-test="datasets-empty">
      No dataset is known yet. The list appears when the orchestrator has started.
    </p>
    <v-table v-else>
      <thead>
        <tr>
          <th>Dataset</th>
          <th>Version</th>
          <th>Age</th>
          <th>Status</th>
          <th />
        </tr>
      </thead>
      <tbody>
        <tr v-for="dataset in store.datasets" :key="dataset.name" data-test="dataset-row">
          <td>
            {{ describe(dataset.name) }}
            <div class="text-caption">{{ dataset.name }}</div>
          </td>
          <td data-test="dataset-version">{{ dataset.version ?? '—' }}</td>
          <td data-test="dataset-age">{{ age(dataset.age_seconds) }}</td>
          <td>
            <v-chip size="small" :color="STATUS[dataset.status]?.color" data-test="dataset-status">
              {{ STATUS[dataset.status]?.text ?? dataset.status }}
            </v-chip>
            <div v-if="dataset.detail" class="text-caption" data-test="dataset-detail">
              {{ dataset.detail }}
            </div>
          </td>
          <td class="text-right">
            <v-btn
              variant="text"
              size="small"
              :disabled="dataset.update_requested"
              data-test="dataset-update"
              @click="run(() => store.requestUpdate(dataset.name))"
            >
              {{ dataset.update_requested ? 'Update requested' : 'Update now' }}
            </v-btn>
          </td>
        </tr>
      </tbody>
    </v-table>

    <p class="text-caption mt-4">{{ MAXMIND_ATTRIBUTION }}</p>
  </v-container>
</template>
