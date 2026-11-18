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

const FLOOD = {
  id: 1, severity: "closed", stage: "flooded", closed: true, recovered_percent: 0, speed_factor: null,
  ends_at: 1200, recovered_at: 1800,
  roads: [
    { start: { latitude: 52.63, longitude: 1.30 }, end: { latitude: 52.631, longitude: 1.30 } },
    { start: { latitude: 52.631, longitude: 1.30 }, end: { latitude: 52.632, longitude: 1.301 } },
  ],
}

const ACCESSIBILITY = {
  destinations_sampled: 60, closed_roads: 55, affected_roads: 55,
  accessibility: {
    before: { reachable_percent: 100, unreachable_pairs: 0, mean_seconds: 321, worst_seconds: 871,
              mean_increase_seconds: 0, worst_increase_seconds: 0, areas: [] },
    now: { reachable_percent: 93.7, unreachable_pairs: 15667, mean_seconds: 325, worst_seconds: 871,
           mean_increase_seconds: 2.6, worst_increase_seconds: 189,
           areas: [{ bounds: [52.6276, 1.3024, 52.6321, 1.3098], junctions: 40, mean_increase_seconds: 36, cut_off_pairs: 2245 }] },
  },
  emergency_access: {
    before: { ambulance: { reachable_percent: 100, mean_seconds: 149, worst_seconds: 276, within_target_percent: 100 } },
    now: { ambulance: { reachable_percent: 95, mean_seconds: 154, worst_seconds: 276, within_target_percent: 95 } },
  },
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
    "POST /incident/flood": ({ query }) => ({
      body: {
        status: "flood created",
        flood: { ...FLOOD, severity: query.get("severity") },
      },
    }),
    "GET /incident/floods": { body: { floods: [{ ...FLOOD, stage: "recovering", closed: false, recovered_percent: 40 }] } },
    "POST /incident/flood/clear": { body: { status: "flood cleared" } },
    "POST /incident/bridges": { body: { status: "bridges out of use", bridges: 21 } },
    "GET /resilience/accessibility": { body: ACCESSIBILITY },
    "POST /traffic/signals": { body: { status: "signals adaptive", controller: "adaptive", junctions: 121 } },
    "GET /traffic/signals": {
      body: {
        controller: "adaptive", emergency_priority: true,
        junctions: [{
          node: 9, latitude: 52.63, longitude: 1.29, phase: 0, stage: "green",
          approaches: [{ bearing: 0, light: "green", queue: 0 }, { bearing: 180, light: "red", queue: 5 }],
        }],
      },
    },
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

  it("sends one update at a time when the backend is slower than a tick", async () => {
    const { backend, polling } = await startRun()
    const before = backend.count("POST /simulation/step")

    // Two ticks while the first update is still being answered
    await Promise.all([polling.tick(), polling.tick()])
    expect(backend.count("POST /simulation/step")).toBe(before + 1)

    // Once it is answered, the next tick sends the next one
    await polling.tick()
    expect(backend.count("POST /simulation/step")).toBe(before + 2)
  })

  it("shows the simulated time each update reports", async () => {
    const { polling, user, panel } = await startRun()
    await user.selectOptions(within(panel).getByRole("combobox", { name: "Simulation speed" }), "10")
    await polling.tick()
    await polling.tick()
    expect(await screen.findByText(/Simulation time: 20 s/)).toBeTruthy()
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

describe("floods and resilience", () => {
  it("place a flood where the map is clicked, draw its roads, show its recovery, and clear it", async () => {
    const { backend, polling, user, panel } = await startRun()
    await user.selectOptions(within(panel).getByRole("combobox", { name: "Flood radius" }), "400")
    await user.click(within(panel).getByRole("button", { name: "Place a flood on the map" }))
    clickMap(START)

    const query = await vi.waitFor(() => {
      const sent = backend.last("POST /incident/flood")?.query
      if (!sent) throw new Error("not sent yet")
      return sent
    })
    expect(query.get("radius_metres")).toBe("400")
    expect(query.get("severity")).toBe("closed")
    expect(query.get("duration_seconds")).toBe("1200")
    expect(await screen.findByText(/Flood: 2 roads closed for 20 min, then 10 min to recover/)).toBeTruthy()

    await polling.tick()
    const floods = within(panel).getByRole("list", { name: "Floods" })
    expect(within(floods).getByText(/2 roads recovering: 40%/)).toBeTruthy()
    const lines = screen.getAllByTestId("polyline").filter((l) => l.getAttribute("data-color") === "#1aa0c8")
    expect(lines).toHaveLength(2)
    expect(screen.getByText("Flooded road (fades as it recovers)")).toBeTruthy()

    await user.click(within(floods).getByRole("button", { name: "Clear" }))
    expect(backend.last("POST /incident/flood/clear")?.query.get("flood_id")).toBe("1")
  })

  it("take the bridges out of use", async () => {
    const { backend, user, panel } = await startRun()
    await user.click(within(panel).getByRole("checkbox", { name: "Bridges out of use" }))
    expect(backend.last("POST /incident/bridges")?.query.get("fail")).toBe("true")
    expect(await screen.findByText("21 bridges out of use: traffic reroutes around them")).toBeTruthy()
  })

  it("measure how reachable the city is now", async () => {
    const { user } = await startRun()
    const section = screen.getByRole("region", { name: "Network resilience" })
    await user.click(within(section).getByRole("button", { name: "Measure accessibility now" }))
    const table = await within(section).findByRole("table", { name: "Accessibility before and now" })
    expect(within(table).getByText("93.7%")).toBeTruthy()
    expect(within(table).getByText("Ambulance reach within target")).toBeTruthy()
    expect(within(section).getByText(/2,245 pairs cut off/)).toBeTruthy()
  })
})

describe("traffic signals", () => {
  it("switch on with the chosen controller and draw each approach's real light", async () => {
    const { backend, polling, user, panel } = await startRun()
    await user.selectOptions(within(panel).getByRole("combobox", { name: "Signal control" }), "adaptive")
    await user.click(within(panel).getByRole("checkbox", { name: "Emergency vehicle priority" }))
    await user.click(within(panel).getByRole("button", { name: "Apply signals" }))

    const query = backend.last("POST /traffic/signals")?.query
    expect(query?.get("controller")).toBe("adaptive")
    expect(query?.get("emergency_priority")).toBe("true")
    expect(await screen.findByText("Adaptive signals at 121 junctions, with emergency priority")).toBeTruthy()

    await polling.tick()
    const lights = (await screen.findAllByTestId("circle-marker")).filter((m) =>
      ["#1e8e4f", "#c0392b"].includes(m.getAttribute("data-fill") ?? ""),
    )
    expect(lights.map((m) => m.getAttribute("data-fill")).sort()).toEqual(["#1e8e4f", "#c0392b"])
    // The red approach has a queue behind it, so its light is drawn bigger
    const red = lights.find((m) => m.getAttribute("data-fill") === "#c0392b")
    expect(Number(red?.getAttribute("data-radius"))).toBeGreaterThan(4)
    expect(screen.getByText("Signal red")).toBeTruthy()
  })

  it("are off until switched on, and asks nothing about them", async () => {
    const { backend, polling } = await startRun()
    await polling.tick()
    expect(backend.last("GET /traffic/signals")).toBeUndefined()
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
    // Only cellular-automaton runs report cars stopped in queues
    expect(within(section).queryByRole("img", { name: /Stopped in queues/ })).toBeNull()
  })

  it("chart cars stopped in queues when the run reports them (the cellular automaton)", async () => {
    const backend = liveBackend()
    const step = backend["POST /simulation/step"] as (request: { query: URLSearchParams }) => {
      body: { summary: Record<string, number> }
    }
    mockBackend({
      ...backend,
      "POST /simulation/step": (request: { query: URLSearchParams }) => {
        const answer = step(request)
        return { body: { ...answer.body, summary: { ...answer.body.summary, stopped: 8 } } }
      },
    })
    const polling = controlPolling()
    const user = userEvent.setup()
    render(<App />)
    clickMap(START)
    clickMap(DESTINATION)
    await user.click(screen.getByRole("button", { name: /Calculate Route/ }))
    await screen.findByText(/Route: /)
    await polling.tick()
    await polling.tick()

    const section = screen.getByRole("region", { name: "Live traffic" })
    expect(within(section).getByRole("img", { name: /Stopped in queues over/ })).toBeTruthy()
    expect(within(section).getByText("20%")).toBeTruthy()
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
