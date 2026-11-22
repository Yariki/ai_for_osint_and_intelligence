<script setup lang="ts">
// The diff of two scans (FR-HIS-2 to FR-HIS-5): what changed, by kind; which
// ports were compared; which addresses only one scan targeted; and which
// enrichment changes coincide with a new dataset version.
//
// Either a stored automatic diff (/diffs/:id, the target of a change alert)
// or two scans of a project chosen here, compared now and not stored.
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api, ApiError, type Schemas } from '../api/client'
import ExportMenu from '../components/ExportMenu.vue'
import { FINAL_STATES, useScansStore } from '../stores/scans'

type Diff = Schemas['DiffOut']
type Change = Schemas['ChangeOut']

const props = defineProps<{ diffId?: number; projectId?: number }>()

const route = useRoute()
const router = useRouter()
const scans = useScansStore()

const diff = ref<Diff | null>(null)
const message = ref('')

const KINDS: Array<{ kind: string; label: string }> = [
  { kind: 'host_appeared', label: 'Hosts appeared' },
  { kind: 'host_disappeared', label: 'Hosts disappeared' },
  { kind: 'port_opened', label: 'Ports opened' },
  { kind: 'port_closed', label: 'Ports closed' },
  { kind: 'ptr_changed', label: 'Reverse DNS changed' },
  { kind: 'classification_changed', label: 'Classification changed' },
  { kind: 'enrichment_changed', label: 'Enrichment changed' },
]

function number(value: unknown): number | null {
  const parsed = Number(Array.isArray(value) ? value[0] : value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

const base = computed(() => number(route.query.base))
const target = computed(() => number(route.query.target))
/** Finished scans of the project, newest first, to choose from. */
const choices = computed(() =>
  scans.scans
    .filter((scan) => FINAL_STATES.includes(scan.state))
    .map((scan) => ({
      value: scan.id,
      title: `Scan ${scan.id}, ${new Date(scan.created_at).toLocaleString()} (${scan.state})`,
    })),
)

async function load(): Promise<void> {
  message.value = ''
  diff.value = null
  try {
    if (props.diffId !== undefined) {
      diff.value = await api.get<Diff>(`/diffs/${props.diffId}`)
      return
    }
    if (props.projectId === undefined) return
    await scans.fetchScans(props.projectId)
    if (base.value !== null && target.value !== null) {
      diff.value = await api.get<Diff>(`/projects/${props.projectId}/compare`, {
        base: base.value,
        target: target.value,
      })
    }
  } catch (caught) {
    if (!(caught instanceof ApiError)) throw caught
    message.value = caught.message
  }
}

watch(() => [props.diffId, props.projectId, base.value, target.value].join('|'), load, {
  immediate: true,
})

function choose(name: 'base' | 'target', id: number | null): void {
  void router.replace({ query: { ...route.query, [name]: id ?? undefined } })
}

const exportTarget = computed(() => {
  if (!diff.value) return null
  if (diff.value.id !== null) return { path: `/diffs/${diff.value.id}`, query: {} }
  return {
    path: `/projects/${props.projectId}/compare`,
    query: { base: diff.value.base_scan_id, target: diff.value.target_scan_id },
  }
})

const comparedPorts = computed(() =>
  (diff.value?.compared_ports ?? [])
    .map(([low, high]) => (low === high ? `${low}` : `${low}-${high}`))
    .join(', '),
)

function changesOf(kind: string): Change[] {
  return (diff.value?.changes ?? []).filter((change) => change.kind === kind)
}

function shown(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (Array.isArray(value)) return value.length ? value.join(', ') : '—'
  if (typeof value === 'object') {
    return Object.values(value as Record<string, unknown>)
      .filter((part) => part !== null)
      .join(' / ')
  }
  return String(value)
}

function detail(change: Change): string {
  if (change.port !== null) return `port ${change.port}`
  if (change.kind === 'host_appeared' || change.kind === 'host_disappeared') return ''
  const what = change.field ? `${change.field}: ` : ''
  return `${what}${shown(change.before)} → ${shown(change.after)}`
}
</script>

<template>
  <v-container>
    <div class="d-flex align-center mb-4">
      <h1 class="text-h5">Comparison</h1>
      <v-spacer />
      <ExportMenu v-if="exportTarget" :path="exportTarget.path" :query="exportTarget.query" />
    </div>
    <v-alert v-if="message" type="error" class="mb-4" data-test="comparison-error">
      {{ message }}
    </v-alert>

    <div v-if="projectId !== undefined" class="d-flex ga-2 mb-4" data-test="comparison-choose">
      <v-select
        :model-value="base"
        :items="choices"
        label="Earlier scan"
        density="compact"
        hide-details
        data-test="comparison-base"
        @update:model-value="(id) => choose('base', id)"
      />
      <v-select
        :model-value="target"
        :items="choices"
        label="Later scan"
        density="compact"
        hide-details
        data-test="comparison-target"
        @update:model-value="(id) => choose('target', id)"
      />
    </div>

    <template v-if="diff">
      <p class="mb-2" data-test="comparison-scans">
        From
        <router-link :to="{ name: 'scan', params: { id: diff.base_scan_id } }">
          scan {{ diff.base_scan_id }}
        </router-link>
        to
        <router-link :to="{ name: 'scan', params: { id: diff.target_scan_id } }">
          scan {{ diff.target_scan_id }}
        </router-link>
        <template v-if="diff.stored && diff.created_at">
          · detected {{ new Date(diff.created_at).toLocaleString() }}
        </template>
      </p>
      <p class="text-caption mb-1" data-test="comparison-ports">
        Ports compared (probed in both scans): {{ comparedPorts || 'none' }}
      </p>
      <p v-if="diff.not_comparable.length" class="text-caption mb-3" data-test="comparison-not-comparable">
        Not comparable, targeted by one scan only: {{ diff.not_comparable.join(', ') }}
      </p>

      <div class="d-flex flex-wrap ga-2 mb-4" data-test="comparison-summary">
        <v-chip v-for="item in KINDS" :key="item.kind" size="small" :data-test="`count-${item.kind}`">
          {{ item.label }}: {{ diff.summary[item.kind] ?? 0 }}
        </v-chip>
      </div>

      <p v-if="diff.changes.length === 0" data-test="comparison-none">
        Nothing changed on the {{ diff.summary.compared_addresses ?? 0 }} addresses compared.
      </p>
      <template v-for="item in KINDS" :key="item.kind">
        <v-card v-if="changesOf(item.kind).length" :title="item.label" class="mb-3" :data-test="`kind-${item.kind}`">
          <v-table density="compact">
            <tbody>
              <tr v-for="(change, index) in changesOf(item.kind)" :key="index" data-test="change-row">
                <td style="width: 160px">{{ change.address }}</td>
                <td>
                  {{ detail(change) }}
                  <v-chip
                    v-if="change.dataset_change"
                    size="x-small"
                    color="info"
                    class="ml-2"
                    data-test="dataset-change"
                    title="The reference dataset has another version in the later scan: this may be a database update, not a network change."
                  >
                    dataset updated
                  </v-chip>
                </td>
              </tr>
            </tbody>
          </v-table>
        </v-card>
      </template>
    </template>
  </v-container>
</template>
