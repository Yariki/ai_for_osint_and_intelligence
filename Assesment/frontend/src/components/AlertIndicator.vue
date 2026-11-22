<script setup lang="ts">
// The unread-alert indicator of the top bar, on every page (FR-HIS-8). It
// follows the read state, and a new alert raises it without a reload.
import { onMounted, onUnmounted } from 'vue'

import { useEventStream } from '../composables/useEventStream'
import { useAlertsStore } from '../stores/alerts'

const store = useAlertsStore()
const stream = useEventStream()

function load(): void {
  void store.fetchUnread().catch(() => undefined)
}

const stopEvents = stream.on(store.applyEvent)
// Events are not replayed: after a lost connection the count is loaded again.
const stopReconnect = stream.onReconnect(load)

onMounted(() => {
  load()
  stream.connect()
})
onUnmounted(() => {
  stopEvents()
  stopReconnect()
})
</script>

<template>
  <v-btn :to="{ name: 'alerts' }" variant="text" data-test="alert-indicator">
    <v-badge
      v-if="store.unread > 0"
      :content="store.unread > 99 ? '99+' : store.unread"
      color="error"
      data-test="alert-count"
    >
      <v-icon icon="mdi-bell" />
    </v-badge>
    <v-icon v-else icon="mdi-bell-outline" />
    <span class="ml-1">Alerts</span>
  </v-btn>
</template>
