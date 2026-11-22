<script setup lang="ts">
import { useRouter } from 'vue-router'

import AlertIndicator from './components/AlertIndicator.vue'
import { useEventStream } from './composables/useEventStream'
import { useSessionStore } from './stores/session'

const session = useSessionStore()
const router = useRouter()

async function logout(): Promise<void> {
  // The live stream belongs to the session.
  useEventStream().close()
  await session.logout()
  await router.push({ name: 'login' })
}
</script>

<template>
  <v-app>
    <v-app-bar title="Network Discovery Map">
      <template v-if="session.isAuthenticated">
        <v-btn :to="{ name: 'projects' }" variant="text">Projects</v-btn>
        <v-btn :to="{ name: 'audit' }" variant="text">Audit log</v-btn>
        <v-btn :to="{ name: 'datasets' }" variant="text">Datasets</v-btn>
        <v-spacer />
        <AlertIndicator />
        <span class="mx-4" data-test="username">{{ session.username }}</span>
        <v-btn :to="{ name: 'password' }" variant="text">Password</v-btn>
        <v-btn variant="text" data-test="logout" @click="logout">Log out</v-btn>
      </template>
    </v-app-bar>
    <v-main>
      <router-view />
    </v-main>
  </v-app>
</template>
