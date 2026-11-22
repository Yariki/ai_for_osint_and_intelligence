<script setup lang="ts">
// The traceroutes of the selected host, newest first, each with how it was
// made (FR-TRC-7). Selecting one opens its hops and draws its path.
import { useTraceroutesStore } from '../stores/traceroutes'

const store = useTraceroutesStore()

function describe(run: (typeof store.history)[number]): string {
  const parameters = run.parameters
  const port = parameters.port === null ? '' : ` port ${parameters.port}`
  return (
    `${run.method.toUpperCase()}${port}, ${parameters.max_hops} hops, ` +
    `${parameters.probes_per_hop} probes per hop, ${parameters.timeout_ms} ms` +
    (run.vantage_point ? `, from ${run.vantage_point}` : '')
  )
}
</script>

<template>
  <div data-test="traceroute-history">
    <p v-if="store.history.length === 0" class="text-caption">No traceroute yet.</p>
    <v-list v-else density="compact" class="pa-0">
      <v-list-item
        v-for="run in store.history"
        :key="run.id"
        :active="store.current?.id === run.id"
        data-test="traceroute-run"
        @click="store.open(run.id)"
      >
        <v-list-item-title>
          {{ new Date(run.created_at).toLocaleString() }}: {{ run.state }}
        </v-list-item-title>
        <v-list-item-subtitle>{{ describe(run) }}</v-list-item-subtitle>
      </v-list-item>
    </v-list>
  </div>
</template>
