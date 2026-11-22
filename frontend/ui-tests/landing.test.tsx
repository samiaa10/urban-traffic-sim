// The landing band: the city model's figures from GET /city/overview,
// the ways into each part of the site, and folding it away.

import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import App from "../src/App.tsx"
import { mockBackend, workingBackend } from "./helpers.ts"

vi.mock("react-leaflet", async () => await import("./fakeLeaflet.tsx"))
vi.mock("../src/CommandCentre.tsx", () => ({ CommandCentre: () => <div data-testid="command-centre" /> }))

const OVERVIEW = {
  junctions: 5058, road_segments: 5853, road_km: 403.4, signalised_junctions: 121,
  osm_stations: { ambulance: 0, fire: 3, police: 4 },
  population: { residents: 143901, output_areas: 471, source: "Census 2021 (ONS)" },
  live_runs: 2, database: "not configured",
  ai: { provider: "none", model: null, available: false, reason: "No AI provider is configured" },
}

beforeEach(() => localStorage.clear())
afterEach(() => localStorage.clear())

describe("landing", () => {
  it("shows the city model's figures from the backend", async () => {
    mockBackend({ ...workingBackend(), "GET /city/overview": { body: OVERVIEW } })
    render(<App />)
    const band = screen.getByRole("region", { name: "About NORWICH//SIM" })
    expect(await within(band).findByText("403.4 km")).toBeTruthy()
    expect(within(band).getByText("5,853 roads, 5,058 junctions")).toBeTruthy()
    expect(within(band).getByText("143,901")).toBeTruthy()
    expect(within(band).getByText("3 fire, 4 police, 0 ambulance")).toBeTruthy()
    expect(screen.getByText("Model online · 2 live runs")).toBeTruthy()
  })

  it("says nothing it does not know when the backend is unreachable", async () => {
    mockBackend({ ...workingBackend(), "GET /city/overview": new Error("offline") })
    render(<App />)
    const band = screen.getByRole("region", { name: "About NORWICH//SIM" })
    expect(within(band).getByText("Loading…")).toBeTruthy()
    expect(screen.getByText("Connecting…")).toBeTruthy()
  })

  it("opens the command centre from the band", async () => {
    mockBackend({ ...workingBackend(), "GET /city/overview": { body: OVERVIEW } })
    render(<App />)
    await userEvent.click(within(screen.getByRole("region", { name: "About NORWICH//SIM" })).getByRole("button", { name: "Command centre" }))
    expect(screen.getByTestId("command-centre")).toBeTruthy()
    expect(localStorage.getItem("norwich-sim-map-view")).toBe("command")
  })

  it("folds away and remembers it", async () => {
    mockBackend({ ...workingBackend(), "GET /city/overview": { body: OVERVIEW } })
    const { unmount } = render(<App />)
    await userEvent.click(screen.getByRole("button", { name: "Fold the introduction away" }))
    expect(screen.queryByRole("region", { name: "About NORWICH//SIM" })).toBeNull()
    unmount()
    render(<App />)
    expect(screen.queryByRole("region", { name: "About NORWICH//SIM" })).toBeNull()
    await userEvent.click(screen.getByRole("button", { name: /About this digital twin/ }))
    expect(screen.getByRole("region", { name: "About NORWICH//SIM" })).toBeTruthy()
  })
})
