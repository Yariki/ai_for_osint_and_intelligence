<script setup lang="ts">
// The address across the scans of its project: when it answered, which ports
// were open, and what changed from one scan to the next (FR-MAP-9).
import { computed } from 'vue'

import type { Schemas } from '../api/client'

type Entry = Schemas['TimelineEntryOut']

const props = defineProps<{
  entries: Entry[]
  /** The scan the detail is opened in. Its entry is marked. */
  current: number
  firstSeenAt: string | null
  lastSeenAt: string | null
}>()

/** When a scan took place: its start, or for a scan that never started, nothing. */
function when(entry: Entry): number {
  return entry.started_at ? Date.parse(entry.started_at) : Number.POSITIVE_INFINITY
}

/** Oldest first. Scans that have not started yet come last, in the order they were created. */
const ordered = computed(() =>
  [...props.entries].sort((a, b) => when(a) - when(b) || a.scan_id - b.scan_id),
)

function date(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '—'
}
</script>

<template>
  <div data-test="timeline">
    <p class="text-caption mb-1" data-test="timeline-seen">
      First seen in this project: {{ date(firstSeenAt) }}. Last seen: {{ date(lastSeenAt) }}.
    </p>
    <v-table density="compact">
      <thead>
        <tr>
          <th>Scan</th>
          <th>Started</th>
          <th>Result</th>
          <th>Open ports</th>
          <th>Changes</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="entry in ordered"
          :key="entry.scan_id"
          data-test="timeline-entry"
          :class="{ 'font-weight-bold': entry.scan_id === current }"
        >
          <td>
            <router-link :to="{ name: 'scan', params: { id: entry.scan_id } }">
              #{{ entry.scan_id }}
            </router-link>
          </td>
          <td>{{ date(entry.started_at) }}</td>
          <td>{{ entry.reachable ? 'answered' : 'no answer' }} ({{ entry.state }})</td>
          <td>{{ entry.open_ports.join(', ') || '—' }}</td>
          <td>
            <span v-if="entry.opened.length" data-test="timeline-opened">
              opened: {{ entry.opened.join(', ') }}
            </span>
            <span v-if="entry.closed.length" data-test="timeline-closed">
              {{ entry.opened.length ? '; ' : '' }}closed: {{ entry.closed.join(', ') }}
            </span>
            <span v-if="!entry.opened.length && !entry.closed.length">—</span>
          </td>
        </tr>
      </tbody>
    </v-table>
    <p class="text-caption">
      A change is reported only for ports that both scans probed.
    </p>
  </div>
</template>
