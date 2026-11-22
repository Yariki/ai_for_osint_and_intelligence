<script setup lang="ts">
// Editor for the entries of a project scope: single addresses, CIDR blocks
// and start-end ranges.
//
// It does no validation and no normalization of its own. What the analyst
// typed is sent as it is, and the server's rejection is shown on the entry
// it concerns. Saving goes through the attestation dialog; until that is
// confirmed nothing is sent.
import { ref, watch } from 'vue'

import { ApiError } from '../api/client'
import { type Scope, useProjectsStore } from '../stores/projects'
import AttestationDialog from './AttestationDialog.vue'

const props = defineProps<{ projectId: number; entries: string[]; readonly?: boolean }>()
const emit = defineEmits<{ saved: [scope: Scope] }>()

const store = useProjectsStore()

const rows = ref<string[]>([])
/** Server rejections by row index. */
const rowErrors = ref<Record<number, string>>({})
/** Rejections that concern no single entry, such as the scope ceiling. */
const generalErrors = ref<string[]>([])
const attesting = ref(false)
const busy = ref(false)

watch(
  () => props.entries,
  (entries) => {
    rows.value = [...entries]
    rowErrors.value = {}
    generalErrors.value = []
  },
  { immediate: true },
)

function addRow(): void {
  rows.value.push('')
}

function removeRow(index: number): void {
  rows.value.splice(index, 1)
  // Row numbers have shifted; the old rejections no longer point at the right rows.
  rowErrors.value = {}
}

async function save(): Promise<void> {
  busy.value = true
  rowErrors.value = {}
  generalErrors.value = []
  try {
    emit('saved', await store.saveScope(props.projectId, [...rows.value], true))
  } catch (caught) {
    if (!(caught instanceof ApiError)) throw caught
    if (caught.details.length === 0) generalErrors.value.push(caught.message)
    for (const detail of caught.details) {
      const match = /^entries\.(\d+)$/.exec(detail.field ?? '')
      if (match) rowErrors.value[Number(match[1])] = detail.message
      else generalErrors.value.push(detail.message)
    }
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div>
    <v-alert
      v-for="(message, index) in generalErrors"
      :key="index"
      type="error"
      class="mb-3"
      data-test="scope-error"
    >
      {{ message }}
    </v-alert>

    <p v-if="rows.length === 0" class="mb-3" data-test="scope-empty">The scope has no entries.</p>

    <div v-for="(_, index) in rows" :key="index" class="d-flex align-start" data-test="scope-row">
      <v-text-field
        v-model="rows[index]"
        :label="`Entry ${index + 1}`"
        placeholder="203.0.114.7, 198.52.0.0/24 or 198.52.0.10-198.52.0.20"
        density="compact"
        :readonly="readonly"
        :error-messages="rowErrors[index]"
      />
      <v-btn
        v-if="!readonly"
        class="ml-2"
        variant="text"
        data-test="scope-remove"
        @click="removeRow(index)"
      >
        Remove
      </v-btn>
    </div>

    <div v-if="!readonly" class="mt-2">
      <v-btn class="mr-2" data-test="scope-add" @click="addRow">Add entry</v-btn>
      <v-btn color="primary" :loading="busy" data-test="scope-save" @click="attesting = true">
        Save scope
      </v-btn>
    </div>

    <AttestationDialog v-model="attesting" :entries="rows" @confirm="save" />
  </div>
</template>
