<script setup lang="ts">
// Start a traceroute to the selected host (FR-TRC-1, FR-TRC-2). The server
// checks the target against the project scope and may refuse it.
import { reactive, ref, watch } from 'vue'

import { ApiError } from '../api/client'
import { type Traceroute, useTraceroutesStore } from '../stores/traceroutes'

const props = defineProps<{ modelValue: boolean; projectId: number; address: string }>()
const emit = defineEmits<{
  'update:modelValue': [open: boolean]
  started: [run: Traceroute]
}>()

const store = useTraceroutesStore()
const form = reactive({
  method: 'icmp' as 'icmp' | 'tcp' | 'udp',
  port: '',
  max_hops: 30,
  probes_per_hop: 3,
  timeout_ms: 1000,
})
const message = ref('')
const fieldMessages = ref<Record<string, string>>({})
const busy = ref(false)

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      message.value = ''
      fieldMessages.value = {}
    }
  },
)

async function submit(): Promise<void> {
  busy.value = true
  message.value = ''
  fieldMessages.value = {}
  try {
    const port = form.method !== 'icmp' && form.port.trim() ? Number(form.port) : undefined
    const run = await store.start(props.projectId, {
      target: props.address,
      method: form.method,
      port: port ?? null,
      max_hops: Number(form.max_hops),
      probes_per_hop: Number(form.probes_per_hop),
      timeout_ms: Number(form.timeout_ms),
    })
    emit('started', run)
    emit('update:modelValue', false)
  } catch (caught) {
    if (!(caught instanceof ApiError)) throw caught
    message.value = caught.message
    for (const detail of caught.details) {
      if (detail.field) fieldMessages.value[detail.field] = detail.message
    }
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <v-dialog
    :model-value="modelValue"
    max-width="520"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card :title="`Traceroute to ${address}`" data-test="traceroute-dialog">
      <v-card-text>
        <v-alert v-if="message" type="error" class="mb-3" data-test="traceroute-error">
          {{ message }}
          <div v-for="(text, field) in fieldMessages" :key="field">{{ text }}</div>
        </v-alert>
        <v-btn-toggle v-model="form.method" mandatory class="mb-4" data-test="traceroute-method">
          <v-btn value="icmp">ICMP</v-btn>
          <v-btn value="tcp">TCP</v-btn>
          <v-btn value="udp">UDP</v-btn>
        </v-btn-toggle>
        <v-text-field
          v-if="form.method !== 'icmp'"
          v-model="form.port"
          :label="form.method === 'tcp' ? 'Port (default: an open port of the host)' : 'First port (default 33434)'"
          density="compact"
          data-test="traceroute-port"
        />
        <div class="d-flex ga-2">
          <v-text-field v-model="form.max_hops" label="Maximum hops" type="number" density="compact" />
          <v-text-field v-model="form.probes_per_hop" label="Probes per hop" type="number" density="compact" />
          <v-text-field v-model="form.timeout_ms" label="Timeout (ms)" type="number" density="compact" />
        </div>
        <p class="text-caption">
          Probes go to {{ address }} only. Routers on the way are seen through their answers and
          are never probed themselves.
        </p>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn @click="emit('update:modelValue', false)">Cancel</v-btn>
        <v-btn color="primary" :loading="busy" data-test="traceroute-start" @click="submit">
          Start
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
