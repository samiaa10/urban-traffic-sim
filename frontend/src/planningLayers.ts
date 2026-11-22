// The planning view's map layers: created once, updated with new data,
// shown or hidden by group, and removed cleanly. Written against the few
// MapLibre methods it needs (PlanningMap), so tests can drive it with a
// stand-in map (tests/planningLayers.test.ts).

import type { FeatureCollection } from "./mapData.ts"

export type PlanningMap = {
  addSource(id: string, source: { type: "geojson"; data: FeatureCollection }): unknown
  getSource(id: string): { setData(data: FeatureCollection): unknown } | undefined
  removeSource(id: string): unknown
  addLayer(layer: Record<string, unknown>, before?: string): unknown
  getLayer(id: string): unknown
  removeLayer(id: string): unknown
  setLayoutProperty(id: string, name: string, value: unknown): unknown
}

export const SOURCES = ["plan-coverage", "plan-columns", "plan-labels"] as const
type SourceId = (typeof SOURCES)[number]

// What the layer controls switch, and the layers behind each
export const GROUPS = {
  coverage: ["plan-coverage-flat", "plan-coverage-3d"],
  underserved: ["plan-underserved"],
  stations: ["plan-columns", "plan-labels"],
} as const
export type Group = keyof typeof GROUPS

const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] }

export class PlanningLayers {
  private map: PlanningMap
  private before?: string

  constructor(map: PlanningMap, before?: string) {
    this.map = map
    this.before = before
  }

  // Adds every source and layer (empty). Coverage sits under the
  // buildings' labels; station columns and labels on top.
  create(): void {
    for (const id of SOURCES) this.map.addSource(id, { type: "geojson", data: EMPTY })
    // Flat coverage: the hexagons on the ground, see-through so streets show
    this.map.addLayer({
      id: "plan-coverage-flat", type: "fill", source: "plan-coverage",
      paint: { "fill-color": ["get", "colour"], "fill-opacity": 0.38 },
    }, this.before)
    // 3D coverage: a column per hexagon, its height the response time (or
    // the time saved), translucent so buildings show through
    this.map.addLayer({
      id: "plan-coverage-3d", type: "fill-extrusion", source: "plan-coverage",
      paint: {
        "fill-extrusion-color": ["get", "colour"],
        "fill-extrusion-height": ["get", "height"],
        "fill-extrusion-base": 0,
        "fill-extrusion-opacity": 0.5,
      },
    }, this.before)
    // Underserved: hexagons with 50 or more residents beyond the target
    this.map.addLayer({
      id: "plan-underserved", type: "line", source: "plan-coverage",
      filter: ["get", "underserved"],
      paint: { "line-color": "#ff6b5a", "line-width": 2.2, "line-opacity": 0.95 },
    }, this.before)
    this.map.addLayer({
      id: "plan-columns", type: "fill-extrusion", source: "plan-columns",
      paint: {
        "fill-extrusion-color": ["get", "colour"],
        "fill-extrusion-height": ["get", "height"],
        "fill-extrusion-base": ["get", "base"],
        "fill-extrusion-opacity": 0.96,
      },
    })
    this.map.addLayer({
      id: "plan-labels", type: "symbol", source: "plan-labels",
      layout: {
        "text-field": ["get", "text"], "text-font": ["Noto Sans Bold"], "text-size": 11,
        "text-offset": [0, -1.6], "text-allow-overlap": true, "text-ignore-placement": true,
      },
      paint: { "text-color": "#eef3f8", "text-halo-color": "#0b1016", "text-halo-width": 1.4 },
    })
  }

  update(source: SourceId, data: FeatureCollection): void {
    this.map.getSource(source)?.setData(data)
  }

  // 3D columns or flat hexagons for the coverage
  setCoverageStyle(threeD: boolean): void {
    this.setVisible("plan-coverage-3d", threeD)
    this.setVisible("plan-coverage-flat", !threeD)
  }

  setGroup(group: Group, visible: boolean, threeD = true): void {
    if (group === "coverage") {
      this.setVisible("plan-coverage-3d", visible && threeD)
      this.setVisible("plan-coverage-flat", visible && !threeD)
      return
    }
    for (const layer of GROUPS[group]) this.setVisible(layer, visible)
  }

  private setVisible(layer: string, visible: boolean) {
    if (this.map.getLayer(layer)) this.map.setLayoutProperty(layer, "visibility", visible ? "visible" : "none")
  }

  // Removes every layer, then every source (layers first: MapLibre will not
  // remove a source a layer still uses)
  remove(): void {
    const layers = Object.values(GROUPS).flat()
    for (const layer of layers) if (this.map.getLayer(layer)) this.map.removeLayer(layer)
    for (const id of SOURCES) if (this.map.getSource(id)) this.map.removeSource(id)
  }
}
