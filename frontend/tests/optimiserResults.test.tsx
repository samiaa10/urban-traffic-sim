// Renders the optimiser's result tables to HTML and checks what a person
// would read. No browser is needed: react-dom/server turns the components
// into a string.

import assert from "node:assert/strict"
import { describe, it } from "node:test"
import { renderToStaticMarkup } from "react-dom/server"

import type {
  OptimiserResponse,
  OptimiserResult,
  StationSetBaseline,
  StationSetResponse,
} from "../src/optimiser.ts"
import {
  RankingResults,
  SHOWN,
  StationSetResults,
} from "../src/OptimiserResults.tsx"

function result(rank: number, overrides: Partial<OptimiserResult> = {}): OptimiserResult {
  return {
    node: 1000 + rank,
    latitude: 52.6 + rank / 1000,
    longitude: 1.29,
    average_seconds: 150 + rank * 6,
    worst_seconds: 300 + rank * 6,
    within_target_percent: 99.6,
    unreachable: 2,
    rank,
    ...overrides,
  }
}

function ranking(count: number, overrides: Partial<OptimiserResponse> = {}): OptimiserResponse {
  return {
    scenarios: 500,
    target_seconds: 480,
    speed: 20,
    closed_roads: 0,
    explanation: ["Candidate #1 has the lowest average response time."],
    results: Array.from({ length: count }, (_, index) => result(index + 1)),
    ...overrides,
  }
}

function baseline(name: string, label: string, stations: number, average: number, overrides: Partial<StationSetBaseline> = {}): StationSetBaseline {
  return {
    name,
    label,
    stations,
    average_seconds: average,
    worst_seconds: average * 2,
    within_target_percent: 90,
    unreachable: 2,
    ...overrides,
  }
}

function stationSet(overrides: Partial<StationSetResponse> = {}): StationSetResponse {
  return {
    count: 3,
    candidates_considered: 38,
    scenarios: 500,
    target_seconds: 240,
    speed: 20,
    closed_roads: 0,
    explanation: [
      "3 stations together average 1.8 min per emergency.",
      "On average it is 33% faster than the best single station (2.7 min).",
    ],
    stations: [
      { order: 1, node: 11, latitude: 52.6356, longitude: 1.2732, average_seconds_after: 160, within_target_percent_after: 85.2 },
      { order: 2, node: 12, latitude: 52.6254, longitude: 1.2926, average_seconds_after: 132, within_target_percent_after: 88.4 },
      { order: 3, node: 13, latitude: 52.6426, longitude: 1.2282, average_seconds_after: 108, within_target_percent_after: 98.6 },
    ],
    result: { average_seconds: 108, worst_seconds: 252, within_target_percent: 98.6, unreachable: 2 },
    baselines: [
      baseline("best_single", "Best single station", 1, 160),
      baseline("top_individual", "Top 3 ranked one by one", 3, 120),
      baseline("random", "3 random stations (average of 100)", 3, 162, { unreachable: 1.88, within_target_percent: 84.6 }),
    ],
    ...overrides,
  }
}

const html = (element: React.ReactElement) => renderToStaticMarkup(element)

describe("RankingResults (one station)", () => {
  it("recommends candidate #1 and shows the explanation", () => {
    const page = html(<RankingResults data={ranking(5)} />)

    assert.match(page, /Recommended: candidate #1 at/)
    assert.match(page, /52\.6010, 1\.2900/)
    assert.match(page, /lowest average response time/)
  })

  it("shows average, worst case, coverage and unreached for each candidate", () => {
    const page = html(<RankingResults data={ranking(2)} />)

    assert.match(page, /2\.6 min/)
    assert.match(page, /5\.1 min/)
    assert.match(page, /100%/)
    assert.match(page, /<th[^>]*>Within 8\.0 min<\/th>/)
    assert.match(page, /<th[^>]*>Unreached<\/th>/)
  })

  it("highlights only the top-ranked row", () => {
    const page = html(<RankingResults data={ranking(3)} />)

    assert.equal(page.match(/font-weight:bold/g)?.length, 1)
  })

  it(`lists at most ${SHOWN} candidates but says how many there were`, () => {
    const page = html(<RankingResults data={ranking(38)} />)

    assert.equal(page.match(/<tr/g)?.length, SHOWN + 1) // + header row
    assert.match(page, /Top 10 of 38 candidates/)
  })

  it("says top 3 of 3 when there are only three", () => {
    assert.match(html(<RankingResults data={ranking(3)} />), /Top 3 of 3 candidates/)
  })

  it("shows n/a for a candidate that could not reach anything", () => {
    const data = ranking(1)
    data.results[0] = result(1, { average_seconds: null, worst_seconds: null, within_target_percent: 0, unreachable: 500 })

    const page = html(<RankingResults data={data} />)

    assert.match(page, /n\/a/)
    assert.match(page, />500</)
  })

  it("mentions closed roads only when some are closed", () => {
    assert.doesNotMatch(html(<RankingResults data={ranking(2)} />), /closed road/)
    assert.match(
      html(<RankingResults data={ranking(2, { closed_roads: 3 })} />),
      /avoiding 3 closed road segments/
    )
  })

  it("states the ambulance speed in km/h", () => {
    assert.match(html(<RankingResults data={ranking(2, { speed: 20 })} />), /72 km\/h/)
  })

  it("handles an empty result list", () => {
    assert.match(
      html(<RankingResults data={ranking(0)} />),
      /No candidate stations were found/
    )
  })
})

describe("StationSetResults (several stations)", () => {
  it("lists the chosen stations in order, with the average after each", () => {
    const page = html(<StationSetResults data={stationSet()} />)

    assert.match(page, /Recommended: 3 stations/)
    assert.match(page, /52\.6356, 1\.2732 - average with stations 1-1: 2\.7 min/)
    assert.match(page, /average with stations 1-2: 2\.2 min/)
    assert.match(page, /average with stations 1-3: 1\.8 min/)
  })

  it("compares the chosen set with every baseline", () => {
    const page = html(<StationSetResults data={stationSet()} />)

    for (const label of [
      "Chosen 3 stations",
      "Best single station",
      "Top 3 ranked one by one",
      "3 random stations (average of 100)",
    ]) {
      assert.ok(page.includes(label), `missing row: ${label}`)
    }

    // header + chosen + 3 baselines
    assert.equal(page.match(/<tr/g)?.length, 5)
  })

  it("shows average, worst case, coverage and unreachable for every row", () => {
    const page = html(<StationSetResults data={stationSet()} />)

    assert.match(page, /<th[^>]*>Average<\/th>/)
    assert.match(page, /<th[^>]*>Worst case<\/th>/)
    assert.match(page, /<th[^>]*>Within 4\.0 min<\/th>/)
    assert.match(page, /<th[^>]*>Unreached<\/th>/)
    assert.match(page, /98\.6%/)
    assert.match(page, /84\.6%/)
  })

  it("shows whole unreachable counts for the set and averages for random", () => {
    const page = html(<StationSetResults data={stationSet()} />)

    assert.match(page, />2</)
    assert.match(page, />1\.9</)
  })

  it("highlights only the chosen set", () => {
    const page = html(<StationSetResults data={stationSet()} />)

    assert.equal(page.match(/font-weight:bold/g)?.length, 1)
  })

  it("shows the explanation sentences", () => {
    const page = html(<StationSetResults data={stationSet()} />)

    assert.match(page, /33% faster than the best single station/)
  })

  it("uses singular wording for one station", () => {
    const page = html(
      <StationSetResults
        data={stationSet({
          count: 1,
          stations: stationSet().stations.slice(0, 1),
        })}
      />
    )

    assert.match(page, /Recommended: 1 station</)
    assert.match(page, /Chosen 1 station</)
    assert.match(page, /- average: 2\.7 min/)
  })

  it("shows n/a when the chosen set could not reach any emergency", () => {
    const data = stationSet({
      result: { average_seconds: null, worst_seconds: null, within_target_percent: 0, unreachable: 500 },
    })

    const page = html(<StationSetResults data={data} />)

    assert.match(page, /n\/a/)
    assert.match(page, />500</)
  })

  it("mentions closed roads only when some are closed", () => {
    assert.doesNotMatch(html(<StationSetResults data={stationSet()} />), /closed road/)
    assert.match(
      html(<StationSetResults data={stationSet({ closed_roads: 2 })} />),
      /avoiding 2 closed road segments/
    )
  })

  it("explains what coverage and unreached mean, with the chosen target", () => {
    const page = html(<StationSetResults data={stationSet()} />)

    assert.match(page, /share of all emergencies reached within 4\.0 min/)
    assert.match(page, /Unreached column/)
  })

  it("handles an empty answer", () => {
    assert.match(
      html(<StationSetResults data={stationSet({ stations: [] })} />),
      /No stations were chosen/
    )
  })

  it("does not let text from the server inject markup", () => {
    const page = html(
      <StationSetResults
        data={stationSet({ explanation: ["<script>alert(1)</script>"] })}
      />
    )

    assert.doesNotMatch(page, /<script>/)
    assert.match(page, /&lt;script&gt;/)
  })
})
