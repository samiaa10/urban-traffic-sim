// Tests for the 3D vehicle bodies (src/vehicles3d.ts) and the timeline's
// incident marks (src/live.ts): real positions, real headings, nothing added.

import assert from "node:assert/strict"
import { describe, it } from "node:test"

import { BODY, LANE_OFFSET, bodyFeatures, bodyScale, footprint, interpolate } from "../src/vehicles3d.ts"
import { incidentMarks, type Sample } from "../src/live.ts"

const METRES = 111_320
const pose = (heading: number) => ({ lat: 52.63, lon: 1.29, heading })

// The footprint's centre and its corners in metres from the vehicle
function metres(points: [number, number][]) {
  const scale = METRES * Math.cos((52.63 * Math.PI) / 180)
  return points.slice(0, 4).map(([lon, lat]) => [(lon - 1.29) * scale, (lat - 52.63) * METRES])
}

describe("vehicle bodies", () => {
  it("are as long as a car, facing the way it drives", () => {
    const [front, , back] = metres(footprint(pose(0), false))
    // Heading north: the front corners are 2.25 m north, the back 2.25 m south
    assert.ok(Math.abs(front[1] - BODY.car.length / 2) < 0.01)
    assert.ok(Math.abs(back[1] + BODY.car.length / 2) < 0.01)
  })

  it("sit left of the road's centre line, as UK traffic drives", () => {
    const north = metres(footprint(pose(0), false))
    const centreEast = north.reduce((sum, [east]) => sum + east, 0) / 4
    assert.ok(Math.abs(centreEast + LANE_OFFSET) < 0.01)   // west of the line
    const east = metres(footprint(pose(90), false))
    const centreNorth = east.reduce((sum, [, n]) => sum + n, 0) / 4
    assert.ok(Math.abs(centreNorth - LANE_OFFSET) < 0.01)   // north of the line
  })

  it("make emergency vehicles bigger and taller", () => {
    const vehicle = (emergency: boolean) => ({
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [1.29, 52.63] as [number, number] },
      properties: { id: 1, emergency },
    })
    const poses = new Map([[1, pose(0)]])
    const [car] = bodyFeatures([vehicle(false)], poses, 1).features
    const [ambulance] = bodyFeatures([vehicle(true)], poses, 1).features
    assert.equal(car.properties.height, BODY.car.height)
    assert.equal(ambulance.properties.height, BODY.emergency.height)
  })

  it("are drawn bigger only when zoomed out, never smaller than life", () => {
    assert.equal(bodyScale(19), 1)
    assert.equal(bodyScale(18), 1)
    assert.equal(bodyScale(17), 2)
    assert.equal(bodyScale(14), 3)
  })
})

describe("gliding between updates", () => {
  it("moves in a straight line between two real positions", () => {
    const halfway = interpolate({ lat: 52.63, lon: 1.29, heading: 0 }, { lat: 52.64, lon: 1.30, heading: 0 }, 0.5)
    assert.ok(Math.abs(halfway.lat - 52.635) < 1e-9 && Math.abs(halfway.lon - 1.295) < 1e-9)
  })

  it("turns the short way round", () => {
    assert.equal(interpolate(pose(350), pose(10), 0.5).heading, 0)
    assert.equal(interpolate(pose(10), pose(350), 0.5).heading, 0)
  })
})

describe("the timeline's incident marks", () => {
  const sample = (time: number, closed: number, accidents: number): Sample => ({
    time, active_vehicles: 10, held_up: 0, mean_speed_factor: 1, arrived: 0,
    mean_journey_seconds: null, reroutes: 0, closed_roads: closed, accidents,
  })

  it("mark when roads closed or an accident happened, not when they cleared", () => {
    const marks = incidentMarks([sample(1, 0, 0), sample(2, 2, 0), sample(3, 2, 1), sample(4, 0, 0)])
    assert.deepEqual(marks, [
      { time: 2, label: "2 road(s) closed" },
      { time: 3, label: "Accident" },
    ])
  })
})
