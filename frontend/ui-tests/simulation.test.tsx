// What a person can do with the map and the simulation: choose points,
// calculate a route, watch vehicles move, trigger a road closure, and
// what they see when the backend fails.
//
// The app has no pause or reset buttons. A simulation starts when a route
// is calculated, stops by itself when every vehicle has arrived or the
// backend stops answering, and a new map click starts over; those are the
// behaviours tested here.

import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import App from "../src/App.tsx"
import { COLOURS } from "../src/mapStyles.ts"
import {
  DESTINATION,
  POINT,
  START,
  VEHICLE,
  car,
  clickMap,
  controlPolling,
  lines,
  markers,
  mockBackend,
  workingBackend,
} from "./helpers.ts"

vi.mock("react-leaflet", async () => await import("./fakeLeaflet.tsx"))

const calculateButton = () =>
  screen.getByRole("button", { name: /Calculate Route/ })

// Render the app, choose two points and press Calculate Route
async function calculateRoute() {
  const user = userEvent.setup()
  render(<App />)
  clickMap(START)
  clickMap(DESTINATION)
  await user.click(calculateButton())
  await screen.findByText(/Route: 1\D?234 m/)
  return user
}

describe("choosing a route on the map", () => {
  it("asks for a start point, then a destination, then offers Calculate Route", () => {
    mockBackend(workingBackend())
    render(<App />)

    expect(screen.getByText(/Waiting for input/)).toBeTruthy()
    expect(screen.getByText(/choose a start point/)).toBeTruthy()
    expect(screen.queryByRole("button", { name: /Calculate Route/ })).toBeNull()

    clickMap(START)
    expect(screen.getByText(/choose a destination/)).toBeTruthy()
    expect(screen.queryByRole("button", { name: /Calculate Route/ })).toBeNull()

    clickMap(DESTINATION)
    expect(calculateButton()).toBeTruthy()
    expect(screen.getByText(/Click Calculate Route to start/)).toBeTruthy()
  })

  it("puts a marker on the start and destination points", () => {
    mockBackend(workingBackend())
    render(<App />)

    clickMap(START)
    clickMap(DESTINATION)

    expect(
      markers(POINT).map((marker) => marker.getAttribute("data-center"))
    ).toEqual(["52.63,1.29", "52.65,1.31"])
  })
})

describe("calculating a route and running the simulation", () => {
  it("finds the nearest roads, calculates the route and starts the simulation", async () => {
    const backend = mockBackend(workingBackend())
    controlPolling()

    await calculateRoute()

    expect(backend.paths()).toEqual([
      "GET /nearest-node",
      "GET /nearest-node",
      "GET /route",
      "POST /simulation/start",
    ])
    expect(backend.last("GET /route")?.query.get("start")).toBe("11")
    expect(backend.last("GET /route")?.query.get("destination")).toBe("22")
    expect(backend.last("POST /simulation/start")?.query.get("start")).toBe("11")
  })

  it("shows the route on the map and its numbers in the status panel", async () => {
    mockBackend(workingBackend())
    controlPolling()

    await calculateRoute()

    expect(
      screen.getByText(/Route: 1\D?234 m \(A\*, 77 nodes explored\)/)
    ).toBeTruthy()
    expect(screen.getByText(/Running/)).toBeTruthy()
    expect(lines(COLOURS.route).map((line) => line.getAttribute("data-points"))).toEqual(["5"])
    expect(markers(VEHICLE)).toHaveLength(1)
  })

  it("moves the vehicle and draws congestion on each update", async () => {
    mockBackend({
      ...workingBackend(),
      "POST /simulation/step": {
        body: { vehicles: [car({ latitude: 52.64, longitude: 1.3 })] },
      },
      "GET /simulation/congestion": {
        body: {
          congestion: [
            {
              start: { latitude: 52.63, longitude: 1.29 },
              end: { latitude: 52.632, longitude: 1.292 },
              vehicle_count: 3,
              level: "heavy",
            },
          ],
        },
      },
    })
    const polling = controlPolling()
    await calculateRoute()

    expect(markers(VEHICLE)[0].getAttribute("data-center")).toBe("52.631,1.291")
    expect(polling.running()).toBe(1)

    await polling.tick()

    expect(markers(VEHICLE)[0].getAttribute("data-center")).toBe("52.64,1.3")
    expect(
      screen
        .queryAllByTestId("polyline")
        .filter((line) => line.getAttribute("data-points") === "2")
    ).toHaveLength(1)
  })

  it("stops updating and says Completed once every vehicle has arrived", async () => {
    const backend = mockBackend({
      ...workingBackend(),
      "POST /simulation/step": {
        body: { vehicles: [car({ finished: true })] },
      },
    })
    const polling = controlPolling()
    await calculateRoute()

    await polling.tick()

    expect(polling.running()).toBe(0)
    expect(screen.getByText(/Completed/)).toBeTruthy()
    expect(screen.getByText(/\(1 finished\)/)).toBeTruthy()

    await polling.tick()
    expect(backend.count("POST /simulation/step")).toBe(1)
  })

  it("keeps running when only the congestion data is unavailable", async () => {
    mockBackend({
      ...workingBackend(),
      "GET /simulation/congestion": new Error("Failed to fetch"),
    })
    const polling = controlPolling()
    await calculateRoute()

    await polling.tick()

    expect(polling.running()).toBe(1)
    expect(screen.getByText(/Running/)).toBeTruthy()
  })
})

describe("starting over", () => {
  it("clicking the map again stops the simulation and clears the route and vehicles", async () => {
    const backend = mockBackend(workingBackend())
    const polling = controlPolling()
    await calculateRoute()
    expect(polling.running()).toBe(1)

    clickMap({ lat: 52.62, lng: 1.28 })

    expect(polling.running()).toBe(0)
    expect(markers(VEHICLE)).toHaveLength(0)
    expect(lines(COLOURS.route)).toHaveLength(0)
    expect(screen.queryByText(/Route: /)).toBeNull()
    expect(screen.queryByRole("button", { name: /Calculate Route/ })).toBeNull()
    expect(screen.getByText(/choose a destination/)).toBeTruthy()

    await polling.tick()
    expect(backend.count("POST /simulation/step")).toBe(0)
  })

  it("can calculate a second route after starting over", async () => {
    const backend = mockBackend(workingBackend())
    const polling = controlPolling()
    const user = await calculateRoute()

    clickMap({ lat: 52.62, lng: 1.28 })
    clickMap({ lat: 52.66, lng: 1.32 })
    await user.click(calculateButton())
    await screen.findByText(/Route: 1\D?234 m/)

    expect(backend.count("POST /simulation/start")).toBe(2)
    expect(polling.running()).toBe(1)
  })
})

describe("when the backend fails", () => {
  it("shows an error and starts no simulation if the route cannot be found", async () => {
    const backend = mockBackend({
      ...workingBackend(),
      "GET /route": { status: 404, body: { detail: "No route found" } },
    })
    const polling = controlPolling()
    const user = userEvent.setup()
    render(<App />)
    clickMap(START)
    clickMap(DESTINATION)

    await user.click(calculateButton())

    expect(
      await screen.findByText("Could not calculate route. Is the backend running?")
    ).toBeTruthy()
    expect(backend.count("POST /simulation/start")).toBe(0)
    expect(polling.running()).toBe(0)
    expect(screen.queryByText(/Route: /)).toBeNull()
  })

  it("shows the same error when the backend cannot be reached at all", async () => {
    mockBackend({
      ...workingBackend(),
      "GET /nearest-node": new TypeError("Failed to fetch"),
    })
    const user = userEvent.setup()
    render(<App />)
    clickMap(START)
    clickMap(DESTINATION)

    await user.click(calculateButton())

    expect(
      await screen.findByText("Could not calculate route. Is the backend running?")
    ).toBeTruthy()
  })

  it("does not start polling if the simulation cannot be started", async () => {
    mockBackend({
      ...workingBackend(),
      "POST /simulation/start": { status: 500, body: { detail: "boom" } },
    })
    const polling = controlPolling()
    const user = userEvent.setup()
    render(<App />)
    clickMap(START)
    clickMap(DESTINATION)

    await user.click(calculateButton())

    expect(
      await screen.findByText("Could not calculate route. Is the backend running?")
    ).toBeTruthy()
    expect(polling.running()).toBe(0)
    expect(screen.queryByText(/Route: /)).toBeNull()
  })

  it("reports a lost connection and stops polling when an update fails", async () => {
    mockBackend({
      ...workingBackend(),
      "POST /simulation/step": new TypeError("Failed to fetch"),
    })
    const polling = controlPolling()
    await calculateRoute()

    await polling.tick()

    expect(screen.getByText(/Connection lost/)).toBeTruthy()
    expect(polling.running()).toBe(0)
  })

  it("treats an error answer from an update as a lost connection", async () => {
    mockBackend({
      ...workingBackend(),
      "POST /simulation/step": { status: 500, body: { detail: "boom" } },
    })
    const polling = controlPolling()
    await calculateRoute()

    await polling.tick()

    expect(screen.getByText(/Connection lost/)).toBeTruthy()
    expect(polling.running()).toBe(0)
  })
})

describe("road closure and rerouting", () => {
  it("is disabled until a route has been calculated", async () => {
    mockBackend(workingBackend())
    controlPolling()
    render(<App />)
    clickMap(START)
    clickMap(DESTINATION)

    const button = screen.getByRole("button", { name: /Cause Road Closure/ })
    expect((button as HTMLButtonElement).disabled).toBe(true)

    await userEvent.setup().click(calculateButton())
    await screen.findByText(/Route: /)

    expect((button as HTMLButtonElement).disabled).toBe(false)
  })

  it("closes a road on the route and shows the new route", async () => {
    const backend = mockBackend(workingBackend())
    controlPolling()
    const user = await calculateRoute()

    await user.click(screen.getByRole("button", { name: /Cause Road Closure/ }))

    expect(
      await screen.findByText(
        /Road closed: 3 of 10 active vehicles used it, 3 rerouted .* Vehicle 0 new route cost 1500 m \(55 nodes explored\)/
      )
    ).toBeTruthy()
    expect(backend.count("POST /incident/block")).toBe(1)
    expect(backend.count("POST /simulation/reroute")).toBe(1)
    expect(lines(COLOURS.reroute).map((line) => line.getAttribute("data-points"))).toEqual(["4"])
  })

  it("mentions stranded vehicles when some cannot get through", async () => {
    mockBackend({
      ...workingBackend(),
      "POST /incident/block": {
        body: {
          rerouting: { affected: 4, vehicles_checked: 10, rerouted: 3, stranded: 1 },
        },
      },
    })
    controlPolling()
    const user = await calculateRoute()

    await user.click(screen.getByRole("button", { name: /Cause Road Closure/ }))

    expect(await screen.findByText(/3 rerouted, 1 stranded/)).toBeTruthy()
  })

  it("shows the backend's explanation when the road cannot be closed", async () => {
    mockBackend({
      ...workingBackend(),
      "POST /incident/block": {
        status: 400,
        body: { detail: "Those junctions are not directly connected" },
      },
    })
    controlPolling()
    const user = await calculateRoute()

    await user.click(screen.getByRole("button", { name: /Cause Road Closure/ }))

    expect(
      await screen.findByText(
        "Incident failed: Those junctions are not directly connected"
      )
    ).toBeTruthy()
    expect(lines(COLOURS.reroute)).toHaveLength(0)
  })

  it("shows the backend's explanation when the vehicle cannot be rerouted", async () => {
    mockBackend({
      ...workingBackend(),
      "POST /simulation/reroute": {
        status: 400,
        body: { detail: "Vehicle has already finished" },
      },
    })
    controlPolling()
    const user = await calculateRoute()

    await user.click(screen.getByRole("button", { name: /Cause Road Closure/ }))

    expect(
      await screen.findByText("Incident failed: Vehicle has already finished")
    ).toBeTruthy()
    expect(lines(COLOURS.reroute)).toHaveLength(0)
  })
})
