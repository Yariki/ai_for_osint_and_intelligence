<script setup lang="ts">
// Set the analyst's own device type for the address in this project. It
// applies in every scan of the project, later ones included (FR-CLS-5).
import { ref, watch } from 'vue'

import { DEVICE_TYPES } from '../deviceTypes'

const props = defineProps<{ modelValue: boolean; current: string | null; address: string }>()
const emit = defineEmits<{ 'update:modelValue': [open: boolean]; save: [deviceType: string] }>()

const choice = ref<string | null>(props.current)
watch(
  () => props.modelValue,
  (open) => {
    if (open) choice.value = props.current
  },
)
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="460" @update:model-value="emit('update:modelValue', $event)">
    <v-card :title="`Device type of ${address}`" data-test="override-dialog">
      <v-card-text>
        <v-select
          v-model="choice"
          :items="DEVICE_TYPES"
          label="Device type"
          density="compact"
          data-test="override-type"
        />
        <p class="text-caption">
          This applies to {{ address }} in every scan of the project, including later ones, until
          you clear it. The automatic classification stays visible beside it.
        </p>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn @click="emit('update:modelValue', false)">Cancel</v-btn>
        <v-btn
          color="primary"
          :disabled="!choice"
          data-test="override-save"
          @click="choice && emit('save', choice)"
        >
          Save
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
