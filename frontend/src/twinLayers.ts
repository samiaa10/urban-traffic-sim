// The digital twin's map layers: terrain (and its hillshade), the sky and
// light, and the analysis layers (population, coverage, 3D congestion).
// Written against the few MapLibre methods it needs (TwinMap), so tests can
// drive it with a stand-in map (tests/twinLayers.test.ts).
//
// Terrain: Mapzen / Tilezen's open elevation tiles on AWS (Terrarium
// encoding; SRTM and other public sources), exaggerated a little, since
// Norwich rises only from about 0 to 40 m.

import type { FeatureCollection } from "./mapData.ts"

export type TwinMap = {
  addSource(id: string, source: Record<string, unknown>): unknown
  getSource(id: string): { setData?(data: FeatureCollection): unknown } | undefined
  removeSource(id: string): unknown
  addLayer(layer: Record<string, unknown>, before?: string): unknown
  getLayer(id: string): unknown
  removeLayer(id: string): unknown
  setLayoutProperty(id: string, name: string, value: unknown): unknown
  setTerrain(terrain: { source: string; exaggeration: number } | null): unknown
  setSky(sky: Record<string, unknown>): unknown
  setLight(light: Record<string, unknown>): unknown
}

export const DEM_TILES = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
export const EXAGGERATION = 1.8

export const LAYERS = {
  population: ["twin-population"],
  coverage: ["twin-coverage", "twin-coverage-gaps"],
  congestion: ["twin-congestion"],
} as const
export type TwinLayer = keyof typeof LAYERS

const SOURCES = ["twin-population", "twin-coverage", "twin-congestion"] as const
const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] }

export class TwinLayers {
  private map: TwinMap
  private before?: string
  terrain = false

  constructor(map: TwinMap, before?: string) {
    this.map = map
    this.before = before
  }

  create(): void {
    this.map.addSource("twin-dem", {
      type: "raster-dem", tiles: [DEM_TILES], tileSize: 256, encoding: "terrarium", maxzoom: 14,
      attribution: "Elevation: Mapzen / Tilezen (open data)",
    })
    // Hillshade under the roads and buildings: Norwich's valleys
    this.map.addLayer({
      id: "twin-hillshade", type: "hillshade", source: "twin-dem",
      layout: { visibility: "none" },
      paint: {
        "hillshade-shadow-color": "#04070b", "hillshade-highlight-color": "#3a4d63",
        "hillshade-accent-color": "#0b1118", "hillshade-exaggeration": 0.45,
      },
    }, this.before)
    for (const id of SOURCES) this.map.addSource(id, { type: "geojson", data: EMPTY })
    this.map.addLayer({
      id: "twin-population", type: "fill-extrusion", source: "twin-population",
      layout: { visibility: "none" },
      paint: {
        "fill-extrusion-color": ["get", "colour"], "fill-extrusion-height": ["get", "height"],
        "fill-extrusion-base": 0, "fill-extrusion-opacity": 0.62,
      },
    }, this.before)
    this.map.addLayer({
      id: "twin-coverage", type: "fill-extrusion", source: "twin-coverage",
      layout: { visibility: "none" },
      paint: {
        "fill-extrusion-color": ["get", "colour"], "fill-extrusion-height": ["get", "height"],
        "fill-extrusion-base": 0, "fill-extrusion-opacity": 0.55,
      },
    }, this.before)
    this.map.addLayer({
      id: "twin-coverage-gaps", type: "line", source: "twin-coverage",
      layout: { visibility: "none" },
      filter: ["get", "underserved"],
      paint: { "line-color": "#ff6b5a", "line-width": 2, "line-opacity": 0.9 },
    }, this.before)
    this.map.addLayer({
      id: "twin-congestion", type: "fill-extrusion", source: "twin-congestion",
      layout: { visibility: "none" },
      paint: {
        "fill-extrusion-color": ["get", "colour"], "fill-extrusion-height": ["get", "height"],
        "fill-extrusion-base": 0, "fill-extrusion-opacity": 0.85,
      },
    })
  }

  update(layer: TwinLayer, data: FeatureCollection): void {
    this.map.getSource(`twin-${layer}`)?.setData?.(data)
  }

  show(layer: TwinLayer, visible: boolean): void {
    for (const id of LAYERS[layer]) {
      if (this.map.getLayer(id)) this.map.setLayoutProperty(id, "visibility", visible ? "visible" : "none")
    }
  }

  setTerrain(on: boolean): void {
    this.terrain = on
    this.map.setTerrain(on ? { source: "twin-dem", exaggeration: EXAGGERATION } : null)
    if (this.map.getLayer("twin-hillshade")) {
      this.map.setLayoutProperty("twin-hillshade", "visibility", on ? "visible" : "none")
    }
  }

  setAtmosphere(sky: Record<string, unknown>, light: Record<string, unknown>): void {
    this.map.setSky(sky)
    this.map.setLight(light)
  }

  // Layers first, then sources (MapLibre will not remove a source in use)
  remove(): void {
    this.map.setTerrain(null)
    for (const id of ["twin-hillshade", ...Object.values(LAYERS).flat()]) {
      if (this.map.getLayer(id)) this.map.removeLayer(id)
    }
    for (const id of ["twin-dem", ...SOURCES]) if (this.map.getSource(id)) this.map.removeSource(id)
  }
}
