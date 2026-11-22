// The command centre: starting a shift, reporting incidents by clicking
// the map, what the panels show from the backend's state, resolving,
// replay, and the 2D map when the 3D one cannot start. MapLibre needs
// WebGL, so the 3D map is a stand-in that reports what it was given.

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import App from "../src/App.tsx"
import type { CommandIncident, CommandState, Replay, Unit } from "../src/command.ts"
import type { FeatureCollection, SourceId } from "../src/mapData.ts"
import type { Coordinate } from "../src/simulationHelpers.ts"
import { mockBackend, workingBackend, type FakeRequest } from "./helpers.ts"

vi.mock("react-leaflet", async () => await import("./fakeLeaflet.tsx"))

type MapProps = {
  data: Record<SourceId, FeatureCollection>
  onClick: (location: Coordinate) => void
  onFailed: (reason: string) => void
  focus?: { key: string } | null
}
const latest: { props: MapProps | null } = { props: null }

vi.mock("../src/Map3D.tsx", () => ({
  default: (props: MapProps) => {
    latest.props = props
    return (
      <div data-testid="map-3d"
        data-incidents={props.data.points.features.map((f) => f.properties.label).join(",")}
        data-units={props.data.vehicles.features.map((f) => f.properties.id).join(",")}
        data-routes={props.data.routes.features.length}
        data-focus={props.focus?.key ?? ""} />
    )
  },
}))

const RUN = "cc-1"

const unit = (id: number, overrides: Partial<Unit> = {}): Unit => ({
  vehicle_id: id, service: "ambulance", station: "Ambulance standby point 1", station_id: 0, state: "available",
  incident_id: null, latitude: 52.62, longitude: 1.27, eta_seconds: null, reroutes: 0, route: [], ...overrides,
})

const metrics = (overrides: Partial<CommandState["metrics"]> = {}): CommandState["metrics"] => ({
  responses: 0, mean_response_seconds: null, worst_response_seconds: null, within_target_percent: null,
  units_total: 2, units_available: 2, units_en_route: 0, units_on_scene: 0, units_returning: 0,
  incidents_open: 0, incidents_resolved: 0, queued_units: 0,
  by_service: {
    ambulance: { responses: 0, mean_seconds: null, target_seconds: 420, available: 1, units: 1 },
    fire: { responses: 0, mean_seconds: null, target_seconds: 480, available: 1, units: 1 },
    police: { responses: 0, mean_seconds: null, target_seconds: 900, available: 0, units: 0 },
  },
  ...overrides,
})

const medical = (overrides: Partial<CommandIncident> = {}): CommandIncident => ({
  id: 1, kind: "medical", label: "Medical emergency", latitude: 52.63, longitude: 1.29, status: "en_route",
  created_at: 5, resolved_at: null, services: ["ambulance"], on_scene_seconds: 900, units: [2], road: null,
  flood_roads: 0, target: { latitude: 52.63, longitude: 1.29 },
  timeline: [
    { time: 5, event: "reported", label: "Medical emergency reported" },
    { time: 5, event: "dispatched", label: "Ambulance 2 dispatched", service: "ambulance", vehicle_id: 2 },
  ],
  ...overrides,
})

const STATIONS: CommandState["stations"] = [
  { station_id: 0, name: "Ambulance standby point 1", source: "optimiser", service: "ambulance", latitude: 52.62, longitude: 1.27 },
  { station_id: 1, name: "Sprowston Fire Station", source: "OpenStreetMap", service: "fire", latitude: 52.65, longitude: 1.32 },
]

// A backend whose command centre answers `current` (tests change it)
function commandBackend() {
  const current: { state: CommandState } = {
    state: { simulation_time: 0, incidents: [], units: [unit(2), unit(3, { service: "fire", station: "Sprowston Fire Station", station_id: 1 })], stations: STATIONS, metrics: metrics() },
  }
  const reported: FakeRequest[] = []
  const requests = mockBackend({
    ...workingBackend(),
    "POST /command/start": () => ({ status: 201, body: { simulation_id: RUN, traffic_model: "osm", ...current.state } }),
    [`POST /simulations/${RUN}/step`]: { body: { vehicles: [{ vehicle_id: 2, latitude: 52.625, longitude: 1.28, distance_travelled: 100, finished: false, vehicle_type: "ambulance", on_emergency: true }], simulation_time: 10, summary: {} } },
    [`GET /simulations/${RUN}/command`]: () => ({ body: current.state }),
    [`GET /simulations/${RUN}/congestion`]: { body: { congestion: [] } },
    [`POST /simulations/${RUN}/command/incidents`]: (request) => {
      reported.push(request)
      const incident = medical()
      current.state = {
        ...current.state, simulation_time: 5, incidents: [incident],
        units: [unit(2, { state: "en_route", incident_id: 1, eta_seconds: 95,
          route: [{ latitude: 52.62, longitude: 1.27 }, { latitude: 52.63, longitude: 1.29 }] }), current.state.units[1]],
        metrics: metrics({ units_available: 1, units_en_route: 1, incidents_open: 1 }),
      }
      return { status: 201, body: { incident, dispatched: ["dispatched"], metrics: current.state.metrics } }
    },
    [`POST /simulations/${RUN}/command/incidents/1/resolve`]: () => {
      current.state = { ...current.state, incidents: [medical({ status: "resolved", resolved_at: 300 })] }
      return { body: { incident: current.state.incidents[0], metrics: current.state.metrics } }
    },
    [`GET /simulations/${RUN}/command/replay`]: {
      body: {
        simulation_time: 300, incidents: [medical({ status: "resolved", resolved_at: 300 })],
        tracks: [{ vehicle_id: 2, service: "ambulance", samples: [[5, 52.62, 1.27], [100, 52.63, 1.29], [300, 52.63, 1.29]] }],
      } satisfies Replay,
    },
  })
  return { current, reported, requests }
}

async function openCommand() {
  await userEvent.click(screen.getByRole("button", { name: "Command" }))
  await userEvent.click(screen.getByRole("button", { name: "Open the command centre" }))
  return await screen.findByTestId("map-3d")
}

beforeEach(() => {
  localStorage.clear()
  latest.props = null
})
afterEach(() => localStorage.clear())

describe("command centre", () => {
  it("starts a shift with the fleet the backend mobilised", async () => {
    const { requests } = commandBackend()
    render(<App />)
    await openCommand()

    const start = requests.last("POST /command/start")!
    expect(start.query.get("vehicles_per_station")).toBe("2")
    expect(start.query.get("background_vehicles")).toBe("800")
    expect(screen.getByText(/Command centre open: 2 units at 2 stations/)).toBeTruthy()
    const units = screen.getByRole("region", { name: "Units" })
    expect(within(units).getByText("Ambulances: 1 of 1 free")).toBeTruthy()
    expect(within(units).getByText("Sprowston Fire Station")).toBeTruthy()
    // The camera frames the whole fleet
    expect(latest.props!.focus!.key).toBe("stations")
    // The run is stepped
    await waitFor(() => expect(requests.count(`POST /simulations/${RUN}/step`)).toBeGreaterThan(0), { timeout: 3000 })
  })

  it("reports an incident where the map is clicked and follows its units", async () => {
    const { reported } = commandBackend()
    render(<App />)
    const map = await openCommand()

    // Nothing happens on a click until a type is chosen
    act(() => latest.props!.onClick([52.63, 1.29]))
    expect(reported).toHaveLength(0)

    const panel = screen.getByRole("region", { name: "Report an incident" })
    await userEvent.click(within(panel).getByRole("button", { name: "Medical" }))
    expect(screen.getByText("Click the map where the medical is.")).toBeTruthy()
    await act(async () => latest.props!.onClick([52.63, 1.29]))

    expect(reported[0].query.get("kind")).toBe("medical")
    expect(reported[0].query.get("latitude")).toBe("52.63")
    expect(await screen.findByText("#1 Medical emergency: 1 unit dispatched.")).toBeTruthy()
    await waitFor(() => expect(map.getAttribute("data-incidents")).toBe("#1 Medical"))
    expect(map.getAttribute("data-routes")).toBe("1")
    expect(map.getAttribute("data-focus")).toBe("incident-1")

    const figures = screen.getByRole("group", { name: "Response performance" })
    expect(within(figures).getByText("1/2")).toBeTruthy()
    const timeline = screen.getByRole("region", { name: "Incident timeline" })
    expect(within(timeline).getByText("Ambulance 2 dispatched")).toBeTruthy()
    expect(within(screen.getByRole("region", { name: "Units" })).getByText("ETA 1:35")).toBeTruthy()
    // Still on its way: it cannot be resolved yet
    expect((within(timeline).getByRole("button", { name: "Resolve and stand units down" }) as HTMLButtonElement).disabled).toBe(true)
  })

  it("resolves an incident once its units are on scene", async () => {
    const { current } = commandBackend()
    render(<App />)
    await openCommand()
    await userEvent.click(within(screen.getByRole("region", { name: "Report an incident" })).getByRole("button", { name: "Medical" }))
    await act(async () => latest.props!.onClick([52.63, 1.29]))
    current.state = { ...current.state, incidents: [medical({ status: "on_scene" })] }

    const timeline = screen.getByRole("region", { name: "Incident timeline" })
    const resolve = await within(timeline).findByRole("button", { name: "Resolve and stand units down" })
    await waitFor(() => expect((resolve as HTMLButtonElement).disabled).toBe(false), { timeout: 3000 })
    await userEvent.click(resolve)
    expect(await screen.findByText(/#1 resolved: units at the scene are returning/)).toBeTruthy()
    const log = screen.getByRole("region", { name: "Incident log" })
    expect(within(log).getByText("Resolved")).toBeTruthy()
  })

  it("replays the recorded tracks and goes back to live", async () => {
    commandBackend()
    render(<App />)
    const map = await openCommand()
    await userEvent.click(within(screen.getByRole("region", { name: "Report an incident" })).getByRole("button", { name: "Medical" }))
    await act(async () => latest.props!.onClick([52.63, 1.29]))

    await userEvent.click(within(screen.getByRole("region", { name: "Simulation playback" })).getByRole("button", { name: "Replay" }))
    const replay = await screen.findByRole("region", { name: "Replay" })
    await userEvent.click(within(replay).getByRole("button", { name: "Pause replay" }))
    const slider = within(replay).getByRole("slider", { name: "Replay time" }) as HTMLInputElement
    expect([slider.min, slider.max]).toEqual(["5", "300"])

    // Part way along its recorded trip the ambulance is drawn, the incident open
    expect(within(replay).getByText("T+0:05")).toBeTruthy()
    fireEvent.change(slider, { target: { value: "50" } })
    await waitFor(() => expect(map.getAttribute("data-units")).toBe("2"))
    expect(map.getAttribute("data-incidents")).toBe("#1 Medical")
    expect(map.getAttribute("data-routes")).toBe("0")
    expect(within(replay).getByText("T+0:50")).toBeTruthy()
    // Reporting is off while replaying
    expect((within(screen.getByRole("region", { name: "Report an incident" })).getByRole("button", { name: "Fire" }) as HTMLButtonElement).disabled).toBe(true)

    await userEvent.click(within(replay).getByRole("button", { name: "Back to live" }))
    expect(screen.getByRole("region", { name: "Simulation playback" })).toBeTruthy()
  })

  it("shows the 2D map when the 3D map cannot start", async () => {
    commandBackend()
    render(<App />)
    await openCommand()

    act(() => latest.props!.onFailed("WebGL is not available"))

    expect(screen.queryByTestId("map-3d")).toBeNull()
    expect(screen.getByTestId("map")).toBeTruthy()
    expect(screen.getByText(/3D map could not start \(WebGL is not available\), so the 2D map is shown/)).toBeTruthy()
    // The command centre carries on
    expect(screen.getByRole("region", { name: "Incident log" })).toBeTruthy()
  })

  it("says why the command centre could not start", async () => {
    mockBackend({ ...workingBackend(), "POST /command/start": { status: 503, body: { detail: "The simulation could not be saved (database unavailable)" } } })
    render(<App />)
    await userEvent.click(screen.getByRole("button", { name: "Command" }))
    await userEvent.click(screen.getByRole("button", { name: "Open the command centre" }))
    expect((await screen.findByRole("alert")).textContent).toMatch(/Could not start the command centre: The simulation could not be saved/)
  })
})
