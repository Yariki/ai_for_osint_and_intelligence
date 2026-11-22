<script setup lang="ts">
// The filters of the host table and the map. The bar only edits them; they
// live in the URL, and the server applies them to both.
import { reactive, watch } from 'vue'

import { DEVICE_TYPES } from '../deviceTypes'
import { type ChangeStatus, type HostFilters } from '../stores/workspace'

const props = defineProps<{
  filters: HostFilters
  /** False in the project view: a change status is relative to one scan. */
  changeAvailable?: boolean
}>()
const emit = defineEmits<{ change: [filters: HostFilters] }>()

const CHANGES: Array<{ value: ChangeStatus; title: string }> = [
  { value: 'changed', title: 'Changed since the previous scan' },
  { value: 'appeared', title: 'Appeared' },
  { value: 'disappeared', title: 'Disappeared' },
  { value: 'unchanged', title: 'Unchanged' },
]

const form = reactive({
  open_port: '',
  asn: '',
  organization: '',
  country: '',
  ptr: '',
  device_type: null as string | null,
  tag: '',
  change: null as ChangeStatus | null,
})

// The URL is the source: when it changes, the fields follow.
watch(
  () => props.filters,
  (filters) => {
    form.open_port = filters.open_port?.toString() ?? ''
    form.asn = filters.asn?.toString() ?? ''
    form.organization = filters.organization ?? ''
    form.country = filters.country ?? ''
    form.ptr = filters.ptr ?? ''
    form.device_type = filters.device_type ?? null
    form.tag = filters.tag ?? ''
    form.change = filters.change ?? null
  },
  { immediate: true },
)

function apply(): void {
  const filters: HostFilters = {}
  if (/^\d+$/.test(form.open_port.trim())) filters.open_port = Number(form.open_port)
  if (/^\d+$/.test(form.asn.trim())) filters.asn = Number(form.asn)
  if (form.organization.trim()) filters.organization = form.organization.trim()
  if (form.country.trim()) filters.country = form.country.trim()
  if (form.ptr.trim()) filters.ptr = form.ptr.trim()
  if (form.device_type) filters.device_type = form.device_type
  if (form.tag.trim()) filters.tag = form.tag.trim()
  if (form.change) filters.change = form.change
  emit('change', filters)
}

function clear(): void {
  emit('change', {})
}
</script>

<template>
  <v-form class="d-flex flex-wrap align-center ga-2 mb-3" data-test="filter-bar" @submit.prevent="apply">
    <v-text-field
      v-model="form.open_port"
      label="Open port"
      density="compact"
      hide-details
      style="max-width: 130px"
      data-test="filter-open_port"
    />
    <v-text-field
      v-model="form.asn"
      label="ASN"
      density="compact"
      hide-details
      style="max-width: 130px"
      data-test="filter-asn"
    />
    <v-text-field
      v-model="form.organization"
      label="Organization contains"
      density="compact"
      hide-details
      style="max-width: 220px"
      data-test="filter-organization"
    />
    <v-text-field
      v-model="form.country"
      label="Country code"
      density="compact"
      hide-details
      maxlength="2"
      style="max-width: 130px"
      data-test="filter-country"
    />
    <v-text-field
      v-model="form.ptr"
      label="Reverse DNS contains"
      density="compact"
      hide-details
      style="max-width: 220px"
      data-test="filter-ptr"
    />
    <v-select
      v-model="form.device_type"
      :items="DEVICE_TYPES"
      label="Device type"
      density="compact"
      hide-details
      clearable
      style="max-width: 230px"
      data-test="filter-device_type"
    />
    <v-text-field
      v-model="form.tag"
      label="Tag"
      density="compact"
      hide-details
      style="max-width: 150px"
      data-test="filter-tag"
    />
    <v-select
      v-if="changeAvailable !== false"
      v-model="form.change"
      :items="CHANGES"
      label="Change status"
      density="compact"
      hide-details
      clearable
      style="max-width: 260px"
      data-test="filter-change"
    />
    <v-btn type="submit" color="primary" data-test="filter-apply">Filter</v-btn>
    <v-btn
      v-if="Object.keys(filters).length > 0"
      variant="text"
      data-test="filter-clear"
      @click="clear"
    >
      Clear
    </v-btn>
  </v-form>
</template>
