<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError } from '../api/client'
import { useSessionStore } from '../stores/session'

const session = useSessionStore()
const router = useRouter()

const token = ref('')
const username = ref('')
const password = ref('')
const repeated = ref('')
const error = ref<ApiError | null>(null)
const busy = ref(false)

async function submit(): Promise<void> {
  busy.value = true
  error.value = null
  try {
    await session.setup(token.value, username.value, password.value)
    await router.push({ name: 'login' })
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
    <v-card title="First-run setup">
      <v-card-text>
        <p class="mb-4">
          No account exists yet. Create the analyst account with the one-time setup token that the
          API printed to its log when it started.
        </p>
        <v-alert v-if="error && error.details.length === 0" type="error" class="mb-4">
          {{ error.message }}
        </v-alert>
        <v-form @submit.prevent="submit">
          <v-text-field
            v-model="token"
            label="Setup token"
            autocomplete="off"
            :error-messages="error?.fieldMessage('token')"
          />
          <v-text-field
            v-model="username"
            label="Username"
            autocomplete="username"
            :error-messages="error?.fieldMessage('username')"
          />
          <v-text-field
            v-model="password"
            label="Password"
            type="password"
            autocomplete="new-password"
            hint="At least 12 characters"
            persistent-hint
            :error-messages="error?.fieldMessage('password')"
          />
          <v-text-field
            v-model="repeated"
            label="Repeat password"
            type="password"
            autocomplete="new-password"
            :error-messages="repeated && repeated !== password ? 'The passwords differ.' : ''"
          />
          <v-btn
            type="submit"
            color="primary"
            :loading="busy"
            :disabled="!token || !username || !password || repeated !== password"
          >
            Create account
          </v-btn>
        </v-form>
      </v-card-text>
    </v-card>
  </v-container>
</template>
