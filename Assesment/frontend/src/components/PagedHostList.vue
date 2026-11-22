<script setup lang="ts">
// A list of hosts that can be long: a cluster of one city, or every host
// without a location. It is shown a page at a time, and can be narrowed by
// a part of the address. Every host of the list can be reached and selected.
import { computed, ref, watch } from 'vue'

import type { ClusterHost } from '../composables/useMap'

const props = withDefaults(defineProps<{ hosts: ClusterHost[]; pageSize?: number }>(), {
  pageSize: 50,
})
const emit = defineEmits<{ select: [host: ClusterHost] }>()

const search = ref('')
const page = ref(1)

const matching = computed(() =>
  search.value.trim()
    ? props.hosts.filter((host) => host.address.includes(search.value.trim()))
    : props.hosts,
)
const pages = computed(() => Math.max(1, Math.ceil(matching.value.length / props.pageSize)))
const shown = computed(() =>
  matching.value.slice((page.value - 1) * props.pageSize, page.value * props.pageSize),
)

watch([search, () => props.hosts], () => (page.value = 1))
</script>

<template>
  <div>
    <v-text-field
      v-if="hosts.length > pageSize"
      v-model="search"
      label="Address contains"
      density="compact"
      hide-details
      clearable
      class="mb-2"
      data-test="host-list-search"
    />
    <v-list density="compact" class="pa-0">
      <v-list-item
        v-for="host in shown"
        :key="host.address"
        data-test="host-list-item"
        @click="emit('select', host)"
      >
        <v-list-item-title>{{ host.address }}</v-list-item-title>
        <template #append>
          <span class="text-caption">{{ host.open_port_count }} open</span>
        </template>
      </v-list-item>
    </v-list>
    <v-pagination
      v-if="pages > 1"
      v-model="page"
      :length="pages"
      density="compact"
      total-visible="5"
      data-test="host-list-pages"
    />
  </div>
</template>
