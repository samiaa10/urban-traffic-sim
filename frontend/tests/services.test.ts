// Tests for the emergency services and the optimiser-in-traffic helpers
// (src/services.ts), and the optimiser's service options (src/optimiser.ts).

import assert from "node:assert/strict"
import { describe, it } from "node:test"

import { optionQuery } from "../src/optimiser.ts"
import {
  SERVICES,
  emergencyRequests,
  evaluationRequest,
  minutesWithInterval,
  queuedMessage,
  serviceOf,
} from "../src/services.ts"

describe("emergency services", () => {
  it("offers ambulance, fire and police with the backend's speeds", () => {
    assert.deepEqual(SERVICES.map((s) => [s.name, s.speed]), [
      ["ambulance", 20], ["fire", 17], ["police", 22],
    ])
    assert.equal(serviceOf("police").label, "Police car")
  })

  it("asks for an ambulance exactly as before", () => {
    const requests = emergencyRequests("ambulance", 0, [52.63, 1.29], [52.64, 1.3])
    assert.deepEqual(requests.station, [
      "/station/create?latitude=52.63&longitude=1.29&station_type=ambulance",
      "/stations?latitude=52.63&longitude=1.29&station_type=ambulance",
    ])
    assert.equal(requests.vehicle[0], "/station/add-vehicle?station_id=0&speed=20&vehicle_type=ambulance")
    assert.equal(requests.emergency[0], "/emergency/create?latitude=52.64&longitude=1.3&emergency_type=ambulance")
  })

  it("asks for a fire engine at its station and speed", () => {
    const requests = emergencyRequests("fire", 2, [52.63, 1.29], [52.64, 1.3])
    assert.ok(requests.station[1].endsWith("station_type=fire"))
    assert.equal(requests.vehicle[1], "/stations/add-vehicle?station_id=2&speed=17&vehicle_type=fire")
    assert.ok(requests.emergency[1].endsWith("emergency_type=fire"))
  })

  it("explains a queued emergency", () => {
    assert.match(queuedMessage("fire", 2), /Every fire engine is busy.*number 2/)
  })
})

describe("the optimiser tested in traffic", () => {
  it("asks for the top 5 sites, or a set and its comparisons", () => {
    assert.deepEqual(
      evaluationRequest({ kind: "ranking", stations: 3, service: "police", distribution: "population" }),
      { kind: "ranking", stations: 3, top: 5, service: "police", distribution: "population" }
    )
    assert.equal(evaluationRequest({ kind: "station_set", stations: 4, service: "fire", distribution: "uniform" }).top, 3)
  })

  it("shows minutes with their interval", () => {
    assert.equal(minutesWithInterval({ mean: 150, ci95: 30 }), "2.5 ± 0.5 min")
    assert.equal(minutesWithInterval({ mean: 150, ci95: null }), "2.5 min")
    assert.equal(minutesWithInterval({ mean: null, ci95: null }), "-")
  })

  it("adds optimiser options only when they differ from the original", () => {
    assert.equal(optionQuery(), "")
    assert.equal(optionQuery({ service: "ambulance", distribution: "uniform" }), "")
    assert.equal(optionQuery({ service: "fire", distribution: "population" }), "&service=fire&distribution=population")
  })
})
