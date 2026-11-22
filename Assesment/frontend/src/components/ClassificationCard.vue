<script setup lang="ts">
// The device type of the selected host: the automatic result with its
// confidence, the rules that fired and what each one matched, and the
// analyst's override. While an override is set, both are shown (FR-CLS-3, FR-CLS-5).
import { computed, ref } from 'vue'

import { ApiError } from '../api/client'
import { SUB_ROLES, typeLabel } from '../deviceTypes'
import { useWorkspaceStore } from '../stores/workspace'
import OverrideDialog from './OverrideDialog.vue'

const workspace = useWorkspaceStore()
const editing = ref(false)
const message = ref('')

const host = computed(() => workspace.selected)
const automatic = computed(() => host.value?.classification ?? null)

function evidence(found: unknown): string {
  const parts: string[] = []
  const matched = (found ?? {}) as Record<string, unknown>
  if (Array.isArray(matched.open_ports)) parts.push(`open ports ${matched.open_ports.join(', ')}`)
  if (Array.isArray(matched.ptr_names)) parts.push(`name ${matched.ptr_names.join(', ')}`)
  if (matched.seen_as_hop) parts.push('seen as a traceroute hop')
  if (matched.enrichment && typeof matched.enrichment === 'object') {
    for (const [field, value] of Object.entries(matched.enrichment)) parts.push(`${field} ${value}`)
  }
  return parts.join('; ')
}

async function run(action: () => Promise<unknown>): Promise<void> {
  message.value = ''
  try {
    await action()
    editing.value = false
  } catch (caught) {
    if (!(caught instanceof ApiError)) throw caught
    message.value = caught.message
  }
}
</script>

<template>
  <div v-if="host" data-test="classification">
    <v-alert v-if="message" type="error" density="compact" class="mb-2">{{ message }}</v-alert>

    <p v-if="host.type_override" data-test="classification-override">
      <strong>{{ typeLabel(host.type_override) }}</strong>, set by the analyst for this project.
    </p>
    <p data-test="classification-automatic" :class="{ 'text-medium-emphasis': host.type_override }">
      <template v-if="automatic">
        {{ host.type_override ? 'Automatic: ' : '' }}<strong>{{ typeLabel(automatic.device_type) }}</strong>
        <template v-if="automatic.sub_role"> ({{ SUB_ROLES[automatic.sub_role] ?? automatic.sub_role }})</template>,
        confidence {{ Math.round(automatic.confidence * 100) }}%, ruleset {{ automatic.ruleset_version }}.
      </template>
      <template v-else>Not classified yet.</template>
    </p>

    <ul v-if="automatic && automatic.rules.length" class="text-caption mb-2 ml-4" data-test="classification-rules">
      <li v-for="rule in automatic.rules" :key="String(rule.rule)">
        {{ rule.rule }} → {{ typeLabel(rule.type as string) }} ({{ Math.round(Number(rule.confidence) * 100) }}%):
        {{ evidence(rule.evidence) }}
      </li>
    </ul>
    <p v-else-if="automatic" class="text-caption" data-test="classification-no-rules">
      No rule matched: port numbers and names gave no evidence for a type.
    </p>

    <div class="d-flex ga-2 mb-4">
      <v-btn size="small" variant="tonal" data-test="override-open" @click="editing = true">
        {{ host.type_override ? 'Change type' : 'Set type' }}
      </v-btn>
      <v-btn
        v-if="host.type_override"
        size="small"
        variant="text"
        data-test="override-clear"
        @click="run(() => workspace.setOverride(null))"
      >
        Clear
      </v-btn>
    </div>

    <OverrideDialog
      v-model="editing"
      :current="host.type_override"
      :address="host.address"
      @save="(type) => run(() => workspace.setOverride(type))"
    />
  </div>
</template>
