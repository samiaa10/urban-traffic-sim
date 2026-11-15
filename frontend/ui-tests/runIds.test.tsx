// Runs with IDs: once /simulation/start returns a simulation_id, every
// request about the running simulation names it (/simulations/{id}/...),
// so it reaches this run even if another one is started elsewhere. With
// an older backend that returns no ID the original paths are used; every
// other test file covers that case, since their fake backend gives none.

import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import App from "../src/App.tsx"
import {
  DESTINATION,
  RUN_ENDPOINTS,
  START,
  clickMap,
  controlPolling,
  enRoute,
  mockBackend,
  withRunIds,
  workingBackend,
  type Backend,
} from "./helpers.ts"

vi.mock("react-leaflet", async () => await import("./fakeLeaflet.tsx"))

async function calculateRoute() {
  const user = userEvent.setup()
  render(<App />)
  clickMap(START)
  clickMap(DESTINATION)
  await user.click(screen.getByRole("button", { name: /Calculate Route/ }))
  await screen.findByText(/Route: /)
  return user
}

const originalPaths = new Set(RUN_ENDPOINTS.map(([original]) => original))

const usedOriginalPaths = (paths: string[]) =>
  paths.filter((path) => originalPaths.has(path))

describe("a backend that gives each run an ID", () => {
  it("steps and watches the run by its ID", async () => {
    const backend = mockBackend(withRunIds(workingBackend(), "abc-123"))
    const polling = controlPolling()
    await calculateRoute()

    await polling.tick()

    expect(backend.paths().slice(-3)).toEqual([
      "POST /simulations/abc-123/step",
      "GET /simulations/abc-123/congestion",
      "GET /simulations/abc-123/emergencies/status",
    ])
  })

  it("dispatches the ambulance in its own run", async () => {
    const backend = mockBackend(withRunIds(workingBackend()))
    controlPolling()
    const user = await calculateRoute()

    await user.click(
      screen.getByRole("button", { name: /Trigger Ambulance Emergency/ })
    )
    await screen.findByText(/Ambulance 1 dispatched/)

    expect(backend.paths().slice(-3)).toEqual([
      "POST /simulations/run-1/stations",
      "POST /simulations/run-1/stations/add-vehicle",
      "POST /simulations/run-1/emergencies",
    ])
    const emergency = backend.last("POST /simulations/run-1/emergencies")?.query
    expect(emergency?.get("latitude")).toBe(String(DESTINATION.lat))
  })

  it("closes a road and reroutes in its own run", async () => {
    const backend = mockBackend(withRunIds(workingBackend()))
    controlPolling()
    const user = await calculateRoute()

    await user.click(screen.getByRole("button", { name: /Cause Road Closure/ }))
    await screen.findByText(/Road closed: /)

    expect(backend.count("POST /simulations/run-1/incidents/block")).toBe(1)
    expect(backend.count("POST /simulations/run-1/reroute")).toBe(1)
    expect(
      backend.last("POST /simulations/run-1/incidents/block")?.query.get("start")
    ).toBe("33")
  })

  it("closes the road ahead of the ambulance in its own run", async () => {
    const backend = mockBackend(
      withRunIds({
        ...workingBackend(),
        "GET /emergency/status": {
          body: { simulation_time: 5, emergencies: [enRoute] },
        },
      })
    )
    const polling = controlPolling()
    const user = await calculateRoute()
    await user.click(
      screen.getByRole("button", { name: /Trigger Ambulance Emergency/ })
    )
    await screen.findByText(/Ambulance 1 dispatched/)
    await polling.tick()

    await user.click(
      screen.getByRole("button", { name: /Close Road Ahead of Ambulance/ })
    )
    await screen.findByText(/Road ahead closed/)

    expect(backend.count("POST /simulations/run-1/emergencies/block-ahead")).toBe(1)
    expect(usedOriginalPaths(backend.paths())).toEqual([])
  })

  it("follows the new run after a new route is calculated", async () => {
    let started = 0
    const base = withRunIds(workingBackend(), "first")
    const backend = mockBackend({
      ...base,
      ...withRunIds(workingBackend(), "second"),
      "POST /simulation/start": () => {
        started += 1
        const id = started === 1 ? "first" : "second"
        return {
          body: {
            status: "simulation started",
            distance_metres: 1234,
            nodes_explored: 77,
            vehicle_position: { latitude: 52.631, longitude: 1.291 },
            simulation_id: id,
          },
        }
      },
    } satisfies Backend)
    const polling = controlPolling()
    const user = await calculateRoute()
    await polling.tick()

    // A third click starts over; choose a destination and calculate again
    clickMap(START)
    clickMap(DESTINATION)
    await user.click(screen.getByRole("button", { name: /Calculate Route/ }))
    await polling.tick()

    expect(backend.count("POST /simulations/first/step")).toBe(1)
    expect(backend.last("POST /simulations/second/step")).toBeDefined()
    expect(backend.paths().at(-3)).toBe("POST /simulations/second/step")
  })
})
