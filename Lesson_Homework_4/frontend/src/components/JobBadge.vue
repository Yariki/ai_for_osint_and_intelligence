<script setup lang="ts">
import type { JobStatus } from '../types'
defineProps<{ status: JobStatus }>()
const colors: Record<JobStatus, string> = {
  successful: 'success',
  failed: 'error',
  blocked: 'error',
  partial: 'warning',
  queued: 'secondary',
  running: 'primary',
  retrying: 'warning',
}
</script>
<template>
  <v-chip :color="colors[status]" size="x-small" label class="text-capitalize">{{ status }}</v-chip>
</template>
