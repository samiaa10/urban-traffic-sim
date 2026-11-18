// Tests for the live simulation's logic (src/live.ts).

import assert from "node:assert/strict"
import { describe, it } from "node:test"

import {
  accidentRequest,
  appendSample,
  closureRequest,
  describeSample,
  fleetRequest,
  heldUpPercent,
  series,
  speedPercent,
  stepQuery,
  stoppedPercent,
  surgeRequest,
  type Sample,
} from "../src/live.ts"

const sample = (time: number, overrides: Partial<Sample> = {}): Sample => ({
  time, active_vehicles: 10, held_up: 4, mean_speed_factor: 0.8, arrived: 2,
  mean_journey_seconds: 300, reroutes: 1, closed_roads: 0, accidents: 0, ...overrides,
})

describe("speed", () => {
  it("asks for several seconds only above 1x", () => {
    assert.equal(stepQuery(1), "")
    assert.equal(stepQuery(5), "?seconds=5")
  })
})

describe("the live charts' samples", () => {
  it("adds samples and starts again when a new run starts", () => {
    let history = appendSample([], sample(1))
    history = appendSample(history, sample(2))
    assert.equal(history.length, 2)
    assert.deepEqual(appendSample(history, sample(1)).map((s) => s.time), [1])
  })

  it("keeps at most an hour of samples", () => {
    let history: Sample[] = []
    for (let t = 1; t <= 3700; t += 1) history = appendSample(history, sample(t))
    assert.equal(history.length, 3600)
    assert.equal(history[0].time, 101)
  })

  it("turns samples into chart points, skipping missing values", () => {
    const history = [sample(1), sample(2, { mean_speed_factor: null }), sample(3, { active_vehicles: 0, held_up: 0 })]
    assert.deepEqual(series(history, speedPercent), [{ x: 1, y: 80 }, { x: 3, y: 80 }])
    assert.deepEqual(series(history, heldUpPercent), [{ x: 1, y: 40 }, { x: 2, y: 40 }])
  })

  it("charts cars stopped in queues only for runs that report them (the cellular automaton)", () => {
    assert.equal(stoppedPercent(sample(1)), null)
    assert.equal(stoppedPercent(sample(1, { stopped: 3 })), 30)
    assert.equal(stoppedPercent(sample(1, { stopped: 0, active_vehicles: 0 })), null)
  })

  it("describes the latest sample", () => {
    assert.equal(describeSample(undefined), "No data yet: start a route.")
    assert.equal(describeSample(sample(5)), "10 driving, 4 held up, average speed 80% of free flow, 2 arrived")
  })
})

describe("the live controls' requests", () => {
  it("adds a fleet, a rush hour, accidents and closures on the run's own endpoints", () => {
    assert.deepEqual(fleetRequest(500, 3), ["/simulation/fleet?count=500&seed=3", "/fleet?count=500&seed=3"])
    assert.match(surgeRequest()[0], /^\/traffic\/surge\?latitude=52\.6286.*direction=inbound.*duration_seconds=900$/)
    assert.deepEqual(accidentRequest(11, 12, "severe", 15)[1], "/incidents/accident?start=11&destination=12&severity=severe&duration_seconds=900")
    assert.equal(closureRequest(1, 2, false)[1], "/incidents/block?start=1&destination=2")
    assert.equal(closureRequest(1, 2, true)[1], "/incidents/unblock?start=1&destination=2")
  })
})
