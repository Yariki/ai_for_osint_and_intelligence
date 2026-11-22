<script setup lang="ts">
// Tags and notes of the selected address in this project (FR-CLS-6). They
// belong to the analyst and change nothing a scan observed.
import { ref, watch } from 'vue'

import { ApiError } from '../api/client'
import { useWorkspaceStore } from '../stores/workspace'

const workspace = useWorkspaceStore()
const tags = ref<string[]>([])
const notes = ref('')
const message = ref('')
const saved = ref(false)

watch(
  () => workspace.selected,
  (host) => {
    tags.value = [...(host?.tags ?? [])]
    notes.value = host?.notes ?? ''
    saved.value = false
  },
  { immediate: true },
)

async function save(): Promise<void> {
  message.value = ''
  saved.value = false
  try {
    await workspace.saveAnnotations(tags.value, notes.value)
    saved.value = true
  } catch (caught) {
    if (!(caught instanceof ApiError)) throw caught
    message.value = caught.message
  }
}
</script>

<template>
  <div v-if="workspace.selected" data-test="annotations">
    <v-alert v-if="message" type="error" density="compact" class="mb-2">{{ message }}</v-alert>
    <v-combobox
      v-model="tags"
      label="Tags"
      multiple
      chips
      closable-chips
      density="compact"
      hide-details
      class="mb-2"
      data-test="annotations-tags"
    />
    <v-textarea
      v-model="notes"
      label="Notes"
      rows="2"
      auto-grow
      density="compact"
      hide-details
      class="mb-2"
      data-test="annotations-notes"
    />
    <v-btn size="small" variant="tonal" data-test="annotations-save" @click="save">Save</v-btn>
    <span v-if="saved" class="text-caption ml-2" data-test="annotations-saved">Saved.</span>
  </div>
</template>
