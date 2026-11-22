<script setup lang="ts">
// The attestation of FR-PRJ-4: the analyst confirms that they are authorized
// to scan exactly the ranges listed here.
import { ref, watch } from 'vue'

const open = defineModel<boolean>({ required: true })
defineProps<{ entries: string[] }>()
const emit = defineEmits<{ confirm: [] }>()

const authorized = ref(false)

// The box is never ticked in advance, not even when the dialog is opened again.
watch(open, () => {
  authorized.value = false
})

function confirm(): void {
  if (!authorized.value) return
  open.value = false
  emit('confirm')
}
</script>

<template>
  <v-dialog v-model="open" max-width="560">
    <v-card title="Confirm authorization">
      <v-card-text>
        <p class="mb-2">You are about to save this scope:</p>
        <ul class="mb-4 ml-6" data-test="attest-entries">
          <li v-for="(entry, index) in entries" :key="index">
            <code>{{ entry }}</code>
          </li>
          <li v-if="entries.length === 0"><em>No entries: the scope will be empty.</em></li>
        </ul>
        <v-checkbox
          v-model="authorized"
          data-test="attest-check"
          label="I confirm that I am authorized to scan the listed ranges."
          hide-details
        />
        <p class="text-caption">
          The confirmation is stored with this exact scope, your account and the time.
        </p>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn data-test="attest-cancel" @click="open = false">Cancel</v-btn>
        <v-btn color="primary" data-test="attest-confirm" :disabled="!authorized" @click="confirm">
          Save scope
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
