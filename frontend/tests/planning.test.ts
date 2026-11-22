// Tests for the planning view's logic (src/planning.ts): response-time
// bands, the map's features built from the backend's values, the morph
// between layouts, the optimisation's steps and the comparison.

import assert from "node:assert/strict"
import { describe, it } from "node:test"

import {
  HEX_DRAWN,
  METRES_PER_SECOND,
  bandOf,
  compare,
  coverageFeatures,
  differenceFeatures,
  footprint,
  interpolateSeconds,
  minutes,
  optimisationSteps,
  planQuery,
  shrink,
  stationFeatures,
  type Plan,
  type Stage,
  type Summary,
} from "../src/planning.ts"

const ring = (lon: number): [number, number][] => [[lon, 52.63], [lon + 0.001, 52.63], [lon + 0.001, 52.631], [lon, 52.63]]

const summary = (overrides: Partial<Summary>): Summary => ({
  junctions: 100, unreachable_junctions: 0, mean_seconds: 200, median_seconds: 190, p90_seconds: 300,
  worst_seconds: 400, junctions_within_target_percent: 80, population_within_target_percent: 80,
  population_beyond_target: 1000, ...overrides,
})

const stage = (label: string, seconds: (number | null)[], s: Partial<Summary>): Stage => ({
  label, summary: summary(s), cells: { seconds, within: seconds.map(() => 1), beyond_people: [0, 60, 0] },
})

// Three hexagons, one existing station and two optimised
function makePlan(withExisting = true): Plan {
  const stages = [
    stage("optimised:1", [100, 300, null], { mean_seconds: 220, population_within_target_percent: 70 }),
    stage("optimised:2", [90, 150, 500], { mean_seconds: 150, population_within_target_percent: 95,
      population_beyond_target: 200, p90_seconds: 250, worst_seconds: 500 }),
  ]
  if (withExisting) stages.unshift(stage("existing", [200, 120, null], { mean_seconds: 180, unreachable_junctions: 3 }))
  return {
    service: "fire", speed: 17, target_seconds: 240, disruption: "none", closed_roads: 0,
    model: "road distance / speed", hex_metres: 180,
    existing: withExisting
      ? [{ name: "Carrow", osm: "node/1", latitude: 52.62, longitude: 1.31,
          snapped: { latitude: 52.6201, longitude: 1.3101 }, snap_metres: 12 }]
      : [],
    existing_left_out: [],
    candidates: Array.from({ length: 8 }, (_, i) => ({
      latitude: 52.6 + i / 100, longitude: 1.27, rank: i + 1, average_seconds: 200 + i, within_target_percent: 50,
    })),
    search: { candidates: 8, possible_sets: 28, method: "every possible set" },
    optimised: [
      { order: 1, candidate: 3, latitude: 52.63, longitude: 1.29, average_seconds: 220, worst_seconds: 600, within_target_percent: 70 },
      { order: 2, candidate: 5, latitude: 52.64, longitude: 1.3, average_seconds: 150, worst_seconds: 500, within_target_percent: 95 },
    ],
    cells: [0, 1, 2].map((i) => ({ polygon: ring(1.28 + i / 100), junctions: 4, population: 500 })),
    stages,
  }
}

describe("response-time bands", () => {
  it("splits the target in half, then half again beyond it", () => {
    assert.equal(bandOf(120, 240), "fast")
    assert.equal(bandOf(121, 240), "within")
    assert.equal(bandOf(240, 240), "within")
    assert.equal(bandOf(360, 240), "near")
    assert.equal(bandOf(361, 240), "slow")
    assert.equal(bandOf(null, 240), "unreachable")
  })

  it("writes minutes plainly", () => {
    assert.equal(minutes(240), "4 min")
    assert.equal(minutes(90), "1.5 min")
    // Rounded to a tenth before deciding whether it is whole
    assert.equal(minutes(359), "6 min")
  })

  it("asks the backend for the chosen settings", () => {
    assert.equal(planQuery({ service: "police", stations: 4, targetMinutes: 5, disruption: "flood" }),
      "/optimiser/plan?service=police&stations=4&target_seconds=300&disruption=flood")
  })
})

describe("coverage features", () => {
  it("draws each hexagon with its own response time, nothing added", () => {
    const plan = makePlan()
    const features = coverageFeatures(plan, [100, 300, null], [0, 60, 0]).features
    assert.equal(features.length, 3)
    assert.deepEqual(features.map((f) => f.properties.band), ["fast", "near", "unreachable"])
    assert.deepEqual(features.map((f) => f.properties.seconds), [100, 300, -1])
    // Column height follows the time; an unreachable hexagon stays low
    assert.equal(features[1].properties.height, 300 * METRES_PER_SECOND)
    assert.equal(features[2].properties.height, 4)
    // Underserved: 50 or more residents beyond the target
    assert.deepEqual(features.map((f) => f.properties.underserved), [false, true, false])
    // Drawn a little smaller than the cell, about the same centre
    assert.deepEqual(features[0].geometry.coordinates, [shrink(plan.cells[0].polygon, HEX_DRAWN)])
  })

  it("shows the time saved per hexagon in the difference view", () => {
    const features = differenceFeatures(makePlan())!.features
    // Existing 200, 120, unreachable; optimised 90, 150, 500
    assert.deepEqual(features.map((f) => f.properties.saved), [110, -30, 0])
    assert.deepEqual(features.map((f) => f.properties.band), ["faster", "slower", "unreachable"])
    assert.equal(features[0].properties.colour, "#2f7fd1")
    assert.equal(features[1].properties.colour, "#e0533d")
  })

  it("has no difference to show without an existing layout", () => {
    assert.equal(differenceFeatures(makePlan(false)), null)
  })
})

describe("the morph between layouts", () => {
  it("passes through values between the two real results", () => {
    const from = [100, 200, null, 50]
    const to = [300, 100, 80, null]
    assert.deepEqual(interpolateSeconds(from, to, 0), from)
    assert.deepEqual(interpolateSeconds(from, to, 0.5), [200, 150, null, 50])  // a hexagon losing or gaining its route keeps its start value until the end
    assert.deepEqual(interpolateSeconds(from, to, 1), to)
  })
})

describe("the optimisation's steps", () => {
  it("follow the algorithm: existing, candidates, the best singles, then each pick", () => {
    const steps = optimisationSteps(makePlan())
    assert.deepEqual(steps.map((s) => s.key), ["existing", "candidates", "promising", "pick-1", "pick-2"])
    assert.deepEqual(steps.map((s) => s.coverage), ["existing", "existing", "existing", 1, 2])
    assert.deepEqual(steps.map((s) => s.proposed), [0, 0, 0, 1, 2])
    // The figures in the text are the stage's own
    assert.match(steps[4].detail, /95\.0% of residents within 4 min/)
    assert.match(steps[2].detail, /all 28 possible sets of 2/)
  })

  it("start from the candidates when there is no existing layout", () => {
    const steps = optimisationSteps(makePlan(false))
    assert.equal(steps[0].key, "candidates")
    assert.equal(steps[0].coverage, 1)
  })

  it("show the candidates, the five best, and the proposed stations", () => {
    const plan = makePlan()
    const all = stationFeatures(plan, { candidates: "all", proposed: 0 }, true)
    assert.equal(all.columns.features.filter((f) => f.properties.kind === "candidate").length, 8)
    const kinds = (step: Parameters<typeof stationFeatures>[1]) =>
      stationFeatures(plan, step, true).columns.features.map((f) => f.properties.kind)
    assert.deepEqual(kinds({ candidates: "top", proposed: 0 }),
      ["existing", "candidate", "candidate", "candidate", "candidate", "candidate"])
    assert.deepEqual(kinds({ candidates: "none", proposed: 2 }), ["existing", "proposed", "proposed"])
    // Better candidates stand taller
    const heights = all.columns.features.filter((f) => f.properties.kind === "candidate").map((f) => f.properties.height as number)
    assert.deepEqual(heights, [...heights].sort((a, b) => b - a))
    // Existing stations sit where they were snapped to the road
    const labels = stationFeatures(plan, { candidates: "none", proposed: 1 }, true).labels.features
    assert.deepEqual(labels.map((f) => f.properties.text), ["Carrow", "Proposed 1"])
    assert.deepEqual(labels[0].geometry.coordinates, [1.3101, 52.6201])
  })

  it("shrinks a hexagon about its centre", () => {
    const ring: [number, number][] = [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]
    assert.deepEqual(shrink(ring, 0.5), [[0.5, 0.5], [1.5, 0.5], [1.5, 1.5], [0.5, 1.5], [0.5, 0.5]])
  })

  it("centres a station's footprint on it, the size asked for", () => {
    const square = footprint({ latitude: 52.63, longitude: 1.29 }, 50)
    assert.equal(square.length, 5)
    assert.deepEqual(square[0], square[4])
    const height = (square[2][1] - square[0][1]) * 111_320
    const width = (square[1][0] - square[0][0]) * 111_320 * Math.cos((52.63 * Math.PI) / 180)
    assert.ok(Math.abs(height - 50) < 1e-6 && Math.abs(width - 50) < 1e-6)
  })
})

describe("the comparison", () => {
  it("puts the existing and optimised figures side by side and says which is better", () => {
    const rows = compare(makePlan())
    const row = (name: RegExp) => rows.find((r) => name.test(r.measure))!
    assert.deepEqual(row(/Residents within 4 min/), { measure: "Residents within 4 min", existing: "80.0%", optimised: "95.0%", better: "optimised" })
    assert.equal(row(/beyond/).optimised, "200")
    assert.equal(row(/Mean/).existing, "3 min")
    assert.equal(row(/Mean/).optimised, "2.5 min")
    assert.equal(row(/Worst/).better, "existing")
    assert.equal(row(/no route/).better, "optimised")
  })

  it("leaves the existing column empty when nothing is mapped", () => {
    const rows = compare(makePlan(false))
    assert.ok(rows.every((r) => r.existing === "-" && r.better === null))
  })
})
