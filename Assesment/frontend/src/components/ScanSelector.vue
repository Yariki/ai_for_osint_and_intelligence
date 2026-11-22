<script setup lang="ts">
// Chooses what the workspace shows: one scan of the project, or the latest
// known state of the whole project.
import { computed } from 'vue'

import type { Scan } from '../stores/workspace'

const props = defineProps<{ scans: Scan[]; selected: number | 'project' | null }>()
const emit = defineEmits<{ select: [selection: number | 'project'] }>()

const PROJECT = 'project'

const items = computed(() => [
  { value: PROJECT as number | string, title: 'Whole project: latest state of every address' },
  ...props.scans.map((scan, index) => ({
    value: scan.id as number | string,
    title:
      `Scan #${scan.id}, ${new Date(scan.created_at).toLocaleString()}, ${scan.state}` +
      (index === 0 ? ' (latest)' : ''),
  })),
])
</script>

<template>
  <v-select
    :model-value="selected"
    :items="items"
    label="Shown"
    density="compact"
    hide-details
    style="max-width: 460px"
    data-test="scan-selector"
    @update:model-value="emit('select', $event as number | 'project')"
  />
</template>
