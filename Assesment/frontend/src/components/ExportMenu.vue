<script setup lang="ts">
// Download what a view shows as CSV or JSON (FR-EXP-1 to FR-EXP-3).
//
// The links point at the same endpoint the view reads, with the same filters
// and order, plus the format. The browser downloads with the session cookie,
// so an export needs a session like every other request.
import { computed } from 'vue'

type Value = string | number | boolean | null | undefined

const props = defineProps<{
  /** The API path without its /api/v1 prefix, such as /scans/3/hosts. */
  path: string
  query?: Record<string, Value>
}>()

function link(format: 'csv' | 'json'): string {
  const search = new URLSearchParams()
  for (const [name, value] of Object.entries(props.query ?? {})) {
    if (value !== null && value !== undefined && value !== '') search.set(name, String(value))
  }
  search.set('format', format)
  return `/api/v1${props.path}?${search.toString()}`
}

const links = computed(() => ({ csv: link('csv'), json: link('json') }))
</script>

<template>
  <v-menu>
    <template #activator="{ props: activator }">
      <v-btn v-bind="activator" variant="text" size="small" prepend-icon="mdi-download" data-test="export">
        Export
      </v-btn>
    </template>
    <v-list density="compact">
      <v-list-item :href="links.csv" download title="CSV" data-test="export-csv" />
      <v-list-item :href="links.json" download title="JSON" data-test="export-json" />
    </v-list>
  </v-menu>
</template>
