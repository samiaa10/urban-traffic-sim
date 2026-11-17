// Scenario analysis: building scenarios (with roads picked on the map),
// running them as a study, following its progress, and reading the
// ranking, explanation, charts and details. The backend is faked.

import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import App from "../src/App.tsx"
import { COLOURS } from "../src/mapStyles.ts"
import {
  START,
  clickMap,
  controlPolling,
  lines,
  mockBackend,
  type Backend,
} from "./helpers.ts"

vi.mock("react-leaflet", async () => await import("./fakeLeaflet.tsx"))

const ROAD = {
  start: 11,
  destination: 12,
  start_point: { latitude: 52.63, longitude: 1.29 },
  end_point: { latitude: 52.631, longitude: 1.291 },
  distance_metres: 3,
}

const definition = {
  key: "total_delay_vehicle_hours",
  label: "Network delay",
  unit: "vehicle-hours",
  better: "lower",
  definition: "Time lost to traffic, accidents and closures, summed over all trips",
}

function metrics(delay: number, increase: number) {
  return {
    total_delay_vehicle_hours: delay,
    total_travel_vehicle_hours: delay * 3,
    mean_increase_seconds: increase,
    p95_increase_seconds: increase * 2,
    max_increase_seconds: increase * 4,
    mean_delay_seconds: increase / 2,
    completion_percent: 100,
    trips_unroutable: 0,
    emergency_mean_response_seconds: null,
    emergency_worst_response_seconds: null,
    emergency_within_target_percent: null,
  }
}

function study(status: "running" | "completed") {
  const done = status === "completed"
  return {
    study_id: "study-1",
    name: "My study",
    status,
    metric: definition.key,
    scenario_count: 2,
    completed: done ? 2 : 0,
    failed: 0,
    progress_percent: done ? 100 : 0,
    workers: 4,
    wall_seconds: done ? 31 : null,
    scenarios: [
      { scenario_id: "a", position: 0, name: "Baseline", is_baseline: true, seed: 1,
        status: done ? "completed" : "running", error: null, metrics: done ? metrics(11.8, 107) : null, wall_seconds: 12 },
      { scenario_id: "b", position: 1, name: "Road closure", is_baseline: false, seed: 1,
        status: done ? "completed" : "queued", error: null, metrics: done ? metrics(14.2, 131) : null, wall_seconds: 13 },
    ],
    ranking_metric: definition,
    ranking: done
      ? [
          { rank: 1, id: "a", name: "Baseline", value: 11.8, gap_to_best: 0, gap_percent: 0, metrics: metrics(11.8, 107) },
          { rank: 2, id: "b", name: "Road closure", value: 14.2, gap_to_best: 2.4, gap_percent: 20, metrics: metrics(14.2, 131) },
        ]
      : null,
    explanation: done
      ? [
          "Baseline ranks first on network delay (11.8 vehicle-hours); Road closure is second (14.2 vehicle-hours, 2.4 vehicle-hours more, 20%).",
          "Trip by trip (the same 500 completed trips in both): 212 took longer in Road closure.",
        ]
      : [],
  }
}

function detail(id: string, name: string) {
  return {
    scenario_id: id,
    name,
    is_baseline: id === "a",
    status: "completed",
    spec: { closures: id === "b" ? [{ start: 11, destination: 12 }] : [] },
    disruptions: id === "b"
      ? [{ kind: "closure", start_node: 11, end_node: 12,
           start: ROAD.start_point, end: ROAD.end_point }]
      : [],
    result: {
      summary: {},
      metrics: metrics(id === "a" ? 11.8 : 14.2, 107),
      roads: [1, 2, 3].map((rank) => ({
        rank, start_node: 100 + rank, end_node: 200 + rank,
        start: { latitude: 52.63, longitude: 1.29 + rank / 1000 },
        end: { latitude: 52.631, longitude: 1.29 + rank / 1000 },
        delay_vehicle_seconds: 1000 / rank, vehicle_seconds: 2000, peak_vehicles: 7, delay_share: 0.5,
      })),
      areas: [],
      timeseries: [
        { time: 60, active_vehicles: 400, delay_vehicle_seconds: 600, mean_speed_factor: 0.8 },
        { time: 120, active_vehicles: 200, delay_vehicle_seconds: 300, mean_speed_factor: 0.9 },
      ],
      trip_counts: { finished: 500, unfinished: 0, unroutable: 0 },
    },
    vs_baseline: id === "b"
      ? { baseline_name: "Baseline",
          paired: { trips_compared: 500, extra_vehicle_hours: 2.4, trips_slower: 212,
                    trips_faster: 40, worst_increase_seconds: 410, newly_cut_off: 0 } }
      : null,
  }
}

function scenarioBackend(overrides: Backend = {}): Backend {
  let checks = 0
  return {
    "GET /scenarios/nearest-road": { body: ROAD },
    "POST /scenarios/studies": { status: 202, body: { study_id: "study-1" } },
    "POST /scenarios/studies/sweep": { status: 202, body: { study_id: "study-1" } },
    // Running at the first check, completed after that
    "GET /scenarios/studies/study-1": () => ({ body: study(checks++ === 0 ? "running" : "completed") }),
    "POST /scenarios/studies/study-1/cancel": { body: { status: "study cancelled" } },
    "GET /scenarios/a": { body: detail("a", "Baseline") },
    "GET /scenarios/b": { body: detail("b", "Road closure") },
    ...overrides,
  }
}

const builder = () => screen.getByRole("region", { name: "Scenario builder" })
const draftCard = (name: string) =>
  within(builder()).getByRole("listitem", { name: `Scenario ${name}` })

async function pickClosure(user: ReturnType<typeof userEvent.setup>) {
  await user.click(within(draftCard("Road closure")).getByRole("button", { name: "Close a road" }))
  clickMap(START)
  return within(draftCard("Road closure")).findByText("Closed 11-12")
}

describe("building scenarios", () => {
  it("starts with a baseline and a road-closure scenario, and asks nothing yet", () => {
    const backend = mockBackend(scenarioBackend())
    render(<App />)

    expect(screen.getByRole("heading", { name: "Scenario analysis" })).toBeTruthy()
    expect(draftCard("Baseline")).toBeTruthy()
    expect(draftCard("Road closure")).toBeTruthy()
    expect(within(draftCard("Baseline")).getByRole("radio", { name: "Baseline" })).toHaveProperty("checked", true)
    expect(backend.paths()).toEqual([])
  })

  it("picks a road on the map for a closure, without starting a route", async () => {
    const backend = mockBackend(scenarioBackend())
    const user = userEvent.setup()
    render(<App />)

    await user.click(within(draftCard("Road closure")).getByRole("button", { name: "Close a road" }))
    expect(screen.getByText("Road to close for Road closure")).toBeTruthy()

    clickMap(START)

    expect(await within(draftCard("Road closure")).findByText("Closed 11-12")).toBeTruthy()
    const asked = backend.last("GET /scenarios/nearest-road")?.query
    expect(asked?.get("latitude")).toBe(String(START.lat))
    // The click chose a road, not a route start
    expect(screen.getByText(/Next click/).textContent).toContain("Start point")
    // and the picked road is shown on the map
    expect(lines(COLOURS.closure)).toHaveLength(1)
  })

  it("names a picked road when the map knows it", async () => {
    mockBackend(scenarioBackend({
      "GET /scenarios/nearest-road": { body: { ...ROAD, name: "Prince of Wales Road" } },
    }))
    const user = userEvent.setup()
    render(<App />)

    await user.click(within(draftCard("Road closure")).getByRole("button", { name: "Close a road" }))
    clickMap(START)

    expect(await within(draftCard("Road closure")).findByText("Closed Prince of Wales Road")).toBeTruthy()
  })

  it("adds and removes scenarios", async () => {
    mockBackend(scenarioBackend())
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole("button", { name: "Add scenario" }))
    expect(draftCard("Scenario 3")).toBeTruthy()
    expect(screen.getByRole("button", { name: "Run 3 scenarios" })).toBeTruthy()

    await user.click(screen.getByRole("button", { name: "Remove Scenario 3" }))
    expect(screen.getByRole("button", { name: "Run 2 scenarios" })).toBeTruthy()
  })
})

describe("running a study", () => {
  it("sends the scenarios, shows progress, then the ranking and why", async () => {
    const backend = mockBackend(scenarioBackend())
    const polling = controlPolling()
    const user = userEvent.setup()
    render(<App />)
    await pickClosure(user)

    await user.click(screen.getByRole("button", { name: "Run 2 scenarios" }))

    const sent = backend.last("POST /scenarios/studies")?.body as {
      scenarios: { name: string; closures: unknown[] }[]
      baseline: number
      metric: string
    }
    expect(sent.scenarios.map((s) => s.name)).toEqual(["Baseline", "Road closure"])
    expect(sent.scenarios[1].closures).toEqual([{ start: 11, destination: 12 }])
    expect(sent.baseline).toBe(0)
    expect(sent.metric).toBe("total_delay_vehicle_hours")

    expect(await screen.findByText(/0 of 2 scenarios/)).toBeTruthy()
    expect(screen.getByRole("button", { name: "Cancel study" })).toBeTruthy()

    await polling.tick()

    const table = await screen.findByRole("table", { name: "Scenario ranking" })
    const rows = within(table).getAllByRole("row").slice(1)
    expect(rows.map((row) => within(row).getAllByRole("cell")[1].textContent)).toEqual([
      "Baseline", "Road closure",
    ])
    // Each name opens that scenario's details
    expect(within(rows[1]).getByRole("button", { name: "Show details of Road closure" })).toBeTruthy()
    expect(within(rows[0]).getByText("11.8 veh-h")).toBeTruthy()
    // The ranked metric is not repeated in another column
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent)
    expect(headers.filter((h) => h === "Network delay")).toHaveLength(1)
    expect(screen.getByText(/2 of 2 scenarios in 31 s/)).toBeTruthy()
    expect(screen.getByText(/212 took longer in Road closure/)).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Cancel study" })).toBeNull()
    // Polling stops once the study is over
    expect(polling.running()).toBe(0)
    // The delay-over-time chart, from each scenario's time series
    expect(await screen.findByRole("img", { name: /Delay per simulated minute/ })).toBeTruthy()
  })

  it("shows a scenario's most delayed roads and draws them on the map", async () => {
    mockBackend(scenarioBackend())
    const polling = controlPolling()
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole("button", { name: "Run 2 scenarios" }))
    await polling.tick()
    const table = await screen.findByRole("table", { name: "Scenario ranking" })
    await user.click(within(table).getByRole("button", { name: "Show details of Road closure" }))

    const card = await screen.findByRole("region", { name: "Details of Road closure" })
    const roads = within(card).getByRole("table", { name: "Roads that lost the most time" })
    expect(within(roads).getAllByRole("row")).toHaveLength(4)
    expect(within(card).getByText(/212 of 500 trips took longer/)).toBeTruthy()

    expect(lines(COLOURS.delayHotspot)).toHaveLength(3)
    expect(lines(COLOURS.closure)).toHaveLength(1)
    expect(screen.getByText("Most time lost")).toBeTruthy()
  })

  it("can cancel a running study", async () => {
    const backend = mockBackend(scenarioBackend())
    controlPolling()
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole("button", { name: "Run 2 scenarios" }))
    await user.click(await screen.findByRole("button", { name: "Cancel study" }))

    expect(backend.count("POST /scenarios/studies/study-1/cancel")).toBe(1)
  })

  it("sends a large random-closure study as a sweep", async () => {
    const backend = mockBackend(scenarioBackend())
    controlPolling()
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByText("Large study: random closures"))
    await user.selectOptions(screen.getByRole("combobox", { name: "Scenarios" }), "100")
    await user.click(screen.getByRole("button", { name: "Run random-closure study" }))

    const sent = backend.last("POST /scenarios/studies/sweep")?.body as {
      kind: string
      count: number
      closures_per_scenario: number
    }
    expect(sent.kind).toBe("random_closures")
    expect(sent.count).toBe(100)
    expect(sent.closures_per_scenario).toBe(3)
  })
})

describe("when the backend refuses", () => {
  it("lists the problems with the scenarios", async () => {
    mockBackend(scenarioBackend({
      "POST /scenarios/studies": {
        status: 400,
        body: { detail: ["scenarios[1].closures[0]: junctions 1 and 9 are not directly connected by a road"] },
      },
    }))
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole("button", { name: "Run 2 scenarios" }))

    expect((await screen.findByRole("alert")).textContent).toContain("not directly connected")
  })

  it("says so when the backend has no database", async () => {
    mockBackend(scenarioBackend({
      "POST /scenarios/studies": {
        status: 503,
        body: { detail: "Scenario studies need a database (DATABASE_URL is not set)" },
      },
    }))
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole("button", { name: "Run 2 scenarios" }))

    expect((await screen.findByRole("alert")).textContent).toContain("DATABASE_URL")
  })
})
