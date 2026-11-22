// The map of the workspace: MapLibre GL with the operator's raster tiles and
// the hosts as a clustered layer.
//
// The functions at the top are plain data transformations and are what the
// tests exercise. `useMap` at the bottom owns the MapLibre object, which needs
// a real browser with WebGL; the library is loaded only when a map is mounted.

import type { Feature, FeatureCollection, LineString, Point, Polygon } from 'geojson'
import type { GeoJSONSource, Map as MapLibreMap } from 'maplibre-gl'

import type { Schemas } from '../api/client'
import type { MapPoint } from '../stores/workspace'

export type Tiles = Schemas['TilesOut']

/** The credit the GeoLite2 licence asks for wherever its data is shown (NFR-LIC-1). */
export const MAXMIND_ATTRIBUTION =
  'This product includes GeoLite Data created by MaxMind, available from https://www.maxmind.com.'

export const MAX_ZOOM = 18
const EARTH_RADIUS_KM = 6371

export interface ClusterHost {
  address: string
  scan_id: number
  open_port_count: number
}

type PointFeature = Feature<Point, ClusterHost>

/** The hosts as GeoJSON points. GeoJSON puts the longitude first. */
export function toFeatures(points: MapPoint[]): FeatureCollection<Point, ClusterHost> {
  return {
    type: 'FeatureCollection',
    features: points.map((point) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [point.longitude, point.latitude] },
      properties: {
        address: point.address,
        scan_id: point.scan_id,
        open_port_count: point.open_port_count,
      },
    })),
  }
}

/** The hosts of a cluster, from its leaf features, in address order. */
export function clusterHosts(leaves: Feature[]): ClusterHost[] {
  const hosts = leaves.map((leaf) => {
    const properties = (leaf as PointFeature).properties
    return {
      address: String(properties.address),
      scan_id: Number(properties.scan_id),
      open_port_count: Number(properties.open_port_count),
    }
  })
  return hosts.sort((a, b) => addressNumber(a.address) - addressNumber(b.address))
}

export function addressNumber(address: string): number {
  return address.split('.').reduce((sum, octet) => sum * 256 + Number(octet), 0)
}

/**
 * A cluster can be split by zooming in only if the map can zoom that far.
 * Hosts on one coordinate never split; they are opened as a list (FR-MAP-2).
 */
export function canSplit(expansionZoom: number): boolean {
  return expansionZoom <= MAX_ZOOM
}

/** The box that holds every point, as [west, south, east, north]; null without points. */
export function bounds(points: MapPoint[]): [number, number, number, number] | null {
  if (points.length === 0) return null
  const longitudes = points.map((point) => point.longitude)
  const latitudes = points.map((point) => point.latitude)
  return [
    Math.min(...longitudes),
    Math.min(...latitudes),
    Math.max(...longitudes),
    Math.max(...latitudes),
  ]
}

/**
 * The accuracy radius of a location as a polygon (FR-MAP-10): the points at
 * that distance from the centre, along the great circle.
 */
export function accuracyCircle(
  latitude: number,
  longitude: number,
  radiusKm: number,
  steps = 64,
): Feature<Polygon> {
  const angular = radiusKm / EARTH_RADIUS_KM
  const lat = (latitude * Math.PI) / 180
  const lon = (longitude * Math.PI) / 180
  const ring: number[][] = []
  for (let step = 0; step <= steps; step++) {
    const bearing = (2 * Math.PI * step) / steps
    const pointLat = Math.asin(
      Math.sin(lat) * Math.cos(angular) + Math.cos(lat) * Math.sin(angular) * Math.cos(bearing),
    )
    const pointLon =
      lon +
      Math.atan2(
        Math.sin(bearing) * Math.sin(angular) * Math.cos(lat),
        Math.cos(angular) - Math.sin(lat) * Math.sin(pointLat),
      )
    ring.push([(pointLon * 180) / Math.PI, (pointLat * 180) / Math.PI])
  }
  return { type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] }, properties: {} }
}

/** A hop of a traceroute path: where it is, or null if it was silent or has no location. */
export interface PathHop {
  ttl: number
  /** [longitude, latitude] */
  point: [number, number] | null
}

export interface PathSegment {
  from_ttl: number
  to_ttl: number
  /** The segment skips a silent or unlocated hop, so the path between its ends is not known. */
  dashed: boolean
}

/**
 * The path of a traceroute as line segments between the hops that have a
 * location, in TTL order. A segment that skips a silent or unlocated hop, or
 * a TTL that is missing, is dashed (FR-TRC-6).
 */
export function pathSegments(hops: PathHop[]): Array<Feature<LineString, PathSegment>> {
  const segments: Array<Feature<LineString, PathSegment>> = []
  let previous: { ttl: number; point: [number, number] } | null = null
  let skipped = false
  for (const hop of [...hops].sort((a, b) => a.ttl - b.ttl)) {
    if (hop.point === null) {
      skipped = true
      continue
    }
    if (previous !== null) {
      segments.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: [previous.point, hop.point] },
        properties: {
          from_ttl: previous.ttl,
          to_ttl: hop.ttl,
          dashed: skipped || hop.ttl - previous.ttl > 1,
        },
      })
    }
    previous = { ttl: hop.ttl, point: hop.point }
    skipped = false
  }
  return segments
}

const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] }

export interface MapHandlers {
  /** A single host was clicked. */
  onHost: (host: ClusterHost) => void
  /** A cluster that cannot be split by zooming was clicked. */
  onCluster: (hosts: ClusterHost[]) => void
}

export function useMap(handlers: MapHandlers) {
  let map: MapLibreMap | null = null
  let loaded = false
  let pending: MapPoint[] = []
  let pendingPath: PathHop[] = []
  let fitted = false

  function hostSource(): GeoJSONSource | undefined {
    return map?.getSource('hosts') as GeoJSONSource | undefined
  }

  /** Create the map in the container, with the configured tile source and no other. */
  async function mount(container: HTMLElement, tiles: Tiles): Promise<void> {
    const { Map, NavigationControl } = await import('maplibre-gl')
    const created = new Map({
      container,
      maxZoom: MAX_ZOOM,
      center: [0, 20],
      zoom: 1,
      style: {
        version: 8,
        sources: {
          tiles: {
            type: 'raster',
            tiles: [tiles.url_template],
            tileSize: 256,
            attribution: tiles.attribution,
          },
        },
        layers: [{ id: 'tiles', type: 'raster', source: 'tiles' }],
      },
    })
    map = created
    created.addControl(new NavigationControl({ showCompass: false }))

    created.on('load', () => {
      created.addSource('accuracy', { type: 'geojson', data: EMPTY })
      created.addLayer({
        id: 'accuracy',
        type: 'fill',
        source: 'accuracy',
        paint: { 'fill-color': '#1976d2', 'fill-opacity': 0.12, 'fill-outline-color': '#1976d2' },
      })
      // The path of the open traceroute, under the hosts.
      created.addSource('path', { type: 'geojson', data: EMPTY })
      created.addLayer({
        id: 'path-solid',
        type: 'line',
        source: 'path',
        filter: ['!', ['get', 'dashed']],
        paint: { 'line-color': '#6a1b9a', 'line-width': 3 },
      })
      created.addLayer({
        id: 'path-dashed',
        type: 'line',
        source: 'path',
        filter: ['get', 'dashed'],
        paint: { 'line-color': '#6a1b9a', 'line-width': 3, 'line-dasharray': [2, 2] },
      })
      created.addSource('hosts', {
        type: 'geojson',
        data: toFeatures(pending),
        cluster: true,
        clusterRadius: 40,
        // Above the map's own limit: hosts on one coordinate stay one cluster at every zoom.
        clusterMaxZoom: MAX_ZOOM + 4,
      })
      created.addLayer({
        id: 'clusters',
        type: 'circle',
        source: 'hosts',
        filter: ['has', 'point_count'],
        paint: {
          'circle-color': '#1976d2',
          'circle-opacity': 0.75,
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 2,
          // The size says how many hosts: up to 10, 100, 1,000, and more.
          'circle-radius': ['step', ['get', 'point_count'], 12, 10, 16, 100, 21, 1000, 27],
        },
      })
      created.addLayer({
        id: 'host',
        type: 'circle',
        source: 'hosts',
        filter: ['!', ['has', 'point_count']],
        paint: {
          'circle-color': '#d32f2f',
          'circle-radius': 6,
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 1.5,
        },
      })
      loaded = true
      showPath(pendingPath)
      fit(pending)
    })

    created.on('click', 'host', (event) => {
      const feature = event.features?.[0]
      const [host] = feature ? clusterHosts([feature]) : []
      if (host) handlers.onHost(host)
    })
    created.on('click', 'clusters', (event) => {
      const feature = event.features?.[0]
      if (feature) void openCluster(feature)
    })
    for (const layer of ['host', 'clusters']) {
      created.on('mouseenter', layer, () => (created.getCanvas().style.cursor = 'pointer'))
      created.on('mouseleave', layer, () => (created.getCanvas().style.cursor = ''))
    }
  }

  /** Zoom into a cluster, or list its hosts if zooming cannot take it apart. */
  async function openCluster(feature: Feature): Promise<void> {
    const source = hostSource()
    if (!map || !source) return
    const clusterId = Number(feature.properties?.cluster_id)
    const count = Number(feature.properties?.point_count)
    const zoom = await source.getClusterExpansionZoom(clusterId)
    if (canSplit(zoom)) {
      const [longitude = 0, latitude = 0] = (feature.geometry as Point).coordinates
      map.easeTo({ center: [longitude, latitude], zoom })
      return
    }
    handlers.onCluster(clusterHosts(await source.getClusterLeaves(clusterId, count, 0)))
  }

  function fit(points: MapPoint[]): void {
    const box = bounds(points)
    if (!map || !box || fitted) return
    fitted = true
    map.fitBounds(box, { padding: 60, maxZoom: 8, animate: false })
  }

  /** Show these hosts. The first non-empty set also decides what the map looks at. */
  function setPoints(points: MapPoint[]): void {
    pending = points
    if (!loaded) return
    hostSource()?.setData(toFeatures(points))
    fit(points)
  }

  /** Look at the hosts again: after another scan was chosen. */
  function refit(): void {
    fitted = false
  }

  /** Draw the accuracy radius of a location, or remove it. */
  function showAccuracy(
    location: { latitude: number; longitude: number; radiusKm: number | null } | null,
  ): void {
    const source = map?.getSource('accuracy') as GeoJSONSource | undefined
    if (!loaded || !source) return
    source.setData(
      location && location.radiusKm
        ? accuracyCircle(location.latitude, location.longitude, location.radiusKm)
        : EMPTY,
    )
  }

  /** Draw the path of a traceroute, or remove it with an empty list. */
  function showPath(hops: PathHop[]): void {
    pendingPath = hops
    const source = map?.getSource('path') as GeoJSONSource | undefined
    if (!loaded || !source) return
    source.setData({ type: 'FeatureCollection', features: pathSegments(hops) })
  }

  function destroy(): void {
    map?.remove()
    map = null
    loaded = false
  }

  return { mount, setPoints, refit, showAccuracy, showPath, destroy }
}
