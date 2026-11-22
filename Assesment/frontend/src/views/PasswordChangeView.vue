<script setup lang="ts">
import { ref } from 'vue'

import { ApiError } from '../api/client'
import { useSessionStore } from '../stores/session'

const session = useSessionStore()

const current = ref('')
const next = ref('')
const repeated = ref('')
const error = ref<ApiError | null>(null)
const changed = ref(false)
const busy = ref(false)

async function submit(): Promise<void> {
  busy.value = true
  error.value = null
  changed.value = false
  try {
    await session.changePassword(current.value, next.value)
    changed.value = true
    current.value = next.value = repeated.value = ''
  } catch (caught) {
    if (!(caught instanceof ApiError)) throw caught
    error.value = caught
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <v-container max-width="520">
    <v-card title="Change password">
      <v-card-text>
        <v-alert v-if="changed" type="success" class="mb-4">
          The password was changed. Every other session of this account has been ended.
        </v-alert>
        <v-alert v-if="error && error.details.length === 0" type="error" class="mb-4">
          {{ error.message }}
        </v-alert>
        <v-form @submit.prevent="submit">
          <v-text-field
            v-model="current"
            label="Current password"
            type="password"
            autocomplete="current-password"
          />
          <v-text-field
            v-model="next"
            label="New password"
            type="password"
            autocomplete="new-password"
            hint="At least 12 characters"
            persistent-hint
            :error-messages="error?.fieldMessage('new_password')"
          />
          <v-text-field
            v-model="repeated"
            label="Repeat new password"
            type="password"
            autocomplete="new-password"
            :error-messages="repeated && repeated !== next ? 'The passwords differ.' : ''"
          />
          <v-btn
            type="submit"
            color="primary"
            :loading="busy"
            :disabled="!current || !next || repeated !== next"
          >
            Change password
          </v-btn>
        </v-form>
      </v-card-text>
    </v-card>
  </v-container>
</template>
