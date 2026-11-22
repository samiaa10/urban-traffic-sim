// Tests for the command centre's logic (src/command.ts): replay positions
// from recorded tracks, incident status over time, the map's features and
// the response figures. Every value comes from the backend's state.

import assert from "node:assert/strict"
import { describe, it } from "node:test"

import {
  canResolve,
  clock,
  commandMapData,
  metricTiles,
  positionAt,
  replaySpan,
  replayVehicles,
  statusAt,
  unitsByService,
  type CommandIncident,
  type CommandState,
  type Metrics,
  type Replay,
  type Unit,
} from "../src/command.ts"

const metrics = (overrides: Partial<Metrics> = {}): Metrics => ({
  responses: 2, mean_response_seconds: 95, worst_response_seconds: 130, within_target_percent: 50,
  units_total: 6, units_available: 4, units_en_route: 1, units_on_scene: 1, units_returning: 0,
  incidents_open: 2, incidents_resolved: 1, queued_units: 0,
  by_service: {
    ambulance: { responses: 1, mean_seconds: 60, target_seconds: 420, available: 2, units: 2 },
    fire: { responses: 1, mean_seconds: 130, target_seconds: 480, available: 1, units: 2 },
    police: { responses: 0, mean_seconds: null, target_seconds: 900, available: 1, units: 2 },
  },
  ...overrides,
})

// A fire: reported at 10, two units sent, one arrives at 70, the other at
// 130, both leave at 1330, resolved then
const fire: CommandIncident = {
  id: 1, kind: "fire", label: "Fire", latitude: 52.63, longitude: 1.29, status: "on_scene",
  created_at: 10, resolved_at: 1330, services: ["fire", "ambulance"], on_scene_seconds: 1200,
  units: [4, 16], road: null, flood_roads: 0, target: { latitude: 52.63, longitude: 1.29 },
  timeline: [
    { time: 10, event: "reported", label: "Fire reported" },
    { time: 10, event: "dispatched", label: "Fire engine 4 dispatched", service: "fire", vehicle_id: 4 },
    { time: 10, event: "dispatched", label: "Ambulance 16 dispatched", service: "ambulance", vehicle_id: 16 },
    { time: 70, event: "arrived", label: "Ambulance 16 arrived: 60 s", service: "ambulance", vehicle_id: 16, response_seconds: 60, within_target: true },
    { time: 130, event: "arrived", label: "Fire engine 4 arrived: 120 s", service: "fire", vehicle_id: 4, response_seconds: 120, within_target: true },
    { time: 1270, event: "cleared", label: "Ambulance 16 left the scene", service: "ambulance", vehicle_id: 16 },
    { time: 1330, event: "cleared", label: "Fire engine 4 left the scene", service: "fire", vehicle_id: 4 },
    { time: 1330, event: "resolved", label: "Incident resolved" },
  ],
}

const closure: CommandIncident = {
  ...fire, id: 2, kind: "road_closure", label: "Road closure", status: "active", created_at: 40, resolved_at: null,
  services: [], units: [], on_scene_seconds: 0,
  road: [{ latitude: 52.62, longitude: 1.28 }, { latitude: 52.621, longitude: 1.281 }],
  timeline: [{ time: 40, event: "reported", label: "Road closure reported" }, { time: 40, event: "road", label: "Road closed" }],
}

const unit = (id: number, overrides: Partial<Unit>): Unit => ({
  vehicle_id: id, service: "ambulance", station: "Ambulance standby point 1", station_id: 0, state: "available",
  incident_id: null, latitude: 52.6, longitude: 1.27, eta_seconds: null, reroutes: 0, route: [], ...overrides,
})

describe("replay", () => {
  const samples: [number, number, number][] = [[10, 52.6, 1.27], [11, 52.601, 1.27], [13, 52.601, 1.272]]

  it("interpolates between the recorded samples, and shows nothing outside them", () => {
    assert.deepEqual(positionAt(samples, 10), [52.6, 1.27])
    assert.deepEqual(positionAt(samples, 12), [52.601, 1.271])
    assert.deepEqual(positionAt(samples, 13), [52.601, 1.272])
    assert.equal(positionAt(samples, 9), null)
    assert.equal(positionAt(samples, 14), null)
    assert.equal(positionAt([], 10), null)
  })

  it("covers the first report to the recording's time", () => {
    const replay: Replay = { simulation_time: 900, incidents: [fire], tracks: [{ vehicle_id: 4, service: "fire", samples }] }
    assert.deepEqual(replaySpan(replay), [10, 900])
  })

  it("draws each unit out at that moment, facing the way it moves", () => {
    const replay: Replay = {
      simulation_time: 20, incidents: [],
      tracks: [{ vehicle_id: 4, service: "fire", samples }, { vehicle_id: 9, service: "police", samples: [[15, 52.7, 1.3], [16, 52.7, 1.3]] }],
    }
    const vehicles = replayVehicles(replay, 10)
    assert.deepEqual(vehicles.map((v) => [v.vehicle_id, v.vehicle_type]), [[4, "fire"]])
    assert.ok(Math.abs(vehicles[0].heading! - 0) < 1)   // moving north
    assert.ok(Math.abs(replayVehicles(replay, 12)[0].heading! - 90) < 1)   // then east
  })

  it("works out an incident's status at any moment from its timeline", () => {
    assert.equal(statusAt(fire, 5), null)
    assert.equal(statusAt(fire, 10), "en_route")
    assert.equal(statusAt(fire, 100), "en_route")
    assert.equal(statusAt(fire, 130), "on_scene")
    assert.equal(statusAt(fire, 1330), "resolved")
    const queued = { ...fire, resolved_at: null, timeline: fire.timeline.slice(0, 2) }
    assert.equal(statusAt(queued, 20), "queued")
    assert.equal(statusAt(closure, 50), "active")
  })
})

describe("the map", () => {
  const state: CommandState = {
    simulation_time: 100,
    incidents: [{ ...fire, resolved_at: null }, closure, { ...fire, id: 3, status: "resolved" }],
    units: [
      unit(16, { state: "en_route", incident_id: 1, latitude: 52.61, longitude: 1.28,
        route: [{ latitude: 52.61, longitude: 1.28 }, { latitude: 52.62, longitude: 1.285 }, { latitude: 52.63, longitude: 1.29 }] }),
      unit(17, { state: "on_scene", incident_id: 1 }),
      unit(18, {}),
    ],
    stations: [{ station_id: 0, name: "Ambulance standby point 1", source: "optimiser", service: "ambulance", latitude: 52.6, longitude: 1.27 }],
    metrics: metrics(),
  }
  const vehicles = [16, 17, 18].map((id) => ({
    vehicle_id: id, latitude: 52.6, longitude: 1.27, distance_travelled: 0,
    finished: id !== 16, vehicle_type: "ambulance", on_emergency: id === 16,
  }))

  it("draws open incidents, their roads, units' routes and the stations", () => {
    const data = commandMapData({ state, vehicles, congestion: [], floods: [], selected: 2 })
    assert.deepEqual(data.points.features.map((f) => f.properties.label), ["#1 Fire", "#2 Road closure"])
    assert.equal(data.points.features[1].properties.selected, true)
    assert.equal(data.closures.features.length, 1)
    // The route starts where the unit is now
    const route = data.routes.features[0]
    assert.equal(route.properties.unit, 16)
    assert.deepEqual(route.geometry.coordinates[0], [1.28, 52.61])
    assert.equal(route.geometry.coordinates.length, 3)
    assert.match(String(data.stations.features[0].properties.popup), /1 of 3 units at the station\nStandby point chosen by the optimiser/)
  })

  it("keeps units at a scene on the map; parked ones are left to their station", () => {
    const data = commandMapData({ state, vehicles, congestion: [], floods: [] })
    assert.deepEqual(data.vehicles.features.map((f) => f.properties.id).sort(), [16, 17])
  })

  it("draws the recording instead of the live state when replaying", () => {
    const replay: Replay = { simulation_time: 100, incidents: [fire, closure], tracks: [{ vehicle_id: 4, service: "fire", samples: [[10, 52.6, 1.27], [20, 52.61, 1.27]] }] }
    const early = commandMapData({ state, vehicles, congestion: [], floods: [], replay: { data: replay, time: 15 } })
    assert.deepEqual(early.points.features.map((f) => f.properties.label), ["#1 Fire"])
    assert.deepEqual(early.vehicles.features.map((f) => f.properties.id), [4])
    assert.equal(early.routes.features.length, 0)
    const later = commandMapData({ state, vehicles, congestion: [], floods: [], replay: { data: replay, time: 60 } })
    assert.deepEqual(later.points.features.map((f) => f.properties.label), ["#1 Fire", "#2 Road closure"])
    assert.equal(later.vehicles.features.length, 0)
  })
})

describe("figures", () => {
  it("shows the backend's numbers as clock times and shares", () => {
    const tiles = metricTiles(metrics())
    assert.deepEqual(tiles.map((t) => t.value), ["1:35", "2:10", "50%", "4/6", "2"])
    assert.equal(tiles[2].tone, "bad")
    assert.equal(tiles[3].detail, "1 en route, 1 on scene")
  })

  it("has nothing to average before any unit arrives", () => {
    const tiles = metricTiles(metrics({ responses: 0, mean_response_seconds: null, worst_response_seconds: null, within_target_percent: null }))
    assert.deepEqual(tiles.slice(0, 3).map((t) => t.value), ["-", "-", "-"])
    assert.equal(tiles[0].detail, "0 units arrived")
  })

  it("warns when every unit is busy or one is waiting", () => {
    const tiles = metricTiles(metrics({ units_available: 0, queued_units: 2 }))
    assert.equal(tiles[3].tone, "bad")
    assert.equal(tiles[4].detail, "2 waiting for a unit")
  })

  it("formats clock times", () => {
    assert.equal(clock(65), "1:05")
    assert.equal(clock(3725), "1:02:05")
    assert.equal(clock(null), "-")
  })

  it("groups units by service and knows when an incident can be closed", () => {
    const grouped = unitsByService([unit(1, { service: "police" }), unit(2, {}), unit(3, { service: "police" })])
    assert.deepEqual(grouped.map(([service, list]) => [service, list.length]), [["ambulance", 1], ["police", 2]])
    assert.equal(canResolve({ ...fire, status: "en_route" }), false)
    assert.equal(canResolve({ ...fire, status: "on_scene" }), true)
    assert.equal(canResolve(closure), true)
  })
})
