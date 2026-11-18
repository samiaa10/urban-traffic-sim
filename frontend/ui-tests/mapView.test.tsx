// The 2D / 3D map switch. MapLibre needs WebGL, which jsdom lacks, so the
// 3D map is replaced by a stand-in that shows what the app gives it and
// lets a test click it or make it fail (the real map is covered by the
// end-to-end tests).

import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import App from "../src/App.tsx"
import type { FeatureCollection, SourceId } from "../src/mapData.ts"
import type { Coordinate } from "../src/simulationHelpers.ts"
import { mockBackend, workingBackend } from "./helpers.ts"

vi.mock("react-leaflet", async () => await import("./fakeLeaflet.tsx"))

type Props = {
  data: Record<SourceId, FeatureCollection>
  onClick: (location: Coordinate) => void
  onFailed: (reason: string) => void
  inspecting: boolean
  onInspectingChange: (inspecting: boolean) => void
}
const latest: { props: Props | null } = { props: null }

vi.mock("../src/Map3D.tsx", () => ({
  default: (props: Props) => {
    latest.props = props
    return (
      <div
        data-testid="map-3d"
        data-points={props.data.points.features.length}
        data-inspecting={String(props.inspecting)}
      />
    )
  },
}))

const view = (name: "2D" | "3D") => screen.getByRole("button", { name })

beforeEach(() => {
  localStorage.clear()
  latest.props = null
})
afterEach(() => localStorage.clear())

describe("map view", () => {
  it("starts on the 2D map", () => {
    mockBackend(workingBackend())
    render(<App />)

    expect(view("2D").getAttribute("aria-pressed")).toBe("true")
    expect(screen.getByTestId("map")).toBeTruthy()
    expect(screen.queryByTestId("map-3d")).toBeNull()
  })

  it("switches to 3D, remembers it, and switches back", async () => {
    mockBackend(workingBackend())
    render(<App />)

    await userEvent.click(view("3D"))
    expect(await screen.findByTestId("map-3d")).toBeTruthy()
    expect(screen.queryByTestId("map")).toBeNull()
    expect(view("3D").getAttribute("aria-pressed")).toBe("true")
    expect(localStorage.getItem("norwich-sim-map-view")).toBe("3d")

    await userEvent.click(view("2D"))
    expect(screen.getByTestId("map")).toBeTruthy()
    expect(screen.queryByTestId("map-3d")).toBeNull()
    expect(localStorage.getItem("norwich-sim-map-view")).toBe("2d")
  })

  it("opens on 3D when that was the last choice", async () => {
    localStorage.setItem("norwich-sim-map-view", "3d")
    mockBackend(workingBackend())
    render(<App />)

    expect(await screen.findByTestId("map-3d")).toBeTruthy()
  })

  it("places route points from clicks on the 3D map", async () => {
    localStorage.setItem("norwich-sim-map-view", "3d")
    mockBackend(workingBackend())
    render(<App />)
    const map = await screen.findByTestId("map-3d")

    act(() => latest.props!.onClick([52.63, 1.29]))
    await waitFor(() => expect(map.getAttribute("data-points")).toBe("1"))
    expect(latest.props!.data.points.features[0].geometry.coordinates).toEqual([1.29, 52.63])
    expect(screen.getByText(/Next click/).textContent).toMatch(/Destination/)
  })

  it("falls back to the 2D map, and says why, when the 3D map cannot start", async () => {
    localStorage.setItem("norwich-sim-map-view", "3d")
    mockBackend(workingBackend())
    render(<App />)
    await screen.findByTestId("map-3d")

    act(() => latest.props!.onFailed("WebGL is not available"))

    expect(screen.getByTestId("map")).toBeTruthy()
    expect(
      screen.getByText(/3D map could not start \(WebGL is not available\)/).closest(".notice--error"),
    ).toBeTruthy()
    expect(localStorage.getItem("norwich-sim-map-view")).toBe("2d")
  })

  it("labels the next click as Inspect while the 3D map inspects, without placing points", async () => {
    localStorage.setItem("norwich-sim-map-view", "3d")
    mockBackend(workingBackend())
    render(<App />)
    const map = await screen.findByTestId("map-3d")
    expect(map.getAttribute("data-inspecting")).toBe("false")

    act(() => latest.props!.onInspectingChange(true))
    expect(map.getAttribute("data-inspecting")).toBe("true")
    expect(screen.getByText(/Next click/).textContent).toMatch(/Inspect/)

    // Back in 2D the next click places a point again
    await userEvent.click(view("2D"))
    expect(screen.getByText(/Next click/).textContent).toMatch(/Start point/)
  })

  it("adds the building heights to the legend in 3D only", async () => {
    mockBackend(workingBackend())
    render(<App />)
    expect(screen.queryByText("Building height")).toBeNull()

    await userEvent.click(view("3D"))
    await screen.findByTestId("map-3d")
    expect(screen.getByText("Building height")).toBeTruthy()
    expect(screen.getByText("Over 40 m")).toBeTruthy()
  })
})
