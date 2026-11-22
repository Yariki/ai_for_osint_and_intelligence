<script setup lang="ts">
// The hosts of a cluster that zooming cannot take apart: hosts that share one
// coordinate. City-level location puts most addresses of a range on the same
// point, so this is the normal case, not an edge case (FR-MAP-2).
import type { ClusterHost } from '../composables/useMap'
import PagedHostList from './PagedHostList.vue'

defineProps<{ hosts: ClusterHost[] }>()
const emit = defineEmits<{ select: [host: ClusterHost]; close: [] }>()
</script>

<template>
  <v-dialog
    :model-value="hosts.length > 0"
    max-width="460"
    scrollable
    @update:model-value="emit('close')"
  >
    <v-card data-test="cluster-list">
      <v-card-title>{{ hosts.length.toLocaleString('en') }} hosts at this location</v-card-title>
      <v-card-subtitle>
        The location is approximate. These hosts share it; they are not in one place.
      </v-card-subtitle>
      <v-card-text>
        <PagedHostList :hosts="hosts" @select="emit('select', $event)" />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" data-test="cluster-list-close" @click="emit('close')">Close</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
