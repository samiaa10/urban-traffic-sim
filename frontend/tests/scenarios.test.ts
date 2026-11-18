// Tests for the scenario screen's logic (src/scenarios.ts): turning what
// is on screen into study requests, formatting, charts, requests.

import assert from "node:assert/strict"
import { describe, it } from "node:test"

import { setApiBase } from "../src/optimiser.ts"
import {
  describeDetail,
  draftToSpec,
  formatSeconds,
  formatValue,
  binLabel,
  histogram,
  isFinished,
  linePoints,
  pickedRoadLabel,
  progressLabel,
  randomClosureRequest,
  request,
  roadLabel,
  studyRequest,
  type Draft,
  type Study,
} from "../src/scenarios.ts"

function draft(overrides: Partial<Draft> = {}): Draft {
  return {
    key: 1,
    name: "Baseline",
    vehicles: 500,
    seed: 1,
    rerouting: true,
    surge: false,
    closures: [],
    accidents: [],
    ...overrides,
  }
}

describe("draftToSpec", () => {
  it("turns a plain draft into a scenario with spread departures", () => {
    assert.deepEqual(draftToSpec(draft()), {
      name: "Baseline",
      seed: 1,
      demand: { vehicles: 500, spread_seconds: 300 },
      rerouting: true,
      closures: [],
      accidents: [],
      surges: [],
    })
  })

  it("adds a flood or bridge failures, with resilience measures, only when chosen", () => {
    const plain = draftToSpec(draft())
    assert.equal("floods" in plain || "failures" in plain || "resilience" in plain, false)
    const flooded = draftToSpec(draft({ flood: true }))
    assert.equal(flooded.floods?.[0].severity, "closed")
    assert.equal(flooded.resilience, true)
    const bridges = draftToSpec(draft({ bridgesOut: true }))
    assert.ok(bridges.failures?.[0].names.includes("Whitefriars"))
    assert.equal(bridges.resilience, true)
  })

  it("adds traffic signals only when chosen", () => {
    assert.equal("signals" in draftToSpec(draft()), false)
    assert.deepEqual(draftToSpec(draft({ signals: "fixed" })).signals, { controller: "fixed", emergency_priority: false })
    assert.deepEqual(draftToSpec(draft({ signals: "priority" })).signals, { controller: "adaptive", emergency_priority: true })
  })

  it("names a traffic model only when one was chosen (otherwise the server's default)", () => {
    assert.equal("traffic_model" in draftToSpec(draft()), false)
    assert.equal(draftToSpec(draft({ trafficModel: "ca" })).traffic_model, "ca")
  })

  it("keeps only the road of each closure and adds accident timing", () => {
    const spec = draftToSpec(draft({
      closures: [{ start: 1, destination: 2, label: "1-2" }],
      accidents: [{ start: 3, destination: 4, label: "3-4", severity: "minor", start_at: 60 }],
    }))

    assert.deepEqual(spec.closures, [{ start: 1, destination: 2 }])
    assert.deepEqual(spec.accidents, [
      { start: 3, destination: 4, severity: "minor", start_at: 60, duration_seconds: 900 },
    ])
  })

  it("adds a city-centre surge when asked", () => {
    const spec = draftToSpec(draft({ surge: true }))

    assert.equal(spec.surges.length, 1)
    assert.equal(spec.surges[0].direction, "inbound")
    assert.equal(spec.surges[0].vehicles_per_minute, 120)
  })

  it("never sends an empty name", () => {
    assert.equal(draftToSpec(draft({ name: "   " })).name, "Scenario")
  })
})

describe("studyRequest", () => {
  it("sends every scenario and the baseline's position", () => {
    const body = studyRequest("Test", [draft({ key: 1 }), draft({ key: 2, name: "B" })], 2, "mean_increase_seconds")

    assert.equal(body.scenarios.length, 2)
    assert.equal(body.baseline, 1)
    assert.equal(body.metric, "mean_increase_seconds")
  })

  it("sends no baseline when none is chosen", () => {
    assert.equal(studyRequest("", [draft()], null, "x").baseline, null)
    assert.equal(studyRequest("", [draft()], null, "x").name, "Study")
  })
})

describe("randomClosureRequest", () => {
  it("asks for a random-closure sweep around the centre from the base", () => {
    const body = randomClosureRequest(
      draft({ closures: [{ start: 1, destination: 2, label: "1-2" }], seed: 3 }),
      100, 3, 1500, "total_delay_vehicle_hours"
    )

    assert.equal(body.kind, "random_closures")
    assert.equal(body.count, 100)
    assert.equal(body.closures_per_scenario, 3)
    assert.equal(body.zone.radius_metres, 1500)
    assert.equal(body.sweep_seed, 3)
    // The sweep chooses the closures; the base has none of its own
    assert.deepEqual(body.base.closures, [])
  })
})

describe("formatting", () => {
  it("formats each unit", () => {
    assert.equal(formatValue(12.345, "vehicle-hours"), "12.3 veh-h")
    // Below 10, hundredths: 0.71 and 0.74 must not both read 0.7
    assert.equal(formatValue(0.714, "vehicle-hours"), "0.71 veh-h")
    assert.equal(formatValue(87.4, "%"), "87%")
    assert.equal(formatValue(95, "s"), "1 min 35 s")
    assert.equal(formatValue(null, "s"), "-")
  })

  it("formats seconds as minutes and seconds", () => {
    assert.equal(formatSeconds(42), "42 s")
    assert.equal(formatSeconds(120), "2 min")
    assert.equal(formatSeconds(-75), "-1 min 15 s")
  })

  it("describes a study's progress and whether it is over", () => {
    const study = { completed: 3, failed: 1, scenario_count: 10, status: "running" } as Study

    assert.equal(progressLabel(study), "4 of 10 scenarios, 1 failed")
    assert.equal(isFinished(study), false)
    assert.equal(isFinished({ ...study, status: "cancelled" }), true)
  })
})

describe("charts", () => {
  it("scales a series into the box with y growing upwards", () => {
    const points = linePoints([{ x: 0, y: 0 }, { x: 5, y: 10 }, { x: 10, y: 5 }], 100, 50, 10, 10)

    assert.equal(points, "0.0,50.0 50.0,0.0 100.0,25.0")
    assert.equal(linePoints([{ x: 1, y: 1 }], 100, 50, 0, 0), "")
  })

  it("counts values into equal bins", () => {
    const bins = histogram([1, 2, 2, 3, 9, 10], 3)

    assert.deepEqual(bins.map((bin) => bin.count), [4, 0, 2])
    assert.equal(bins[0].from, 1)
    assert.equal(bins[2].to, 10)
    assert.deepEqual(histogram([5, 5], 4).map((bin) => bin.count), [2, 0, 0, 0])
    assert.deepEqual(histogram([], 4), [])
  })

  it("labels bins with enough decimals that their ends differ", () => {
    assert.equal(binLabel({ from: 1, to: 4 }), "1.0-4.0")
    assert.equal(binLabel({ from: 0.7125, to: 0.725 }), "0.71-0.72")
    assert.equal(binLabel({ from: 0.7, to: 0.7 }), "0.7-0.7")
  })
})

describe("requests", () => {
  it("shows the backend's reason, including a list of problems", async () => {
    setApiBase("http://test")
    const fake = async () =>
      ({ ok: false, json: async () => ({ detail: ["scenarios[1]: bad", "scenarios[2]: worse"] }) }) as Response

    await assert.rejects(request("/scenarios/studies", undefined, fake), /scenarios\[1\]: bad; scenarios\[2\]: worse/)
  })

  it("reads the detail whatever its shape", () => {
    assert.equal(describeDetail("No database", "x"), "No database")
    assert.equal(describeDetail([{ msg: "?" }], "Fallback"), "Fallback")
  })

  it("uses the configured backend address", async () => {
    setApiBase("http://example.test/api/")
    const urls: string[] = []
    const fake = async (url: string) => {
      urls.push(url)
      return { ok: true, json: async () => ({}) } as Response
    }

    await request("/scenarios/presets", undefined, fake)

    assert.deepEqual(urls, ["http://example.test/api/scenarios/presets"])
  })
})

describe("road labels", () => {
  const point = { latitude: 52.63, longitude: 1.29 }

  it("names a picked road when the map knows its name", () => {
    const road = { start: 11, destination: 12, start_point: point, end_point: point }
    assert.equal(pickedRoadLabel({ ...road, name: "Prince of Wales Road" }), "Prince of Wales Road")
    assert.equal(pickedRoadLabel({ ...road, name: null }), "11-12")
    assert.equal(pickedRoadLabel(road), "11-12")
  })

  it("names a delayed road, or falls back to its junctions", () => {
    assert.equal(roadLabel({ start_node: 1, end_node: 2, name: "Riverside Road" }), "Riverside Road")
    assert.equal(roadLabel({ start_node: 1, end_node: 2, name: null }), "1-2")
    assert.equal(roadLabel({ start_node: null, end_node: null }), "part of a road (after a reroute)")
  })
})
