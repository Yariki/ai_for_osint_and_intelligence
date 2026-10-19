<script setup lang="ts">
import { reactive, ref } from 'vue'
import type { Source, SourceDraft } from '../types'
const props = defineProps<{ source?: Source; defaultPeriod: number; busy: boolean }>()
const emit = defineEmits<{ save: [draft: SourceDraft]; cancel: [] }>()
const draft = reactive<SourceDraft>({
  name: props.source?.name ?? '',
  url: props.source?.url ?? '',
  period_minutes: props.source?.period_minutes ?? props.defaultPeriod,
  enabled: props.source?.enabled ?? true,
})
const error = ref('')
function submit() {
  if (!draft.name.trim()) {
    error.value = 'Enter a source name.'
    return
  }
  try {
    const url = new URL(draft.url)
    if (
      !['http:', 'https:'].includes(url.protocol) ||
      url.username ||
      url.password ||
      (url.port && !['80', '443'].includes(url.port))
    )
      throw new Error()
  } catch {
    error.value = 'Enter an HTTP or HTTPS feed URL without credentials, using a standard port.'
    return
  }
  const period = Number(draft.period_minutes)
  if (!Number.isInteger(period) || period < 1 || period > 525600) {
    error.value = 'Period must be a whole number between 1 and 525600 minutes.'
    return
  }
  error.value = ''
  emit('save', { ...draft, name: draft.name.trim(), url: draft.url.trim(), period_minutes: period })
}
</script>
<template>
  <v-card class="pa-6" min-width="0">
    <h2 class="section-label mb-5">{{ source ? 'Edit source' : 'Add RSS source' }}</h2>
    <form novalidate @submit.prevent="submit">
      <v-alert v-if="error" type="error" data-test="form-error">{{ error }}</v-alert>
      <v-text-field
        v-model="draft.name"
        label="Source name"
        maxlength="200"
        data-test="source-name"
      />
      <v-text-field
        v-model="draft.url"
        label="Feed URL"
        placeholder="https://example.com/feed.xml"
        :disabled="!!source"
        data-test="source-url"
        hint="Public RSS and Atom feeds are supported."
        persistent-hint
        class="mb-4"
      />
      <v-text-field
        v-model="draft.period_minutes"
        label="Check every (minutes)"
        type="number"
        min="1"
        max="525600"
        data-test="source-period"
      />
      <v-switch
        v-if="source"
        v-model="draft.enabled"
        color="success"
        label="Scheduled crawling enabled"
        hide-details
        class="mb-4"
      />
      <div class="d-flex justify-end ga-2">
        <v-btn variant="text" :disabled="busy" @click="emit('cancel')">Cancel</v-btn>
        <v-btn color="primary" type="submit" :loading="busy">
          {{ source ? 'Save changes' : 'Add source' }}
        </v-btn>
      </div>
    </form>
  </v-card>
</template>
