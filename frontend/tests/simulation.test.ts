// Tests for the simulation screen's logic (src/simulationHelpers.ts):
// status text, emergency text, traffic summary, route conversion.

import assert from "node:assert/strict"
import { describe, it } from "node:test"

import {
  allFinished,
  congestionColour,
  describeEmergency,
  describeSimulationState,
  describeTraffic,
  summariseTraffic,
  toCoordinates,
  type EmergencyStatus,
  type Vehicle,
} from "../src/simulationHelpers.ts"

function vehicle(overrides: Partial<Vehicle> = {}): Vehicle {
  return {
    vehicle_id: 0,
    latitude: 52.63,
    longitude: 1.29,
    distance_travelled: 0,
    finished: false,
    vehicle_type: "normal",
    on_emergency: false,
    ...overrides,
  }
}

function emergency(overrides: Partial<EmergencyStatus> = {}): EmergencyStatus {
  return {
    state: "en_route",
    elapsed_seconds: 12.4,
    response_time_seconds: null,
    remaining_metres: 812.6,
    eta_seconds: 40.2,
    reroutes: 0,
    ...overrides,
  }
}

describe("toCoordinates", () => {
  it("turns backend points into [lat, lon] pairs for the map", () => {
    assert.deepEqual(
      toCoordinates([
        { latitude: 52.63, longitude: 1.29 },
        { latitude: 52.64, longitude: 1.3 },
      ]),
      [
        [52.63, 1.29],
        [52.64, 1.3],
      ]
    )
  })

  it("keeps the order of the route", () => {
    const route = toCoordinates([
      { latitude: 1, longitude: 10 },
      { latitude: 2, longitude: 20 },
      { latitude: 3, longitude: 30 },
    ])

    assert.deepEqual(route.map((pair) => pair[0]), [1, 2, 3])
  })

  it("gives an empty route for no points", () => {
    assert.deepEqual(toCoordinates([]), [])
  })
})

describe("congestionColour", () => {
  it("maps each level to its map colour", () => {
    assert.equal(congestionColour("heavy"), "#c0392b")
    assert.equal(congestionColour("moderate"), "#e0932f")
    assert.equal(congestionColour("light"), "#2e9e6b")
  })

  it("draws anything else as green rather than crashing", () => {
    assert.equal(congestionColour("clear"), "#2e9e6b")
    assert.equal(congestionColour("something new"), "#2e9e6b")
    assert.equal(congestionColour(""), "#2e9e6b")
  })
})

describe("summariseTraffic", () => {
  it("has nothing to report with no vehicles", () => {
    assert.equal(summariseTraffic([]), null)
  })

  it("has nothing to report once everyone has arrived", () => {
    assert.equal(
      summariseTraffic([vehicle({ finished: true }), vehicle({ finished: true })]),
      null
    )
  })

  it("reports free flowing traffic as 100%", () => {
    const summary = summariseTraffic([
      vehicle({ speed_factor: 1 }),
      vehicle({ speed_factor: 1 }),
    ])

    assert.deepEqual(summary, {
      driving: 2,
      heldUp: 0,
      averageSpeedPercent: 100,
    })
  })

  it("counts vehicles that are slowed down and averages their speed", () => {
    const summary = summariseTraffic([
      vehicle({ speed_factor: 1 }),
      vehicle({ speed_factor: 0.6 }),
      vehicle({ speed_factor: 0.3 }),
      vehicle({ speed_factor: 0.3 }),
    ])

    assert.equal(summary?.driving, 4)
    assert.equal(summary?.heldUp, 3)
    assert.equal(summary?.averageSpeedPercent, 55) // (1 + .6 + .3 + .3) / 4
  })

  it("ignores vehicles that have already finished", () => {
    const summary = summariseTraffic([
      vehicle({ speed_factor: 0.3 }),
      vehicle({ speed_factor: 0.3, finished: true }),
      vehicle({ speed_factor: 1, finished: true }),
    ])

    assert.equal(summary?.driving, 1)
    assert.equal(summary?.averageSpeedPercent, 30)
  })

  it("treats a vehicle with no speed information as free flowing", () => {
    const summary = summariseTraffic([vehicle()])

    assert.equal(summary?.heldUp, 0)
    assert.equal(summary?.averageSpeedPercent, 100)
  })
})

describe("describeTraffic", () => {
  it("says when nobody is held up", () => {
    assert.equal(
      describeTraffic({ driving: 2, heldUp: 0, averageSpeedPercent: 100 }),
      "Traffic: no vehicles held up | average speed 100% of free flow"
    )
  })

  it("says how many are held up and how slowly traffic moves", () => {
    assert.equal(
      describeTraffic({ driving: 4, heldUp: 3, averageSpeedPercent: 55 }),
      "Traffic: 3 of 4 vehicles held up | average speed 55% of free flow"
    )
  })
})

describe("describeSimulationState", () => {
  const none = {
    connectionLost: false,
    hasStart: false,
    hasDestination: false,
    hasRoute: false,
    running: false,
  }

  it("starts by asking for a start point", () => {
    const state = describeSimulationState(none)

    assert.equal(state.label, "Waiting for input")
    assert.match(state.hint, /start point/)
  })

  it("then asks for a destination", () => {
    const state = describeSimulationState({ ...none, hasStart: true })

    assert.equal(state.label, "Waiting for input")
    assert.match(state.hint, /destination/)
  })

  it("then asks you to calculate the route", () => {
    const state = describeSimulationState({
      ...none,
      hasStart: true,
      hasDestination: true,
    })

    assert.equal(state.label, "Waiting for input")
    assert.match(state.hint, /Calculate Route/)
  })

  it("is Running while the simulation is polling", () => {
    const state = describeSimulationState({
      ...none,
      hasStart: true,
      hasDestination: true,
      hasRoute: true,
      running: true,
    })

    assert.equal(state.label, "Running")
    assert.equal(state.colour, "#2f7d6b")
  })

  it("is Completed once a route exists and polling has stopped", () => {
    const state = describeSimulationState({
      ...none,
      hasStart: true,
      hasDestination: true,
      hasRoute: true,
    })

    assert.equal(state.label, "Completed")
  })

  it("reports a lost connection above everything else", () => {
    const state = describeSimulationState({
      connectionLost: true,
      hasStart: true,
      hasDestination: true,
      hasRoute: true,
      running: true,
    })

    assert.equal(state.label, "Connection lost")
    assert.equal(state.colour, "#b4423a")
  })

  it("ignores a destination when there is no start", () => {
    const state = describeSimulationState({ ...none, hasDestination: true })

    assert.match(state.hint, /start point/)
  })
})

describe("describeEmergency", () => {
  it("shows ETA and distance while the ambulance is on its way", () => {
    assert.equal(
      describeEmergency(emergency()),
      "En route | ETA 40 s | 813 m to go | not rerouted | 12 s elapsed"
    )
  })

  it("shows the final response time once it has arrived", () => {
    assert.equal(
      describeEmergency(
        emergency({
          state: "arrived",
          response_time_seconds: 67,
          elapsed_seconds: 67,
          eta_seconds: 0,
          remaining_metres: 0,
        })
      ),
      "Arrived. Response time 67 s | not rerouted | 67 s elapsed"
    )
  })

  it("copes with an arrival that has no recorded response time", () => {
    assert.match(
      describeEmergency(emergency({ state: "arrived" })),
      /Response time 0 s/
    )
  })

  it("says when the ambulance is stopped by a closed road", () => {
    assert.match(
      describeEmergency(emergency({ state: "blocked" })),
      /^Stopped by a closed road \|/
    )
  })

  it("says when no ambulance has been sent yet", () => {
    assert.match(
      describeEmergency(emergency({ state: "waiting" })),
      /^Waiting for an ambulance \|/
    )
  })

  it("counts how many times the ambulance was rerouted", () => {
    assert.match(describeEmergency(emergency({ reroutes: 2 })), /rerouted 2x/)
    assert.match(describeEmergency(emergency({ reroutes: 0 })), /not rerouted/)
  })
})

describe("allFinished", () => {
  it("is false with no vehicles (nothing has started)", () => {
    assert.equal(allFinished([]), false)
  })

  it("is false while any vehicle is still driving", () => {
    assert.equal(
      allFinished([vehicle({ finished: true }), vehicle({ finished: false })]),
      false
    )
  })

  it("is true when every vehicle has arrived", () => {
    assert.equal(
      allFinished([vehicle({ finished: true }), vehicle({ finished: true })]),
      true
    )
  })
})
