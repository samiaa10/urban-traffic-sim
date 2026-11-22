// Tests for the planning view's map layers (src/planningLayers.ts), driven
// with a stand-in map that keeps the sources and layers MapLibre would.

import assert from "node:assert/strict"
import { describe, it } from "node:test"

import type { FeatureCollection } from "../src/mapData.ts"
import { GROUPS, PlanningLayers, SOURCES, type PlanningMap } from "../src/planningLayers.ts"

class FakeMap implements PlanningMap {
  sources = new Map<string, FeatureCollection>()
  layers: { id: string; source: string; before?: string; visibility: string }[] = []
  calls: string[] = []

  addSource(id: string, source: { type: "geojson"; data: FeatureCollection }) {
    assert.ok(!this.sources.has(id), `source ${id} added twice`)
    this.sources.set(id, source.data)
    this.calls.push(`addSource ${id}`)
  }
  getSource(id: string) {
    if (!this.sources.has(id)) return undefined
    return { setData: (data: FeatureCollection) => this.sources.set(id, data) }
  }
  removeSource(id: string) {
    // MapLibre refuses to remove a source a layer still uses
    assert.ok(!this.layers.some((l) => l.source === id), `source ${id} still in use`)
    this.sources.delete(id)
    this.calls.push(`removeSource ${id}`)
  }
  addLayer(layer: Record<string, unknown>, before?: string) {
    assert.ok(this.sources.has(layer.source as string), `layer ${layer.id} has no source`)
    this.layers.push({ id: layer.id as string, source: layer.source as string, before, visibility: "visible" })
  }
  getLayer(id: string) {
    return this.layers.find((l) => l.id === id)
  }
  removeLayer(id: string) {
    this.layers = this.layers.filter((l) => l.id !== id)
    this.calls.push(`removeLayer ${id}`)
  }
  setLayoutProperty(id: string, name: string, value: unknown) {
    assert.equal(name, "visibility")
    this.getLayer(id)!.visibility = value as string
  }
  visibility(id: string) {
    return this.getLayer(id)?.visibility
  }
}

const collection = (n: number): FeatureCollection => ({
  type: "FeatureCollection",
  features: Array.from({ length: n }, () => ({
    type: "Feature", geometry: { type: "Point", coordinates: [1.29, 52.63] }, properties: {},
  })),
})

describe("planning map layers", () => {
  it("creates every source and layer, coverage below the labels", () => {
    const map = new FakeMap()
    new PlanningLayers(map, "place-labels").create()
    assert.deepEqual([...map.sources.keys()], [...SOURCES])
    assert.deepEqual(map.layers.map((l) => l.id).sort(), Object.values(GROUPS).flat().sort())
    for (const id of ["plan-coverage-flat", "plan-coverage-3d", "plan-underserved"]) {
      assert.equal(map.getLayer(id)!.before, "place-labels")
    }
    // Stations go on top of everything
    assert.equal(map.getLayer("plan-columns")!.before, undefined)
  })

  it("updates a source with new data", () => {
    const map = new FakeMap()
    const layers = new PlanningLayers(map)
    layers.create()
    layers.update("plan-coverage", collection(3))
    assert.equal(map.sources.get("plan-coverage")!.features.length, 3)
    layers.update("plan-coverage", collection(1))
    assert.equal(map.sources.get("plan-coverage")!.features.length, 1)
  })

  it("shows the coverage as 3D columns or flat hexagons, never both", () => {
    const map = new FakeMap()
    const layers = new PlanningLayers(map)
    layers.create()
    layers.setGroup("coverage", true, true)
    assert.deepEqual([map.visibility("plan-coverage-3d"), map.visibility("plan-coverage-flat")], ["visible", "none"])
    layers.setGroup("coverage", true, false)
    assert.deepEqual([map.visibility("plan-coverage-3d"), map.visibility("plan-coverage-flat")], ["none", "visible"])
    layers.setGroup("coverage", false, true)
    assert.deepEqual([map.visibility("plan-coverage-3d"), map.visibility("plan-coverage-flat")], ["none", "none"])
    layers.setCoverageStyle(false)
    assert.equal(map.visibility("plan-coverage-flat"), "visible")
  })

  it("hides and shows each group's layers together", () => {
    const map = new FakeMap()
    const layers = new PlanningLayers(map)
    layers.create()
    layers.setGroup("stations", false)
    assert.deepEqual([map.visibility("plan-columns"), map.visibility("plan-labels")], ["none", "none"])
    assert.equal(map.visibility("plan-underserved"), "visible")
    layers.setGroup("stations", true)
    assert.equal(map.visibility("plan-columns"), "visible")
  })

  it("removes every layer before its source, and copes with being removed twice", () => {
    const map = new FakeMap()
    const layers = new PlanningLayers(map)
    layers.create()
    layers.remove()
    assert.equal(map.layers.length, 0)
    assert.equal(map.sources.size, 0)
    const firstSource = map.calls.findIndex((c) => c.startsWith("removeSource"))
    const lastLayer = map.calls.map((c) => c.startsWith("removeLayer")).lastIndexOf(true)
    assert.ok(lastLayer < firstSource)
    layers.remove()
    // And it can be created again afterwards
    layers.create()
    assert.equal(map.sources.size, SOURCES.length)
  })

  it("does nothing to layers that are not there yet", () => {
    const map = new FakeMap()
    const layers = new PlanningLayers(map)
    layers.setGroup("stations", false)
    layers.update("plan-coverage", collection(2))
    assert.equal(map.layers.length, 0)
  })
})
