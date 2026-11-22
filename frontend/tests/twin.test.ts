// Tests for the digital twin's environment (src/twin.ts, src/twinLayers.ts):
// the sun over Norwich, the light and sky it gives, the live weather's
// effect, the analysis layers and the map layers' lifecycle.

import assert from "node:assert/strict"
import { describe, it } from "node:test"

import type { FeatureCollection } from "../src/mapData.ts"
import type { Plan } from "../src/planning.ts"
import {
  atmosphere,
  congestionWalls,
  describeCode,
  existingCoverage,
  phaseOf,
  populationFeatures,
  preferLite,
  rampColour,
  softwareRenderer,
  sunPosition,
  weatherEffect,
  type Weather,
} from "../src/twin.ts"
import { DEM_TILES, LAYERS, TwinLayers, type TwinMap } from "../src/twinLayers.ts"

const near = (a: number, b: number, tolerance: number) => assert.ok(Math.abs(a - b) <= tolerance, `${a} is not within ${tolerance} of ${b}`)

describe("the sun over Norwich", () => {
  it("is at its highest, due south, at solar noon on the solstices", () => {
    // Solar noon in Norwich (1.29° E) is about 11:57 UTC
    const summer = sunPosition(new Date(Date.UTC(2026, 5, 21, 11, 57)))
    near(summer.altitude, 90 - 52.63 + 23.44, 0.6)
    near(summer.azimuth, 180, 2)
    const winter = sunPosition(new Date(Date.UTC(2026, 11, 21, 11, 57)))
    near(winter.altitude, 90 - 52.63 - 23.44, 0.6)
  })

  it("rises in the east and sets in the west", () => {
    const morning = sunPosition(new Date(Date.UTC(2026, 2, 20, 6, 10)))
    const evening = sunPosition(new Date(Date.UTC(2026, 2, 20, 18, 0)))
    near(morning.altitude, 0, 2)
    near(morning.azimuth, 90, 6)
    near(evening.azimuth, 270, 6)
    assert.ok(sunPosition(new Date(Date.UTC(2026, 2, 20, 0, 0))).altitude < -30)
  })

  it("names the light by the sun's height", () => {
    assert.deepEqual([30, 5, -4, -20].map(phaseOf), ["day", "golden", "twilight", "night"])
  })
})

const weather = (overrides: Partial<Weather>): Weather => ({
  time: "2026-11-22T12:00", temperature_c: 6.1, precipitation_mm: 0, rain_mm: 0, snowfall_cm: 0,
  cloud_cover_percent: 20, visibility_m: 30_000, wind_speed_kmh: 6.5, wind_direction_deg: 70,
  weather_code: 0, is_day: true, source: "Open-Meteo", ...overrides,
})

describe("light, sky and weather", () => {
  it("lights the city from the sun's direction by day and dims it at night", () => {
    const day = atmosphere({ altitude: 40, azimuth: 180 }, null)
    const night = atmosphere({ altitude: -30, azimuth: 0 }, null)
    assert.equal(day.light.position[1], 180)
    assert.ok(day.light.intensity > night.light.intensity)
    assert.equal(night.phase, "night")
  })

  it("greys the sky under cloud and haze", () => {
    const clear = atmosphere({ altitude: 30, azimuth: 180 }, weather({}))
    const dull = atmosphere({ altitude: 30, azimuth: 180 }, weather({ cloud_cover_percent: 100, visibility_m: 2000 }))
    assert.notEqual(clear.sky["sky-color"], dull.sky["sky-color"])
    assert.ok(dull.sky["fog-ground-blend"] > clear.sky["fog-ground-blend"])
    assert.ok(dull.light.intensity < clear.light.intensity)
  })

  it("shows rain as heavy as the reading, and nothing without a reading", () => {
    assert.equal(weatherEffect(null).rain, 0)
    assert.equal(weatherEffect(weather({ rain_mm: 1 })).rain, 0.25)
    assert.equal(weatherEffect(weather({ rain_mm: 12 })).rain, 1)
    assert.match(weatherEffect(weather({ weather_code: 61, rain_mm: 1 })).label, /^Rain, 6 °C, wind 7 km\/h$/)
    assert.equal(describeCode(45), "Fog")
  })
})

const ring = (lon: number): [number, number][] => [[lon, 52.63], [lon + 0.001, 52.63], [lon + 0.001, 52.631], [lon, 52.63]]

function plan(withExisting: boolean): Plan {
  return {
    service: "fire", speed: 17, target_seconds: 480, disruption: "none", closed_roads: 0, model: "", hex_metres: 180,
    existing: [], existing_left_out: [], candidates: [], search: { candidates: 0, possible_sets: 0, method: "" }, optimised: [],
    cells: [0, 1, 2].map((i) => ({ polygon: ring(1.28 + i / 100), junctions: 4, population: [0, 100, 400][i] })),
    stages: withExisting ? [{
      label: "existing",
      summary: { junctions: 12, unreachable_junctions: 0, mean_seconds: 200, median_seconds: 200, p90_seconds: 300, worst_seconds: 600,
        junctions_within_target_percent: 90, population_within_target_percent: 90, population_beyond_target: 60 },
      cells: { seconds: [100, 300, 600], within: [1, 1, 0], beyond_people: [0, 0, 60] },
    }] : [],
  }
}

describe("analysis layers", () => {
  it("extrudes residents per hexagon on a square-root scale, skipping empty ones", () => {
    const features = populationFeatures(plan(true)).features
    assert.deepEqual(features.map((f) => f.properties.population), [100, 400])
    // 100 is a quarter of the most, so half the height above the base
    assert.equal(features[1].properties.height, 6 + 320)
    assert.equal(features[0].properties.height, 6 + 160)
    assert.equal(features[1].properties.colour, rampColour(1))
  })

  it("draws existing coverage at a quarter of the planning view's height, or nothing without stations", () => {
    const cells = existingCoverage(plan(true))!.features
    assert.deepEqual(cells.map((f) => f.properties.band), ["fast", "within", "near"])
    assert.equal(cells[2].properties.height, 600 * 0.5 * 0.25)
    assert.equal(cells[2].properties.underserved, true)
    assert.equal(existingCoverage(plan(false)), null)
  })

  it("raises a wall either side of each busy road, as tall as its traffic", () => {
    const congestion: FeatureCollection = {
      type: "FeatureCollection",
      features: [
        { type: "Feature", geometry: { type: "LineString", coordinates: [[1.29, 52.63], [1.291, 52.63], [1.291, 52.631]] }, properties: { vehicles: 10, colour: "#e0533d" } },
        { type: "Feature", geometry: { type: "LineString", coordinates: [[1.3, 52.63], [1.301, 52.63]] }, properties: { vehicles: 1 } },
      ],
    }
    const walls = congestionWalls(congestion).features
    assert.equal(walls.length, 2)   // two segments of the busy road; the quiet one is left out
    assert.equal(walls[0].properties.height, 66)
    // 8 m wide in all (4 m either side of the line)
    const corners = walls[0].geometry.coordinates[0] as [number, number][]
    near((corners[0][1] - corners[3][1]) * 111_320, 8, 0.01)
  })

  it("suggests performance mode on small devices and big simulations", () => {
    assert.equal(preferLite({ cores: 8, memoryGb: 16 }), false)
    assert.equal(preferLite({ cores: 4 }), true)
    assert.equal(preferLite({ memoryGb: 2 }), true)
    assert.equal(preferLite({ reducedMotion: true }), true)
    assert.equal(preferLite({ cores: 8 }, 5000), true)
    assert.equal(preferLite({ cores: 16, software: true }), true)
  })

  it("recognises graphics drawn without a GPU", () => {
    assert.equal(softwareRenderer("ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)"), true)
    assert.equal(softwareRenderer("llvmpipe (LLVM 15.0.7, 256 bits)"), true)
    assert.equal(softwareRenderer("ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)"), false)
    assert.equal(softwareRenderer(null), false)
  })
})

class FakeMap implements TwinMap {
  sources = new Map<string, Record<string, unknown>>()
  layers: { id: string; source: string; visibility: string }[] = []
  terrain: unknown = "unset"
  sky: unknown = null
  light: unknown = null
  addSource(id: string, source: Record<string, unknown>) { this.sources.set(id, source) }
  getSource(id: string) {
    const source = this.sources.get(id)
    return source ? { setData: (data: FeatureCollection) => { source.data = data } } : undefined
  }
  removeSource(id: string) {
    assert.ok(!this.layers.some((l) => l.source === id), `${id} still in use`)
    this.sources.delete(id)
  }
  addLayer(layer: Record<string, unknown>) {
    assert.ok(this.sources.has(layer.source as string))
    const layout = (layer.layout ?? {}) as { visibility?: string }
    this.layers.push({ id: layer.id as string, source: layer.source as string, visibility: layout.visibility ?? "visible" })
  }
  getLayer(id: string) { return this.layers.find((l) => l.id === id) }
  removeLayer(id: string) { this.layers = this.layers.filter((l) => l.id !== id) }
  setLayoutProperty(id: string, _name: string, value: unknown) { this.getLayer(id)!.visibility = value as string }
  setTerrain(terrain: unknown) { this.terrain = terrain }
  setSky(sky: unknown) { this.sky = sky }
  setLight(light: unknown) { this.light = light }
}

describe("twin map layers", () => {
  it("adds open elevation tiles and hidden layers, and switches terrain on and off", () => {
    const map = new FakeMap()
    const twin = new TwinLayers(map)
    twin.create()
    assert.deepEqual(map.sources.get("twin-dem")!.tiles, [DEM_TILES])
    assert.equal(map.sources.get("twin-dem")!.encoding, "terrarium")
    assert.ok(map.layers.every((l) => l.visibility === "none"))
    twin.setTerrain(true)
    assert.deepEqual(map.terrain, { source: "twin-dem", exaggeration: 1.8 })
    assert.equal(map.getLayer("twin-hillshade")!.visibility, "visible")
    twin.setTerrain(false)
    assert.equal(map.terrain, null)
  })

  it("shows each analysis layer's parts together and updates its data", () => {
    const map = new FakeMap()
    const twin = new TwinLayers(map)
    twin.create()
    twin.show("coverage", true)
    assert.deepEqual(LAYERS.coverage.map((id) => map.getLayer(id)!.visibility), ["visible", "visible"])
    assert.equal(map.getLayer("twin-population")!.visibility, "none")
    const data: FeatureCollection = { type: "FeatureCollection", features: [] }
    twin.update("population", data)
    assert.equal(map.sources.get("twin-population")!.data, data)
  })

  it("sets the sky and light, and removes everything it added", () => {
    const map = new FakeMap()
    const twin = new TwinLayers(map)
    twin.create()
    const sky = atmosphere({ altitude: 20, azimuth: 200 }, null)
    twin.setAtmosphere(sky.sky, sky.light)
    assert.equal(map.light, sky.light)
    twin.setTerrain(true)
    twin.remove()
    assert.equal(map.layers.length, 0)
    assert.equal(map.sources.size, 0)
    assert.equal(map.terrain, null)
  })
})
