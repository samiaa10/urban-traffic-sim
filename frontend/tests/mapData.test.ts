// Tests for the 3D map's data (src/mapData.ts): every feature comes from
// the app's own state, with coordinates turned into GeoJSON's order.

import assert from "node:assert/strict"
import { describe, it } from "node:test"

import {
  SOURCES,
  buildMapData,
  closureFeatures,
  pointFeatures,
  routeFeatures,
  stationFeatures,
  vehicleFeatures,
  type MapState,
} from "../src/mapData.ts"
import { COLOURS } from "../src/mapStyles.ts"
import type { Vehicle } from "../src/simulationHelpers.ts"

const car = (overrides: Partial<Vehicle> = {}): Vehicle => ({
  vehicle_id: 1, latitude: 52.63, longitude: 1.29, distance_travelled: 0,
  finished: false, vehicle_type: "normal", on_emergency: false, ...overrides,
})

const empty: MapState = {
  start: null, destination: null, route: [], congestion: [], emergencyRoute: [],
  blockedAhead: [], incidentRoute: [], emergencyLocation: null, candidates: [],
  stations: [], hotspots: [], scenarioClosures: [], vehicles: [],
}

describe("coordinates", () => {
  it("turns the app's [latitude, longitude] into GeoJSON's [longitude, latitude]", () => {
    const points = pointFeatures({ start: [52.63, 1.29], destination: null, emergencyLocation: null })
    assert.deepEqual(points.features[0].geometry.coordinates, [1.29, 52.63])

    const routes = routeFeatures({ route: [[52.6, 1.2], [52.7, 1.3]], incidentRoute: [], emergencyRoute: [] })
    assert.deepEqual(routes.features[0].geometry.coordinates, [[1.2, 52.6], [1.3, 52.7]])
  })
})

describe("vehicles", () => {
  it("draws only vehicles still driving, at the backend's positions", () => {
    const features = vehicleFeatures([
      car({ vehicle_id: 1, latitude: 52.61, longitude: 1.28 }),
      car({ vehicle_id: 2, finished: true }),
    ]).features
    assert.equal(features.length, 1)
    assert.deepEqual(features[0].geometry.coordinates, [1.28, 52.61])
    assert.equal(features[0].properties.emergency, false)
  })

  it("marks each emergency service with its own colour and letter", () => {
    const [ambulance, fire, police] = vehicleFeatures([
      car({ vehicle_type: "ambulance", on_emergency: true }),
      car({ vehicle_type: "fire" }),
      car({ vehicle_type: "police" }),
    ]).features.map((feature) => feature.properties)
    assert.deepEqual([ambulance.letter, fire.letter, police.letter], ["A", "F", "P"])
    assert.equal(ambulance.colour, COLOURS.emergency)
    assert.equal(new Set([ambulance.colour, fire.colour, police.colour]).size, 3)
  })

  it("treats any vehicle answering an emergency as an emergency vehicle, as the 2D map does", () => {
    const [feature] = vehicleFeatures([car({ on_emergency: true })]).features
    assert.equal(feature.properties.emergency, true)
  })
})

describe("routes and closures", () => {
  it("skips paths with fewer than two points", () => {
    assert.equal(routeFeatures({ route: [[52.6, 1.2]], incidentRoute: [], emergencyRoute: [] }).features.length, 0)
    assert.equal(closureFeatures({ blockedAhead: [], scenarioClosures: [] }).features.length, 0)
  })

  it("keeps the route, the diversion and the emergency route apart", () => {
    const path: [number, number][] = [[52.6, 1.2], [52.7, 1.3]]
    const kinds = routeFeatures({ route: path, incidentRoute: path, emergencyRoute: path })
      .features.map((feature) => feature.properties.kind)
    assert.deepEqual(kinds, ["route", "reroute", "emergency"])
  })

  it("tells a closure ahead of a vehicle from a scenario's closures", () => {
    const kinds = closureFeatures({
      blockedAhead: [[52.6, 1.2], [52.7, 1.3]],
      scenarioClosures: [{ start: [52.6, 1.2], end: [52.61, 1.21] }],
    }).features.map((feature) => feature.properties.kind)
    assert.deepEqual(kinds, ["ahead", "scenario"])
  })
})

describe("stations", () => {
  it("numbers candidates by rank and gives their popup the 2D map's lines", () => {
    const [best] = stationFeatures({
      candidates: [{ rank: 1, latitude: 52.6, longitude: 1.3, average_seconds: 150, worst_seconds: null, within_target_percent: 92.4 }],
      stations: [],
    }).features
    assert.equal(best.properties.label, "1")
    assert.equal(best.properties.colour, COLOURS.rankBest)
    assert.equal(
      best.properties.popup,
      "Candidate #1\nAverage response: 2.5 min\nWorst case: n/a\nWithin target: 92%",
    )
  })

  it("numbers a chosen set in pick order", () => {
    const labels = stationFeatures({
      candidates: [],
      stations: [1, 2].map((order) => ({
        order, latitude: 52.6, longitude: 1.3, average_seconds_after: 200, within_target_percent_after: 80,
      })),
    }).features.map((feature) => feature.properties.label)
    assert.deepEqual(labels, ["1", "2"])
  })
})

describe("all sources", () => {
  it("gives every source the map draws, empty when there is nothing to show", () => {
    const data = buildMapData(empty)
    assert.deepEqual(Object.keys(data).sort(), [...SOURCES].sort())
    for (const collection of Object.values(data)) assert.equal(collection.features.length, 0)
  })
})
