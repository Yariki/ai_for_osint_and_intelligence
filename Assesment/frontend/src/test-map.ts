// Stands in for MapLibre GL in tests. The real library needs a browser with
// WebGL; this one keeps what it was told and lets a test play the user.

import type { Feature } from 'geojson'

type Handler = (event: { features?: Feature[] }) => void

export class FakeSource {
  data: unknown
  /** The zoom at which the clicked cluster would split. */
  expansionZoom = 4
  /** The hosts of the clicked cluster. */
  leaves: Feature[] = []

  constructor(readonly spec: Record<string, unknown>) {
    this.data = spec.data
  }

  setData(data: unknown): Promise<void> {
    this.data = data
    return Promise.resolve()
  }

  getClusterExpansionZoom(): Promise<number> {
    return Promise.resolve(this.expansionZoom)
  }

  getClusterLeaves(_id: number, limit: number, offset: number): Promise<Feature[]> {
    return Promise.resolve(this.leaves.slice(offset, offset + limit))
  }
}

export class FakeMap {
  static instances: FakeMap[] = []

  sources = new Map<string, FakeSource>()
  layers: Array<Record<string, unknown>> = []
  eased: unknown[] = []
  fitted: unknown[] = []
  removed = false
  zoom = 5
  private handlers = new Map<string, Handler[]>()

  constructor(readonly options: Record<string, unknown>) {
    FakeMap.instances.push(this)
  }

  static latest(): FakeMap {
    const map = FakeMap.instances.at(-1)
    if (!map) throw new Error('no map was created')
    return map
  }

  addControl(): void {}

  on(type: string, layerOrHandler: string | Handler, handler?: Handler): void {
    const key = typeof layerOrHandler === 'string' ? `${type}:${layerOrHandler}` : type
    const added = typeof layerOrHandler === 'string' ? handler : layerOrHandler
    if (added) this.handlers.set(key, [...(this.handlers.get(key) ?? []), added])
  }

  /** The style has loaded, or the user did something: "load", "click:host", "click:clusters". */
  fire(key: string, features: Feature[] = []): void {
    for (const handler of this.handlers.get(key) ?? []) handler({ features })
  }

  addSource(id: string, spec: Record<string, unknown>): void {
    this.sources.set(id, new FakeSource(spec))
  }

  getSource(id: string): FakeSource | undefined {
    return this.sources.get(id)
  }

  addLayer(layer: Record<string, unknown>): void {
    this.layers.push(layer)
  }

  getCanvas(): { style: Record<string, string> } {
    return { style: {} }
  }

  getZoom(): number {
    return this.zoom
  }

  easeTo(options: unknown): void {
    this.eased.push(options)
  }

  fitBounds(box: unknown): void {
    this.fitted.push(box)
  }

  remove(): void {
    this.removed = true
  }
}

/** What `vi.mock('maplibre-gl', ...)` returns. */
export const fakeMapLibre = { Map: FakeMap, NavigationControl: class {} }
