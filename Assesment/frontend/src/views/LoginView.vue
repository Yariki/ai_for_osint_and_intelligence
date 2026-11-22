<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '../api/client'
import { useSessionStore } from '../stores/session'

const session = useSessionStore()
const router = useRouter()
const route = useRoute()

const username = ref('')
const password = ref('')
const message = ref('')
const busy = ref(false)
/** Seconds left during which the server refuses logins (too many failed attempts). */
const refusedFor = ref(0)

let timer: ReturnType<typeof setInterval> | undefined

function startCountdown(seconds: number): void {
  refusedFor.value = seconds
  clearInterval(timer)
  timer = setInterval(() => {
    refusedFor.value -= 1
    if (refusedFor.value <= 0) clearInterval(timer)
  }, 1000)
}

onUnmounted(() => clearInterval(timer))

async function submit(): Promise<void> {
  busy.value = true
  message.value = ''
  try {
    await session.login(username.value, password.value)
    const redirect = route.query.redirect
    // Only a path inside the application is followed.
    const target = typeof redirect === 'string' && redirect.startsWith('/') ? redirect : '/'
    await router.push(target)
  } catch (caught) {
    if (!(caught instanceof ApiError)) throw caught
    message.value = caught.message
    if (caught.status === 429) startCountdown(caught.retryAfter ?? 60)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <v-container max-width="420">
    <v-card title="Log in">
      <v-card-text>
        <v-alert v-if="refusedFor > 0" type="warning" class="mb-4" data-test="refused">
          Too many failed logins. Try again in {{ refusedFor }} seconds.
        </v-alert>
        <v-alert v-else-if="message" type="error" class="mb-4" data-test="error">
          {{ message }}
        </v-alert>
        <v-form @submit.prevent="submit">
          <v-text-field v-model="username" label="Username" autocomplete="username" />
          <v-text-field
            v-model="password"
            label="Password"
            type="password"
            autocomplete="current-password"
          />
          <v-btn
            type="submit"
            color="primary"
            :loading="busy"
            :disabled="!username || !password || refusedFor > 0"
          >
            Log in
          </v-btn>
        </v-form>
      </v-card-text>
    </v-card>
  </v-container>
</template>
