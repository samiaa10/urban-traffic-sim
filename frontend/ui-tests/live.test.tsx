// The live controls and live charts: pause and speed, fleets, a rush hour,
// accidents and closures picked on the map, rerouting, and the charts
// drawn from each step's summary. The backend is faked.

import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import App from "../src/App.tsx"
import {
  DESTINATION,
  START,
  car,
  clickMap,
  controlPolling,
  mockBackend,
  rankingAnswer,
  withRunIds,
  workingBackend,
  type Backend,
} from "./helpers.ts"

vi.mock("react-leaflet", async () => await import("./fakeLeaflet.tsx"))

const ROAD = {
  start: 11, destination: 12, name: "Prince of Wales Road",
  start_point: { latitude: 52.63, longitude: 1.29 }, end_point: { latitude: 52.631, longitude: 1.291 },
  distance_metres: 3,
}

function liveBackend(): Backend {
  let time = 0
  return {
    ...workingBackend(),
    "POST /simulation/step": ({ query }) => {
      time += Number(query.get("seconds") ?? 1)
      return {
        body: {
          vehicles: [car()], simulation_time: time,
          summary: { active_vehicles: 40, held_up: 10, mean_speed_factor: 0.75, arrived: 5,
                     mean_journey_seconds: 320, reroutes: 3, closed_roads: 0, accidents: 1 },
        },
      }
    },
    "POST /simulation/fleet": { body: { status: "fleet added", spawned: 500 } },
    "POST /traffic/surge": { body: { status: "surge started" } },
    "POST /traffic/rerouting": { body: { status: "rerouting disabled" } },
    "GET /scenarios/nearest-road": { body: ROAD },
    "POST /incident/accident": { body: { status: "accident created" } },
    "POST /incident/accident/clear": { body: { status: "accident cleared" } },
    "POST /incident/unblock": { body: { status: "road reopened" } },
  }
}

async function startRun() {
  const backend = mockBackend(liveBackend())
  const polling = controlPolling()
  const user = userEvent.setup()
  render(<App />)
  clickMap(START)
  clickMap(DESTINATION)
  await user.click(screen.getByRole("button", { name: /Calculate Route/ }))
  await screen.findByText(/Route: /)
  const panel = screen.getByRole("region", { name: "Live controls" })
  return { backend, polling, user, panel }
}

describe("live controls", () => {
  it("are off until a route is running", () => {
    mockBackend(liveBackend())
    render(<App />)
    const panel = screen.getByRole("region", { name: "Live controls" })
    expect(within(panel).getByRole("button", { name: /Add vehicles/ })).toHaveProperty("disabled", true)
  })

  it("pause stops stepping and resume starts again", async () => {
    const { polling, user, panel } = await startRun()
    expect(polling.running()).toBe(1)

    await user.click(within(panel).getByRole("button", { name: /Pause/ }))
    expect(polling.running()).toBe(0)

    await user.click(within(panel).getByRole("button", { name: /Resume/ }))
    expect(polling.running()).toBe(1)
  })

  it("runs faster by asking for several seconds a tick", async () => {
    const { backend, polling, user, panel } = await startRun()
    await user.selectOptions(within(panel).getByRole("combobox", { name: "Simulation speed" }), "5")
    await polling.tick()
    expect(backend.last("POST /simulation/step")?.query.get("seconds")).toBe("5")
  })

  it("adds a fleet and starts a rush hour", async () => {
    const { backend, user, panel } = await startRun()
    await user.selectOptions(within(panel).getByRole("combobox", { name: "Fleet size" }), "500")
    await user.click(within(panel).getByRole("button", { name: /Add vehicles/ }))
    expect(backend.last("POST /simulation/fleet")?.query.get("count")).toBe("500")
    expect(await screen.findByText(/Added 500 vehicles/)).toBeTruthy()

    await user.click(within(panel).getByRole("button", { name: /rush hour/ }))
    expect(backend.last("POST /traffic/surge")?.query.get("direction")).toBe("inbound")
  })

  it("puts an accident on the road picked on the map, then clears it", async () => {
    const { backend, user, panel } = await startRun()
    await user.selectOptions(within(panel).getByRole("combobox", { name: "Accident severity" }), "moderate")
    await user.click(within(panel).getByRole("button", { name: /Add an accident/ }))
    clickMap(START)

    expect(await screen.findByText(/Moderate accident on Prince of Wales Road for 15 min/)).toBeTruthy()
    const query = backend.last("POST /incident/accident")?.query
    expect([query?.get("start"), query?.get("severity"), query?.get("duration_seconds")]).toEqual(["11", "moderate", "900"])
    // The click picked a road, not a new route start
    expect(backend.count("POST /simulation/start")).toBe(1)

    const list = within(panel).getByRole("list", { name: "Your incidents" })
    await user.click(within(list).getByRole("button", { name: "Clear" }))
    expect(backend.count("POST /incident/accident/clear")).toBe(1)
  })

  it("turns dynamic rerouting off", async () => {
    const { backend, user, panel } = await startRun()
    await user.click(within(panel).getByRole("checkbox", { name: "Dynamic rerouting" }))
    expect(backend.last("POST /traffic/rerouting")?.query.get("enabled")).toBe("false")
  })
})

describe("live charts", () => {
  it("draw each step's summary", async () => {
    const { polling } = await startRun()
    await polling.tick()
    await polling.tick()

    const section = screen.getByRole("region", { name: "Live traffic" })
    expect(within(section).getByText("40 driving, 10 held up, average speed 75% of free flow, 5 arrived")).toBeTruthy()
    expect(within(section).getByRole("img", { name: /Vehicles driving over/ })).toBeTruthy()
    expect(within(section).getByText("320 s")).toBeTruthy()
  })
})

describe("each visitor's own run", () => {
  it("starts a run that replaces nobody else's, and the optimiser plans around its roads", async () => {
    const backend = mockBackend({
      ...withRunIds(liveBackend(), "mine"),
      "GET /optimiser/stations": { body: rankingAnswer(5) },
    })
    controlPolling()
    const user = userEvent.setup()
    render(<App />)
    clickMap(START)
    clickMap(DESTINATION)
    await user.click(screen.getByRole("button", { name: /Calculate Route/ }))
    await screen.findByText(/Route: /)

    expect(backend.last("POST /simulation/start")?.query.get("replace_default")).toBe("false")

    await user.click(screen.getByRole("button", { name: "Find best ambulance station locations" }))
    expect(backend.last("GET /optimiser/stations")?.query.get("simulation_id")).toBe("mine")
  })
})
