// The overall screen: header, navigation, labelled regions, empty states,
// route steps, metric cards and the waiting state of the Calculate button.

import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import App from "../src/App.tsx"
import {
  DESTINATION,
  START,
  clickMap,
  controlPolling,
  deferred,
  mockBackend,
  workingBackend,
  type Reply,
} from "./helpers.ts"

vi.mock("react-leaflet", async () => await import("./fakeLeaflet.tsx"))

describe("page structure", () => {
  it("shows the product name and a navigation to each section", () => {
    mockBackend(workingBackend())
    render(<App />)

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "NORWICH//SIM"
    )

    const nav = screen.getByRole("navigation", { name: "Sections" })
    const hrefs = within(nav)
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"))

    expect(hrefs.slice(0, 3)).toEqual(["#simulation", "#emergency", "#optimiser"])
    expect(
      within(nav).getByRole("link", { name: "Source code on GitHub" })
    ).toBeTruthy()
  })

  it("gives the controls, the map and each group of controls a name", () => {
    mockBackend(workingBackend())
    render(<App />)

    expect(screen.getByRole("main")).toBeTruthy()
    expect(screen.getByRole("complementary", { name: "Controls" })).toBeTruthy()
    expect(screen.getByRole("region", { name: "Norwich map" })).toBeTruthy()

    for (const name of ["Route simulation", "Road closure", "Emergency response", "Station optimiser"]) {
      expect(screen.getByRole("region", { name })).toBeTruthy()
    }
  })

  it("is reachable by keyboard: the skip link points at the map", () => {
    mockBackend(workingBackend())
    render(<App />)

    const skip = screen.getByRole("link", { name: "Skip to the map" })

    expect(skip.getAttribute("href")).toBe("#map")
    expect(document.getElementById("map")).toBeTruthy()
  })
})

describe("empty states and route steps", () => {
  it("explains what to do before a route exists, then stops once one is planned", () => {
    mockBackend(workingBackend())
    render(<App />)

    expect(screen.getByText("No route yet")).toBeTruthy()
    expect(screen.getByText("No active emergency")).toBeTruthy()
    expect(screen.getByText("No optimisation results yet")).toBeTruthy()

    clickMap(START)
    clickMap(DESTINATION)

    expect(screen.queryByText("No route yet")).toBeNull()
    expect(screen.queryByText("No active emergency")).toBeNull()
    // The optimiser does not need a route
    expect(screen.getByText("No optimisation results yet")).toBeTruthy()
  })

  it("lists the chosen points in the route steps", () => {
    mockBackend(workingBackend())
    render(<App />)

    clickMap(START)
    expect(screen.getByText("52.6300, 1.2900")).toBeTruthy()
    expect(screen.queryByText("52.6500, 1.3100")).toBeNull()

    clickMap(DESTINATION)
    expect(screen.getByText("52.6500, 1.3100")).toBeTruthy()
  })
})

describe("metric cards and waiting states", () => {
  it("shows dashes before a route and the route length and search effort after", async () => {
    mockBackend(workingBackend())
    controlPolling()
    const user = userEvent.setup()
    render(<App />)

    expect(screen.getByText("No route calculated")).toBeTruthy()

    clickMap(START)
    clickMap(DESTINATION)
    await user.click(screen.getByRole("button", { name: /Calculate Route/ }))

    expect(await screen.findByText(/^1\D?234$/)).toBeTruthy()
    expect(screen.getByText("A*, 77 nodes explored")).toBeTruthy()
    expect(screen.queryByText("No route calculated")).toBeNull()
  })

  it("disables Calculate Route and marks it busy while the route is being found", async () => {
    const answer = deferred<Reply>()
    mockBackend({
      ...workingBackend(),
      "GET /route": () => answer.promise,
    })
    controlPolling()
    const user = userEvent.setup()
    render(<App />)
    clickMap(START)
    clickMap(DESTINATION)

    await user.click(screen.getByRole("button", { name: /Calculate Route/ }))

    const button = screen.getByRole("button", {
      name: /Calculate Route/,
    }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    expect(button.getAttribute("aria-busy")).toBe("true")

    const route = workingBackend()["GET /route"] as Reply
    answer.resolve(route)

    expect(await screen.findByText(/Route: /)).toBeTruthy()
    // The button frees up once the whole request has finished
    await waitFor(() => expect(button.disabled).toBe(false))
    expect(button.getAttribute("aria-busy")).toBe("false")
  })
})
