<script setup lang="ts">
// The audit log: read-only, with filters and paging. Filtering is done by the server.
import { computed, onMounted, reactive, ref } from 'vue'

import { api, ApiError, type Schemas } from '../api/client'

type AuditEvent = Schemas['AuditEventOut']

const PAGE_SIZE = 50

const filters = reactive({
  action: '',
  actor: '',
  object_type: '',
  object_id: '',
  since: '',
  until: '',
})
const events = ref<AuditEvent[]>([])
const total = ref(0)
const page = ref(1)
const message = ref('')

const pages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

/** A value of a datetime-local input, which is local time, as a UTC timestamp. */
function toUtc(local: string): string {
  return local ? new Date(local).toISOString() : ''
}

async function load(): Promise<void> {
  message.value = ''
  try {
    const result = await api.get<Schemas['Page_AuditEventOut_']>('/audit', {
      ...filters,
      since: toUtc(filters.since),
      until: toUtc(filters.until),
      limit: PAGE_SIZE,
      offset: (page.value - 1) * PAGE_SIZE,
    })
    events.value = result.items
    total.value = result.total
  } catch (caught) {
    if (!(caught instanceof ApiError)) throw caught
    message.value = caught.message
  }
}

function applyFilters(): Promise<void> {
  page.value = 1
  return load()
}

function goTo(newPage: number): Promise<void> {
  page.value = newPage
  return load()
}

onMounted(load)
</script>

<template>
  <v-container>
    <h1 class="text-h5 mb-4">Audit log</h1>

    <v-form class="d-flex flex-wrap ga-2 mb-2" @submit.prevent="applyFilters">
      <v-text-field v-model="filters.action" label="Action" density="compact" hide-details />
      <v-text-field v-model="filters.actor" label="Actor" density="compact" hide-details />
      <v-text-field
        v-model="filters.object_type"
        label="Object type"
        density="compact"
        hide-details
      />
      <v-text-field v-model="filters.object_id" label="Object ID" density="compact" hide-details />
      <v-text-field
        v-model="filters.since"
        label="From"
        type="datetime-local"
        density="compact"
        hide-details
      />
      <v-text-field
        v-model="filters.until"
        label="To"
        type="datetime-local"
        density="compact"
        hide-details
      />
      <v-btn type="submit" color="primary" data-test="audit-filter">Filter</v-btn>
    </v-form>

    <v-alert v-if="message" type="error" class="mb-4">{{ message }}</v-alert>

    <p class="text-caption mb-2">{{ total }} entries. Times are shown in your local time zone.</p>
    <v-table density="compact">
      <thead>
        <tr>
          <th>Time</th>
          <th>Actor</th>
          <th>Action</th>
          <th>Object</th>
          <th>Details</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="event in events" :key="event.id" data-test="audit-row">
          <td>{{ new Date(event.occurred_at).toLocaleString() }}</td>
          <td>{{ event.actor }}</td>
          <td>{{ event.action }}</td>
          <td>{{ event.object_type }} {{ event.object_id }}</td>
          <td>
            <code>{{ JSON.stringify(event.details) }}</code>
          </td>
        </tr>
      </tbody>
    </v-table>

    <v-pagination
      v-if="pages > 1"
      :model-value="page"
      :length="pages"
      class="mt-4"
      @update:model-value="goTo"
    />
  </v-container>
</template>
