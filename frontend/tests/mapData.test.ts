// Tests for the 3D map's data (src/mapData.ts): every feature comes from
// the app's own state, with coordinates turned into GeoJSON's order.

import assert from "node:assert/strict"
import { describe, it } from "node:test"

import {
  SOURCES,
  buildMapData,
  closureFeatures,
  congestionFeatures,
  describeBuilding,
  describeRoad,
  describeSegment,
  describeVehicle,
  metresBetween,
  pointFeatures,
  sameSegment,
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

describe("inspect mode", () => {
  it("describes a vehicle from what the backend sent", () => {
    const [car1, ambulance] = vehicleFeatures([
      car({ vehicle_id: 7, speed_factor: 0.4, journey_seconds: 90 }),
      car({ vehicle_id: 8, vehicle_type: "ambulance", on_emergency: true }),
    ]).features.map((feature) => feature.properties)
    assert.deepEqual(describeVehicle(car1), [
      "Vehicle 7",
      "Held up: 40% of its free-flow speed",
      "Journey so far: 1.5 min",
    ])
    assert.deepEqual(describeVehicle(ambulance), ["Ambulance 8", "Answering an emergency", "Driving freely"])
  })

  it("says when a building's height is probably the default", () => {
    assert.deepEqual(describeBuilding(41, 0), ["Building", "Height: 41 m"])
    assert.deepEqual(describeBuilding(5, undefined), [
      "Building",
      "Height: 5 m",
      "Probably estimated: the height used when none is mapped",
    ])
    assert.deepEqual(describeBuilding(12, 4), ["Building", "Height: 12 m", "Starts 4 m above the ground"])
    assert.deepEqual(describeBuilding(undefined, undefined), ["Building", "No height data"])
  })

  const a = { latitude: 52.63, longitude: 1.29 }
  const b = { latitude: 52.631, longitude: 1.29 }
  const segment = (vehicles: number, level: string, start = a, end = b) => ({
    start, end, vehicle_count: vehicles, level,
  })

  it("gives a road its length and the live traffic on it, in both directions", () => {
    const congestion = congestionFeatures([
      segment(2, "light"),
      segment(3, "heavy", b, a),
      segment(9, "heavy", a, { latitude: 52.64, longitude: 1.3 }),
    ])
    assert.deepEqual(describeRoad({ name: "Colegate", start_point: a, end_point: b }, congestion), [
      "Colegate",
      "111 m, junction to junction",
      "Now: 5 vehicles, heavy traffic",
    ])
    assert.equal(
      describeRoad({ name: null, start_point: a, end_point: b }, congestionFeatures([]))[2],
      "Now: no vehicles on it",
    )
    assert.equal(describeRoad({ start_point: a, end_point: b }, congestionFeatures([]))[0], "Unnamed road")
  })

  it("describes a traffic segment the road lookup could not name", () => {
    const [feature] = congestionFeatures([segment(1, "moderate")]).features
    assert.deepEqual(describeSegment(feature), ["Road", "Now: 1 vehicle, moderate traffic"])
  })

  it("matches a segment either way round, allowing for the map's rounding", () => {
    const one: [number, number][] = [[1.29, 52.63], [1.29, 52.631]]
    assert.ok(sameSegment(one, [[1.29001, 52.631], [1.29, 52.63001]]))
    assert.ok(!sameSegment(one, [[1.29, 52.63], [1.291, 52.631]]))
  })

  it("measures metres between two points", () => {
    assert.equal(Math.round(metresBetween(a, b)), 111)
  })
})
