<script setup lang="ts">
// The hops of the open traceroute, one row per TTL, as they arrive
// (FR-TRC-3, FR-TRC-4, FR-TRC-6).
import { computed } from 'vue'

import { useTraceroutesStore } from '../stores/traceroutes'
import ExportMenu from './ExportMenu.vue'

const store = useTraceroutesStore()

const summary = computed(() => {
  const run = store.current
  if (!run) return ''
  const reached = run.reached === true ? ', target reached' : run.reached === false ? ', target not reached' : ''
  const reason = run.failure_reason ? ` (${run.failure_reason})` : ''
  return `${run.method.toUpperCase()} to ${run.target}: ${run.state}${reached}${reason}.`
})

function times(rtts: Array<number | null>): string {
  return rtts.map((rtt) => (rtt === null ? '*' : `${rtt.toFixed(1)} ms`)).join('  ')
}
</script>

<template>
  <div v-if="store.current" data-test="traceroute-hops">
    <div class="d-flex align-center">
      <p class="text-caption mb-1" data-test="traceroute-state">{{ summary }}</p>
      <v-spacer />
      <ExportMenu :path="`/traceroutes/${store.current.id}`" />
    </div>
    <p v-if="store.current.hops.length === 0" class="text-caption">No hop yet.</p>
    <v-table v-else density="compact">
      <thead>
        <tr>
          <th>TTL</th>
          <th>Address</th>
          <th>Times</th>
          <th>Lost</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="hop in store.current.hops" :key="hop.ttl">
          <tr v-if="hop.silent" data-test="hop-row">
            <td>{{ hop.ttl }}</td>
            <td colspan="2" class="font-italic" data-test="hop-silent">no answer</td>
            <td>{{ hop.lost }}</td>
          </tr>
          <tr
            v-for="(responder, index) in hop.responders"
            v-else
            :key="`${hop.ttl}-${responder.address}`"
            data-test="hop-row"
          >
            <td>{{ index === 0 ? hop.ttl : '' }}</td>
            <td>
              {{ responder.address }}
              <v-chip v-if="responder.unlocatable" size="x-small" class="ml-1" data-test="hop-unlocatable">
                private: no location
              </v-chip>
              <div v-if="responder.ptr_names.length" class="text-caption">
                {{ responder.ptr_names.join(', ') }}
              </div>
              <div v-if="responder.city || responder.country" class="text-caption">
                {{ [responder.city, responder.country].filter(Boolean).join(', ') }} (approximate)
                <template v-if="responder.as_holder"> · {{ responder.as_holder }}</template>
              </div>
            </td>
            <td>{{ times(responder.rtt_ms) }}</td>
            <td>{{ index === 0 ? hop.lost : '' }}</td>
          </tr>
        </template>
      </tbody>
    </v-table>
  </div>
</template>
