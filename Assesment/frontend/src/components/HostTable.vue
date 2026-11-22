<script setup lang="ts">
// The host table of the workspace. Filtering, sorting and paging are done by
// the server; the rows are updated live by the workspace store.
import { computed } from 'vue'

import { typeLabel } from '../deviceTypes'
import ExportMenu from './ExportMenu.vue'
import { PAGE_SIZE, type SortKey, useWorkspaceStore } from '../stores/workspace'

const workspace = useWorkspaceStore()

const columns: Array<{ key: SortKey | null; title: string }> = [
  { key: 'address', title: 'Address' },
  { key: 'open_ports', title: 'Open ports' },
  { key: 'device_type', title: 'Device type' },
  { key: 'country', title: 'Location (approximate)' },
  { key: 'asn', title: 'ASN' },
  { key: 'organization', title: 'Organization' },
  { key: null, title: 'Reverse DNS' },
  { key: 'closed', title: 'Closed' },
  { key: 'filtered', title: 'Filtered' },
]

const pages = computed(() => Math.max(1, Math.ceil(workspace.total / PAGE_SIZE)))

const summary = computed(() => {
  const count = `${workspace.total.toLocaleString('en')} hosts`
  return workspace.filtersActive ? `${count} match the filters.` : `${count}.`
})

function sortBy(key: SortKey | null): void {
  if (key === null) return
  const flip = workspace.sort === key && workspace.order === 'asc'
  void workspace.setSort(key, flip ? 'desc' : 'asc')
}

function place(host: { city: string | null; country: string | null }): string {
  return [host.city, host.country].filter(Boolean).join(', ') || '—'
}
</script>

<template>
  <div>
    <div class="d-flex align-center">
      <p class="text-caption mb-1" data-test="hosts-total">{{ summary }}</p>
      <v-spacer />
      <ExportMenu
        v-if="workspace.exportTarget && workspace.total > 0"
        :path="workspace.exportTarget.path"
        :query="workspace.exportTarget.query"
      />
    </div>
    <p v-if="workspace.hosts.length === 0" data-test="hosts-empty">
      {{ workspace.filtersActive ? 'No host matches the filters.' : 'No hosts discovered yet.' }}
    </p>
    <v-table v-else density="compact" hover>
      <thead>
        <tr>
          <th
            v-for="column in columns"
            :key="column.title"
            :style="column.key ? 'cursor: pointer' : ''"
            :data-test="`sort-${column.key ?? 'none'}`"
            @click="sortBy(column.key)"
          >
            {{ column.title }}
            <span v-if="column.key && workspace.sort === column.key">
              {{ workspace.order === 'asc' ? '▲' : '▼' }}
            </span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="host in workspace.hosts"
          :key="host.address"
          data-test="host-row"
          :class="{ 'bg-grey-lighten-3': workspace.selected?.address === host.address }"
          style="cursor: pointer"
          @click="workspace.select(host.address, host.scan_id)"
        >
          <td>{{ host.address }}</td>
          <td>{{ host.open_port_count }}</td>
          <td data-test="host-type">
            {{ typeLabel(host.effective_type) }}{{ host.type_override ? ' (set)' : '' }}
            <v-chip v-for="tag in host.tags" :key="tag" size="x-small" class="ml-1">{{ tag }}</v-chip>
          </td>
          <td>{{ place(host) }}</td>
          <td>{{ host.asn ?? '—' }}</td>
          <td>{{ host.organization ?? '—' }}</td>
          <td>{{ host.ptr_names.join(', ') || '—' }}</td>
          <td>{{ host.closed_count }}</td>
          <td>{{ host.filtered_count }}</td>
        </tr>
      </tbody>
    </v-table>
    <v-pagination
      v-if="pages > 1"
      :model-value="workspace.page"
      :length="pages"
      class="mt-2"
      @update:model-value="workspace.setPage"
    />
  </div>
</template>
