// The History section: listing saved runs and studies, a run's saved
// history, restarting an ended run (the map then follows the new run),
// and opening or resuming a past study. The backend is faked.

import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import App from "../src/App.tsx"
import { COLOURS } from "../src/mapStyles.ts"
import {
  car,
  controlPolling,
  lines,
  mockBackend,
  type Backend,
} from "./helpers.ts"

vi.mock("react-leaflet", async () => await import("./fakeLeaflet.tsx"))

const route = [0, 1, 2, 3].map((i) => ({ latitude: 52.63 + i / 1000, longitude: 1.29 + i / 1000 }))

const savedRuns = [
  { simulation_id: "live-1", status: "active", live: true, is_default: true,
    created_at: "2026-11-16T10:00:00Z", ended_at: null, start: 11, destination: 22,
    simulation_time: 40, vehicles: 1, restarted_from: null },
  { simulation_id: "old-1", status: "interrupted", live: false, is_default: false,
    created_at: "2026-11-15T09:00:00Z", ended_at: "2026-11-15T09:30:00Z", start: 11, destination: 22,
    simulation_time: 300, vehicles: 41, restarted_from: null },
]

const savedStudies = [
  { study_id: "study-1", name: "Closures", status: "interrupted", scenario_count: 2,
    completed: 1, failed: 0, created_at: "2026-11-15T09:00:00Z" },
  { study_id: "study-2", name: "Demand sweep", status: "completed", scenario_count: 2,
    completed: 2, failed: 0, created_at: "2026-11-14T09:00:00Z" },
]

function runningStudy(id: string, name: string) {
  return {
    study_id: id, name, status: "running", metric: "total_delay_vehicle_hours",
    scenario_count: 2, completed: 1, failed: 0, progress_percent: 50, workers: 2, wall_seconds: null,
    scenarios: [
      { scenario_id: "a", position: 0, name: "Baseline", is_baseline: true, seed: 1,
        status: "completed", error: null, metrics: null, wall_seconds: 12 },
      { scenario_id: "b", position: 1, name: "Closure", is_baseline: false, seed: 1,
        status: "running", error: null, metrics: null, wall_seconds: null },
    ],
    ranking_metric: { key: "total_delay_vehicle_hours", label: "Network delay", unit: "vehicle-hours",
                      better: "lower", definition: "Time lost to traffic" },
    ranking: [], explanation: [],
  }
}

function historyBackend(overrides: Backend = {}): Backend {
  return {
    "GET /simulations": { body: { simulations: savedRuns } },
    "GET /scenarios/studies": { body: { studies: savedStudies } },
    "GET /simulations/old-1/history": {
      body: {
        simulation_id: "old-1", status: "interrupted",
        counts: { closures: 2, accidents: 1, emergencies: 3, emergencies_arrived: 2,
                  stations: 1, fleets: 1, events: 17 },
        mean_response_seconds: 245, restarted_from: null,
      },
    },
    "POST /simulations/old-1/restart": {
      status: 201,
      body: {
        status: "simulation restarted", simulation_id: "new-1", restarted_from: "old-1",
        distance_metres: 1234, nodes_explored: 77,
        vehicle_position: route[0], route,
        applied: { stations: 1, ambulances: 2, closures: 2, accidents: 1, fleet_vehicles: 40 },
      },
    },
    "POST /simulations/new-1/step": { body: { vehicles: [car()] } },
    "GET /simulations/new-1/congestion": { body: { congestion: [] } },
    "GET /simulations/new-1/emergencies/status": { body: { simulation_time: 1, emergencies: [] } },
    "POST /scenarios/studies/study-1/resume": { body: { status: "study resumed", requeued: 1 } },
    "GET /scenarios/studies/study-1": { body: runningStudy("study-1", "Closures") },
    "GET /scenarios/studies/study-2": { body: runningStudy("study-2", "Demand sweep") },
    ...overrides,
  }
}

async function loadHistory() {
  const user = userEvent.setup()
  render(<App />)
  await user.click(screen.getByRole("button", { name: "Load saved runs and studies" }))
  await screen.findByRole("table", { name: "Saved simulation runs" })
  return user
}

const runRow = (id: string) => {
  const table = screen.getByRole("table", { name: "Saved simulation runs" })
  const rows = within(table).getAllByRole("row").slice(1)
  return rows[savedRuns.findIndex((run) => run.simulation_id === id)]
}

describe("the History section", () => {
  it("asks for nothing until it is opened", () => {
    const backend = mockBackend(historyBackend())
    render(<App />)

    expect(screen.getByRole("heading", { name: "Saved runs and studies" })).toBeTruthy()
    expect(screen.getByRole("link", { name: "History" }).getAttribute("href")).toBe("#history")
    expect(backend.paths()).toEqual([])
  })

  it("lists the saved runs and studies", async () => {
    const backend = mockBackend(historyBackend())
    await loadHistory()

    expect(backend.last("GET /simulations")?.query.get("limit")).toBe("20")
    expect(within(runRow("live-1")).getByText("running (current)")).toBeTruthy()
    expect(within(runRow("old-1")).getByText("interrupted")).toBeTruthy()
    // A live run cannot be restarted; an interrupted one can
    expect(within(runRow("live-1")).queryByRole("button", { name: "Restart" })).toBeNull()
    expect(within(runRow("old-1")).getByRole("button", { name: "Restart" })).toBeTruthy()

    const studies = screen.getByRole("table", { name: "Saved scenario studies" })
    expect(within(studies).getByRole("button", { name: "Resume Closures" })).toBeTruthy()
    expect(within(studies).queryByRole("button", { name: "Resume Demand sweep" })).toBeNull()
    expect(within(studies).getByText("1 of 2")).toBeTruthy()
    expect(screen.getByRole("button", { name: "Refresh" })).toBeTruthy()
  })

  it("shows a run's saved history", async () => {
    mockBackend(historyBackend())
    const user = await loadHistory()

    await user.click(within(runRow("old-1")).getByRole("button", { name: "Details" }))

    const facts = await screen.findByRole("list", { name: "Saved history" })
    expect(within(facts).getByText("2 closure(s), 1 accident(s)")).toBeTruthy()
    expect(within(facts).getByText(/2 of 3 emergencies reached/).textContent).toContain("mean response")
  })

  it("restarts a run and follows the new one on the map", async () => {
    const backend = mockBackend(historyBackend())
    const polling = controlPolling()
    const user = await loadHistory()

    await user.click(within(runRow("old-1")).getByRole("button", { name: "Restart" }))

    expect(await screen.findByText(/Restarted from a saved run with the same route, 1 station, 2 closures/)).toBeTruthy()
    expect(backend.count("POST /simulations/old-1/restart")).toBe(1)
    // Its route is drawn, and it is stepped by its own ID
    expect(lines(COLOURS.route)).toHaveLength(1)
    expect(polling.running()).toBe(1)

    await polling.tick()

    expect(backend.paths()).toContain("POST /simulations/new-1/step")
    expect(backend.paths()).toContain("GET /simulations/new-1/congestion")
    expect(screen.getByText(/Route: /)).toBeTruthy()
  })

  it("resumes an interrupted study and shows it in the scenario results", async () => {
    const backend = mockBackend(historyBackend())
    controlPolling()
    const user = await loadHistory()

    await user.click(screen.getByRole("button", { name: "Resume Closures" }))

    expect(await screen.findByText(/Resumed Closures/)).toBeTruthy()
    expect(backend.count("POST /scenarios/studies/study-1/resume")).toBe(1)
    expect(await screen.findByText(/1 of 2 scenarios/)).toBeTruthy()
    expect(backend.paths()).toContain("GET /scenarios/studies/study-1")
  })

  it("opens a past study without changing it", async () => {
    const backend = mockBackend(historyBackend())
    controlPolling()
    const user = await loadHistory()

    await user.click(screen.getByRole("button", { name: "Open Demand sweep" }))

    expect(await screen.findByText(/1 of 2 scenarios/)).toBeTruthy()
    expect(backend.paths()).toContain("GET /scenarios/studies/study-2")
    expect(backend.paths().filter((path) => path.startsWith("POST"))).toEqual([])
  })

  it("says why when there is no database", async () => {
    mockBackend(historyBackend({
      "GET /simulations": { status: 503, body: { detail: "No database is configured" } },
    }))
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole("button", { name: "Load saved runs and studies" }))

    expect((await screen.findByRole("alert")).textContent).toContain("No database is configured")
    expect(screen.queryByRole("table", { name: "Saved simulation runs" })).toBeNull()
  })
})
