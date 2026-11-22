<script setup lang="ts">
// The alert list (FR-HIS-7, FR-HIS-8): changes found by scheduled scans,
// failed, skipped and rejected runs, and messages that could not be
// processed. Opening an alert marks it read and goes where it points.
import { onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError } from '../api/client'
import { type Alert, alertRoute, useAlertsStore } from '../stores/alerts'

const store = useAlertsStore()
const router = useRouter()

const unreadOnly = ref(false)
const message = ref('')

async function run(action: () => Promise<unknown>): Promise<void> {
  message.value = ''
  try {
    await action()
  } catch (caught) {
    if (!(caught instanceof ApiError)) throw caught
    message.value = caught.message
  }
}

watch(unreadOnly, (only) => run(() => store.fetchAlerts(only)), { immediate: true })
onUnmounted(store.forgetList)

const LABELS: Record<string, string> = {
  changes_detected: 'Changes',
  scheduled_run_failed: 'Run failed',
  scheduled_run_skipped: 'Run skipped',
  scheduled_run_rejected: 'Run rejected',
  message_dead_lettered: 'Message lost',
}

async function open(alert: Alert): Promise<void> {
  await run(() => store.markRead(alert.id))
  const target = alertRoute(alert)
  if (target) await router.push(target)
}
</script>

<template>
  <v-container>
    <div class="d-flex align-center mb-4">
      <h1 class="text-h5">Alerts</h1>
      <v-spacer />
      <v-switch
        v-model="unreadOnly"
        label="Unread only"
        density="compact"
        hide-details
        class="mr-4"
        data-test="alerts-unread-only"
      />
      <v-btn :disabled="store.unread === 0" data-test="alerts-read-all" @click="run(store.markAllRead)">
        Mark all read
      </v-btn>
    </div>
    <v-alert v-if="message" type="error" class="mb-4">{{ message }}</v-alert>

    <p v-if="store.alerts.length === 0" data-test="alerts-empty">No alerts.</p>
    <v-list v-else lines="two" data-test="alerts">
      <v-list-item
        v-for="alert in store.alerts"
        :key="alert.id"
        :data-test="`alert-${alert.id}`"
        :class="{ 'font-weight-bold': !alert.read_at }"
        @click="open(alert)"
      >
        <v-list-item-title>
          <v-chip size="x-small" class="mr-2">{{ LABELS[alert.type] ?? alert.type }}</v-chip>
          {{ alert.summary }}
        </v-list-item-title>
        <v-list-item-subtitle>
          {{ new Date(alert.created_at).toLocaleString() }}
          <template v-if="alert.project_name"> · {{ alert.project_name }}</template>
          <template v-if="!alert.read_at"> · unread</template>
        </v-list-item-subtitle>
      </v-list-item>
    </v-list>
  </v-container>
</template>
