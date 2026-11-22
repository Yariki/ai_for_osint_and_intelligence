<script setup lang="ts">
// Choose a scan profile to fill a dialog with its parameters, or save the
// dialog's parameters as a profile (FR-CFG-8). The parent owns the form: this
// component asks it for the current parameters and hands it a profile's.
import { onMounted, ref } from 'vue'

import { ApiError } from '../api/client'
import type { ScanSettings } from '../scanSettings'
import { type Profile, useSchedulesStore } from '../stores/schedules'

const props = defineProps<{ current: () => ScanSettings }>()
const emit = defineEmits<{ apply: [settings: ScanSettings] }>()

const store = useSchedulesStore()
const selected = ref<number | null>(null)
const saving = ref(false)
const name = ref('')
const message = ref('')

onMounted(() => {
  void store.fetchProfiles().catch(() => {
    message.value = 'The scan profiles could not be loaded.'
  })
})

function choose(id: number | null): void {
  selected.value = id
  const profile = store.profiles.find((candidate: Profile) => candidate.id === id)
  if (profile) emit('apply', profile.parameters)
}

function failed(caught: unknown): void {
  if (!(caught instanceof ApiError)) throw caught
  message.value = caught.details[0]?.message ?? caught.message
}

async function saveNew(): Promise<void> {
  message.value = ''
  try {
    const created = await store.createProfile({ name: name.value, parameters: props.current() })
    selected.value = created.id
    saving.value = false
    name.value = ''
  } catch (caught) {
    failed(caught)
  }
}

async function update(): Promise<void> {
  const profile = store.profiles.find((candidate) => candidate.id === selected.value)
  if (!profile) return
  message.value = ''
  try {
    await store.updateProfile(profile.id, { name: profile.name, parameters: props.current() })
  } catch (caught) {
    failed(caught)
  }
}
</script>

<template>
  <div data-test="profile-select">
    <div class="d-flex align-center ga-2">
      <v-select
        :model-value="selected"
        :items="store.profiles"
        item-title="name"
        item-value="id"
        label="Scan profile"
        density="compact"
        clearable
        hide-details
        data-test="profile-choice"
        @update:model-value="choose"
      />
      <v-btn
        variant="text"
        size="small"
        :disabled="selected === null"
        data-test="profile-update"
        @click="update"
      >
        Save to profile
      </v-btn>
      <v-btn variant="text" size="small" data-test="profile-new" @click="saving = !saving">
        Save as new profile
      </v-btn>
    </div>
    <div v-if="saving" class="d-flex align-center ga-2 mt-2">
      <v-text-field
        v-model="name"
        label="Profile name"
        density="compact"
        hide-details
        data-test="profile-name"
      />
      <v-btn color="primary" size="small" :disabled="!name.trim()" data-test="profile-save" @click="saveNew">
        Save
      </v-btn>
    </div>
    <p v-if="message" class="text-error text-caption mt-1" data-test="profile-message">{{ message }}</p>
  </div>
</template>
