// Tests for the History section's logic (src/history.ts): which runs can
// be restarted, which studies resumed, and the words shown.

import assert from "node:assert/strict"
import { describe, it } from "node:test"

import {
  canRestart,
  canResume,
  formatWhen,
  restartSummary,
  runStatusLabel,
  type RestartedRun,
  type SavedRun,
  type StudySummary,
} from "../src/history.ts"

function run(overrides: Partial<SavedRun> = {}): SavedRun {
  return {
    simulation_id: "run-1",
    status: "stopped",
    live: false,
    is_default: false,
    created_at: "2026-11-16T09:05:00Z",
    ended_at: "2026-11-16T09:20:00Z",
    start: 11,
    destination: 22,
    simulation_time: 300,
    vehicles: 1,
    restarted_from: null,
    ...overrides,
  }
}

function study(status: StudySummary["status"]): StudySummary {
  return {
    study_id: "s",
    name: "S",
    status,
    scenario_count: 4,
    completed: 1,
    failed: 0,
    created_at: "2026-11-16T09:05:00Z",
  }
}

function restarted(applied: Partial<RestartedRun["applied"]> = {}): RestartedRun {
  return {
    simulation_id: "run-2",
    restarted_from: "run-1",
    distance_metres: 1234,
    nodes_explored: 77,
    vehicle_position: { latitude: 52.63, longitude: 1.29 },
    route: [],
    applied: {
      stations: 0, ambulances: 0, closures: 0, accidents: 0, fleet_vehicles: 0,
      ...applied,
    },
  }
}

describe("canRestart", () => {
  it("allows every run that is no longer live", () => {
    for (const status of ["stopped", "replaced", "interrupted"] as const) {
      assert.equal(canRestart(run({ status })), true, status)
    }
  })

  it("does not restart a live run", () => {
    assert.equal(canRestart(run({ status: "active", live: true })), false)
  })
})

describe("canResume", () => {
  it("resumes interrupted and cancelled studies only", () => {
    assert.equal(canResume(study("interrupted")), true)
    assert.equal(canResume(study("cancelled")), true)
    assert.equal(canResume(study("completed")), false)
    assert.equal(canResume(study("running")), false)
    assert.equal(canResume(study("queued")), false)
  })
})

describe("runStatusLabel", () => {
  it("says which live run is the current one", () => {
    assert.equal(runStatusLabel(run({ status: "active", live: true, is_default: true })), "running (current)")
    assert.equal(runStatusLabel(run({ status: "active", live: true })), "running")
    assert.equal(runStatusLabel(run({ status: "interrupted" })), "interrupted")
  })
})

describe("formatWhen", () => {
  it("shows a day, month and time", () => {
    const text = formatWhen("2026-11-16T09:05:00Z")
    assert.match(text, /16 Nov/)
    assert.match(text, /\d{2}:05/)
  })

  it("leaves text that is not a date as it is", () => {
    assert.equal(formatWhen("not a date"), "not a date")
  })
})

describe("restartSummary", () => {
  it("lists what the restart put back", () => {
    assert.equal(
      restartSummary(restarted({ stations: 1, closures: 2, accidents: 1, fleet_vehicles: 40 })),
      "Restarted from a saved run with the same route, 1 station, 2 closures, " +
        "1 accident, 40 fleet vehicles. Vehicles start again from the beginning."
    )
  })

  it("is short when there was only the route", () => {
    assert.equal(
      restartSummary(restarted()),
      "Restarted from a saved run with the same route. Vehicles start again from the beginning."
    )
  })
})
