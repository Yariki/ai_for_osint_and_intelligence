<script setup lang="ts">
// Hosts without coordinates. They cannot be drawn, and they are never dropped
// silently: they are listed here, beside the map (FR-MAP-3).
import type { ClusterHost } from '../composables/useMap'
import { useWorkspaceStore } from '../stores/workspace'
import PagedHostList from './PagedHostList.vue'

const workspace = useWorkspaceStore()

function select(host: ClusterHost): void {
  void workspace.select(host.address, host.scan_id)
}
</script>

<template>
  <v-card variant="outlined" data-test="unlocated-panel">
    <v-card-title class="text-subtitle-1">
      Without a location: {{ workspace.unlocated.length.toLocaleString('en') }}
    </v-card-title>
    <v-card-text>
      <p v-if="workspace.unlocated.length === 0" class="text-caption">
        Every host that is shown has a location.
      </p>
      <template v-else>
        <p class="text-caption mb-2">
          No location is known for these hosts, so they are not on the map.
        </p>
        <PagedHostList :hosts="workspace.unlocated" :page-size="8" @select="select" />
      </template>
    </v-card-text>
  </v-card>
</template>
