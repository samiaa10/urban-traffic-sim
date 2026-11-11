// The ambulance emergency: dispatching an ambulance, watching its
// progress, closing a road ahead of it, and what happens when it fails.

import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import App from "../src/App.tsx"
import { COLOURS } from "../src/mapStyles.ts"
import {
  DESTINATION,
  EMERGENCY_SPOT,
  START,
  clickMap,
  controlPolling,
  enRoute,
  lines,
  markers,
  mockBackend,
  workingBackend,
} from "./helpers.ts"

vi.mock("react-leaflet", async () => await import("./fakeLeaflet.tsx"))

const emergencyButton = () =>
  screen.getByRole("button", { name: /Trigger Ambulance Emergency/ })

async function calculateRoute() {
  const user = userEvent.setup()
  render(<App />)
  clickMap(START)
  clickMap(DESTINATION)
  await user.click(screen.getByRole("button", { name: /Calculate Route/ }))
  await screen.findByText(/Route: /)
  return user
}

describe("dispatching an ambulance", () => {
  it("cannot be triggered until a route has been calculated", async () => {
    mockBackend(workingBackend())
    controlPolling()
    render(<App />)
    clickMap(START)
    clickMap(DESTINATION)

    const button = emergencyButton() as HTMLButtonElement
    expect(button.disabled).toBe(true)
    expect(button.title).toBe("Calculate a route first")

    await userEvent.setup().click(
      screen.getByRole("button", { name: /Calculate Route/ })
    )
    await screen.findByText(/Route: /)

    expect(button.disabled).toBe(false)
  })

  it("creates a station, adds an ambulance and raises the emergency, in that order", async () => {
    const backend = mockBackend(workingBackend())
    controlPolling()
    const user = await calculateRoute()

    await user.click(emergencyButton())
    await screen.findByText(/Ambulance 1 dispatched/)

    expect(backend.paths().slice(-3)).toEqual([
      "POST /station/create",
      "POST /station/add-vehicle",
      "POST /emergency/create",
    ])
    // The emergency happens at the destination the user chose
    const emergency = backend.last("POST /emergency/create")?.query
    expect(emergency?.get("latitude")).toBe(String(DESTINATION.lat))
    expect(emergency?.get("longitude")).toBe(String(DESTINATION.lng))
  })

  it("shows the ambulance route, the emergency location and the response cost", async () => {
    mockBackend(workingBackend())
    const polling = controlPolling()
    const user = await calculateRoute()

    await user.click(emergencyButton())

    expect(
      await screen.findByText(
        /Ambulance 1 dispatched \| 2346 m response route cost \| 99 nodes explored/
      )
    ).toBeTruthy()
    expect(lines(COLOURS.emergency).map((line) => line.getAttribute("data-points"))).toEqual(["3"])
    expect(
      markers(EMERGENCY_SPOT).map((marker) => marker.getAttribute("data-center"))
    ).toEqual(["52.65,1.31"])
    // The simulation keeps running so the ambulance can be watched
    expect(polling.running()).toBe(1)
  })

  it("shows the ambulance's progress as the simulation runs", async () => {
    mockBackend({
      ...workingBackend(),
      "GET /emergency/status": {
        body: { simulation_time: 5, emergencies: [enRoute] },
      },
    })
    const polling = controlPolling()
    const user = await calculateRoute()
    await user.click(emergencyButton())
    await screen.findByText(/Ambulance 1 dispatched/)

    await polling.tick()

    expect(
      screen.getByText(/En route \| ETA 40 s \| 800 m to go \| not rerouted \| 5 s elapsed/)
    ).toBeTruthy()
  })

  it("shows the response time once the ambulance has arrived", async () => {
    mockBackend({
      ...workingBackend(),
      "GET /emergency/status": {
        body: {
          simulation_time: 61,
          emergencies: [
            {
              ...enRoute,
              state: "arrived",
              response_time_seconds: 61,
              remaining_metres: 0,
              eta_seconds: 0,
              elapsed_seconds: 61,
            },
          ],
        },
      },
    })
    const polling = controlPolling()
    const user = await calculateRoute()
    await user.click(emergencyButton())
    await screen.findByText(/Ambulance 1 dispatched/)

    await polling.tick()

    expect(screen.getByText(/Arrived\. Response time 61 s/)).toBeTruthy()
    // Nothing left to close a road ahead of
    expect(
      screen.queryByRole("button", { name: /Close Road Ahead of Ambulance/ })
    ).toBeNull()
  })
})

describe("when the ambulance cannot be dispatched", () => {
  it("shows the backend's reason if no ambulance is available", async () => {
    mockBackend({
      ...workingBackend(),
      "POST /emergency/create": {
        status: 400,
        body: { detail: "No available emergency vehicle" },
      },
    })
    controlPolling()
    const user = await calculateRoute()

    await user.click(emergencyButton())

    expect(
      await screen.findByText("Emergency failed: No available emergency vehicle")
    ).toBeTruthy()
    expect(lines(COLOURS.emergency)).toHaveLength(0)
    expect(markers(EMERGENCY_SPOT)).toHaveLength(0)
  })

  it("stops after the first step if the station cannot be created", async () => {
    const backend = mockBackend({
      ...workingBackend(),
      "POST /station/create": { status: 500, body: {} },
    })
    controlPolling()
    const user = await calculateRoute()

    await user.click(emergencyButton())

    expect(
      await screen.findByText("Emergency failed: Could not create ambulance station")
    ).toBeTruthy()
    expect(backend.count("POST /station/add-vehicle")).toBe(0)
    expect(backend.count("POST /emergency/create")).toBe(0)
  })

  it("shows an error if the ambulance cannot be added to the station", async () => {
    const backend = mockBackend({
      ...workingBackend(),
      "POST /station/add-vehicle": { status: 500, body: {} },
    })
    controlPolling()
    const user = await calculateRoute()

    await user.click(emergencyButton())

    expect(
      await screen.findByText("Emergency failed: Could not add ambulance")
    ).toBeTruthy()
    expect(backend.count("POST /emergency/create")).toBe(0)
  })

  it("shows an error if the backend cannot be reached", async () => {
    mockBackend({
      ...workingBackend(),
      "POST /station/create": new TypeError("Failed to fetch"),
    })
    controlPolling()
    const user = await calculateRoute()

    await user.click(emergencyButton())

    expect(
      await screen.findByText("Emergency failed: Failed to fetch")
    ).toBeTruthy()
  })
})

describe("closing a road ahead of the ambulance", () => {
  async function ambulanceOnItsWay(overrides = {}) {
    mockBackend({
      ...workingBackend(),
      "GET /emergency/status": {
        body: { simulation_time: 5, emergencies: [enRoute] },
      },
      ...overrides,
    })
    const polling = controlPolling()
    const user = await calculateRoute()
    await user.click(emergencyButton())
    await screen.findByText(/Ambulance 1 dispatched/)
    await polling.tick()
    return user
  }

  const closeButton = () =>
    screen.getByRole("button", { name: /Close Road Ahead of Ambulance/ })

  it("only offers the button while the ambulance is on its way", async () => {
    mockBackend(workingBackend())
    controlPolling()
    const user = await calculateRoute()
    await user.click(emergencyButton())
    await screen.findByText(/Ambulance 1 dispatched/)

    // No status update has arrived yet
    expect(
      screen.queryByRole("button", { name: /Close Road Ahead of Ambulance/ })
    ).toBeNull()
  })

  it("closes the road, shows it, and shows the ambulance's new route", async () => {
    const user = await ambulanceOnItsWay()

    await user.click(closeButton())

    expect(
      await screen.findByText(
        /Road ahead closed .* Ambulance rerouted: \+322 m \(about \+16 s on the response time\)/
      )
    ).toBeTruthy()
    expect(lines(COLOURS.closure).map((line) => line.getAttribute("data-points"))).toEqual(["2"])
    // The red route was replaced by the longer one (5 points instead of 3)
    expect(lines(COLOURS.emergency).map((line) => line.getAttribute("data-points"))).toEqual(["5"])
  })

  it("shows the backend's reason if the road cannot be closed", async () => {
    const user = await ambulanceOnItsWay({
      "POST /emergency/block-ahead": {
        status: 400,
        body: { detail: "No ambulance is on its way" },
      },
    })

    await user.click(closeButton())

    expect(
      await screen.findByText("Could not close road: No ambulance is on its way")
    ).toBeTruthy()
    expect(lines(COLOURS.closure)).toHaveLength(0)
  })
})
