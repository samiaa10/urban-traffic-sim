// Stage 5 in the interface: the traffic model of a run, emergencies of
// each service (and the queue), the optimiser's service and population
// options, and the optimiser's answers tested in traffic. Backend faked.

import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import App from "../src/App.tsx"
import {
  DESTINATION,
  START,
  clickMap,
  controlPolling,
  mockBackend,
  rankingAnswer,
  workingBackend,
  type Backend,
} from "./helpers.ts"

vi.mock("react-leaflet", async () => await import("./fakeLeaflet.tsx"))

async function calculateRoute(backend: Backend = workingBackend()) {
  const fake = mockBackend(backend)
  controlPolling()
  const user = userEvent.setup()
  render(<App />)
  clickMap(START)
  clickMap(DESTINATION)
  await user.click(screen.getByRole("button", { name: /Calculate Route/ }))
  await screen.findByText(/Route: /)
  return { user, fake }
}

describe("the traffic model", () => {
  it("is sent with the run (realistic by default, simple when chosen)", async () => {
    const { user, fake } = await calculateRoute()
    expect(fake.last("POST /simulation/start")?.query.get("traffic_model")).toBe("osm")

    await user.selectOptions(screen.getByRole("combobox", { name: "Traffic model" }), "simple")
    await user.click(screen.getByRole("button", { name: /Calculate Route/ }))
    expect(fake.last("POST /simulation/start")?.query.get("traffic_model")).toBe("simple")
  })
})

describe("emergency services", () => {
  it("raises a fire emergency with a fire station and a fire engine", async () => {
    const { user, fake } = await calculateRoute()

    await user.selectOptions(screen.getByRole("combobox", { name: "Emergency service" }), "fire")
    await user.click(screen.getByRole("button", { name: /Trigger Fire Emergency/ }))

    expect(fake.last("POST /station/create")?.query.get("station_type")).toBe("fire")
    const vehicle = fake.last("POST /station/add-vehicle")?.query
    expect([vehicle?.get("vehicle_type"), vehicle?.get("speed"), vehicle?.get("station_id")]).toEqual(["fire", "17", "0"])
    expect(fake.last("POST /emergency/create")?.query.get("emergency_type")).toBe("fire")
    expect(await screen.findByText(/Fire engine 1 dispatched/)).toBeTruthy()

    // A second emergency gets its own (second) station
    await user.click(screen.getByRole("button", { name: /Trigger Fire Emergency/ }))
    expect(fake.last("POST /station/add-vehicle")?.query.get("station_id")).toBe("1")
  })

  it("says when an emergency is queued because every vehicle is busy", async () => {
    const backend = workingBackend()
    backend["POST /emergency/create"] = {
      status: 202,
      body: { status: "emergency queued", emergency_type: "ambulance", queue_position: 1 },
    }
    const { user } = await calculateRoute(backend)

    await user.click(screen.getByRole("button", { name: /Trigger Ambulance Emergency/ }))

    expect(await screen.findByText(/Every ambulance is busy: the emergency is queued \(number 1\)/)).toBeTruthy()
  })
})

describe("the optimiser's options", () => {
  it("asks for fire stations where people live when chosen", async () => {
    const fake = mockBackend({ ...workingBackend(), "GET /optimiser/stations": { body: rankingAnswer(5) } })
    const user = userEvent.setup()
    render(<App />)

    await user.selectOptions(screen.getByRole("combobox", { name: "Optimiser service" }), "fire")
    await user.selectOptions(screen.getByRole("combobox", { name: "Optimiser emergency locations" }), "population")
    await user.click(screen.getByRole("button", { name: "Find best fire station locations" }))

    const query = fake.last("GET /optimiser/stations")?.query
    expect([query?.get("service"), query?.get("distribution"), query?.get("scenarios")]).toEqual(["fire", "population", "500"])
  })
})

function evaluation(complete: boolean) {
  const demand = (vehicles: number, mean: number) => ({
    vehicles, runs: 3,
    mean_response_seconds: { mean, ci95: 12 },
    p90_response_seconds: mean + 60, worst_response_seconds: mean + 120,
    within_target_percent: { mean: 95, ci95: 3 }, unreached: 0,
    network_delay_vehicle_hours: { mean: 4, ci95: 0.5 },
    added_delay_vehicle_hours: { mean: 0.12, ci95: 0.05 },
  })
  return {
    evaluation_id: "ev-1",
    status: complete ? "completed" : "running",
    report: {
      scenarios_done: complete ? 36 : 12,
      scenarios_total: 36,
      complete,
      candidates: complete ? [
        { label: "Site #2", stations: [], estimate: { mean_response_seconds: 160 }, paired_estimate_seconds: 150,
          simulated_mean_response_seconds: 190, estimate_gap_percent: 26.7, estimated_rank: 2, simulated_rank: 1,
          by_demand: [demand(500, 180), demand(1500, 200)] },
        { label: "Site #1", stations: [], estimate: { mean_response_seconds: 155 }, paired_estimate_seconds: 158,
          simulated_mean_response_seconds: 210, estimate_gap_percent: 32.9, estimated_rank: 1, simulated_rank: 2,
          by_demand: [demand(500, 195), demand(1500, 225)] },
      ] : [],
      rank_agreement_kendall_tau: complete ? -1 : null,
      explanation: complete ? ["Site #2 answers fastest in traffic."] : [],
    },
  }
}

describe("the optimiser tested in traffic", () => {
  it("asks nothing until asked, then shows the ranking in traffic", async () => {
    let checks = 0
    const fake = mockBackend({
      ...workingBackend(),
      "POST /optimiser/evaluations": { status: 202, body: { evaluation_id: "ev-1" } },
      "GET /optimiser/evaluations/ev-1": () => ({ body: evaluation(checks++ > 0) }),
    })
    const user = userEvent.setup()
    render(<App />)
    expect(fake.paths()).toEqual([])

    const section = screen.getByRole("region", { name: "Test the optimiser in traffic" })
    await user.selectOptions(within(section).getByRole("combobox", { name: "Service to test" }), "police")
    await user.click(within(section).getByRole("button", { name: /Test in traffic/ }))

    expect(fake.last("POST /optimiser/evaluations")?.body).toEqual({
      kind: "ranking", stations: 3, top: 5, service: "police", distribution: "uniform",
    })
    expect(await within(section).findByText("12 of 36 simulations done")).toBeTruthy()
  })

  it("shows each candidate's simulated time next to the optimiser's estimate", async () => {
    mockBackend({
      ...workingBackend(),
      "POST /optimiser/evaluations": { status: 202, body: { evaluation_id: "ev-1" } },
      "GET /optimiser/evaluations/ev-1": { body: evaluation(true) },
    })
    const user = userEvent.setup()
    render(<App />)
    const section = screen.getByRole("region", { name: "Test the optimiser in traffic" })
    await user.click(within(section).getByRole("button", { name: /Test in traffic/ }))

    const table = await within(section).findByRole("table", { name: "Candidates ranked in traffic" })
    const rows = within(table).getAllByRole("row").slice(1)
    expect(within(rows[0]).getByText("Site #2")).toBeTruthy()
    expect(within(rows[0]).getByText("+27%")).toBeTruthy()
    expect(within(rows[0]).getByText("3.0 ± 0.2 min")).toBeTruthy()
    expect(within(rows[0]).getByText("0.12 veh-h")).toBeTruthy()
    expect(within(section).getByText(/Kendall's tau -1.00/)).toBeTruthy()
    expect(within(section).getByText("Site #2 answers fastest in traffic.")).toBeTruthy()
  })
})
