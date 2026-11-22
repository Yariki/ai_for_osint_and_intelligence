<script setup lang="ts">
// The map of the workspace: the hosts the table shows, clustered.
//
// There is no default tile source. Without a configured one the map is not
// created at all, so no tile server is contacted; a notice says what to do.
import 'maplibre-gl/dist/maplibre-gl.css'

import { onMounted, onUnmounted, ref, watch } from 'vue'

import { api, type Schemas } from '../api/client'
import { type ClusterHost, MAXMIND_ATTRIBUTION, type Tiles, useMap } from '../composables/useMap'
import { useTraceroutesStore } from '../stores/traceroutes'
import { useWorkspaceStore } from '../stores/workspace'
import ClusterHostList from './ClusterHostList.vue'

const workspace = useWorkspaceStore()
const traceroutes = useTraceroutesStore()

const container = ref<HTMLElement | null>(null)
const tiles = ref<Tiles | null>(null)
const configured = ref<boolean | null>(null)
const cluster = ref<ClusterHost[]>([])

function select(host: ClusterHost): void {
  cluster.value = []
  void workspace.select(host.address, host.scan_id)
}

const map = useMap({ onHost: select, onCluster: (hosts) => (cluster.value = hosts) })

onMounted(async () => {
  const config = await api.get<Schemas['ConfigOut']>('/config')
  tiles.value = config.tiles
  configured.value = config.tiles !== null
})

// The container exists once the configuration says that there is a tile source.
watch(container, async (element) => {
  if (!element || !tiles.value) return
  await map.mount(element, tiles.value)
  map.setPoints(workspace.located)
})

// Live: the points follow the store, as the table rows do.
watch(
  () => workspace.located,
  (points) => map.setPoints(points),
  { deep: true },
)
// Another scan, or the whole project: look at its hosts.
watch([() => workspace.scan?.id, () => workspace.projectView], () => map.refit())

// The accuracy radius of the selected host (FR-MAP-10).
watch(
  () => workspace.selected,
  (host) => {
    const found = host?.enrichment
    map.showAccuracy(
      found && found.latitude !== null && found.longitude !== null
        ? {
            latitude: found.latitude,
            longitude: found.longitude,
            radiusKm: found.accuracy_radius_km,
          }
        : null,
    )
  },
)

// The path of the open traceroute, hop by hop as it arrives (FR-TRC-6). A hop
// with several responders is drawn at the first one that has a location.
watch(
  () => traceroutes.current?.hops,
  (hops) => {
    map.showPath(
      (hops ?? []).map((hop) => {
        const located = hop.responders.find(
          (responder) =>
            !responder.unlocatable &&
            responder.latitude !== null &&
            responder.latitude !== undefined &&
            responder.longitude !== null &&
            responder.longitude !== undefined,
        )
        return {
          ttl: hop.ttl,
          point: located ? [located.longitude as number, located.latitude as number] : null,
        }
      }),
    )
  },
  { deep: true },
)

onUnmounted(() => map.destroy())
</script>

<template>
  <div>
    <v-alert v-if="configured === false" type="info" variant="tonal" data-test="map-notice">
      No map tile source is configured, so the map is switched off and no tile server is
      contacted. The hosts are in the table below. To switch the map on, the operator sets
      <code>NDM_TILE_URL_TEMPLATE</code> and <code>NDM_TILE_ATTRIBUTION</code> for a tile provider
      whose usage policy permits this use.
    </v-alert>
    <template v-else-if="configured">
      <div ref="container" style="height: 420px" data-test="map" />
      <p class="text-caption mt-1" data-test="map-count">
        {{ workspace.located.length.toLocaleString('en') }} hosts on the map. Locations are
        approximate: a host is drawn at the centre of an area, not where it is. Select a host to
        see the radius of that area.
      </p>
      <p class="text-caption" data-test="tile-attribution">Map: {{ tiles?.attribution }}</p>
    </template>
    <p class="text-caption" data-test="maxmind-attribution">{{ MAXMIND_ATTRIBUTION }}</p>

    <ClusterHostList :hosts="cluster" @select="select" @close="cluster = []" />
  </div>
</template>
