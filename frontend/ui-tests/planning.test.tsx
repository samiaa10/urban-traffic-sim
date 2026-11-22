// The planning view: settings ask the backend again, the figures and
// layouts shown are the backend's, the optimisation plays step by step,
// and the 2D map comes back if the 3D map cannot start. MapLibre needs
// WebGL, so the planning canvas is a stand-in that reports what it was
// given (the real map is covered by the end-to-end tests).

import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import App from "../src/App.tsx"
import type { FeatureCollection } from "../src/mapData.ts"
import type { Plan, Stage } from "../src/planning.ts"
import { mockBackend, workingBackend, type FakeRequest } from "./helpers.ts"

vi.mock("react-leaflet", async () => await import("./fakeLeaflet.tsx"))

type CanvasProps = {
  plan: Plan | null
  seconds: (number | null)[] | null
  fixed: FeatureCollection | null
  columns: FeatureCollection
  onFailed: (reason: string) => void
}
const latest: { props: CanvasProps | null } = { props: null }

vi.mock("../src/PlanningCanvas.tsx", () => ({
  default: (props: CanvasProps) => {
    latest.props = props
    const kinds = props.columns.features.map((f) => f.properties.kind).join(",")
    return (
      <div
        data-testid="planning-map"
        data-seconds={props.seconds?.join(",") ?? ""}
        data-fixed={props.fixed ? "difference" : ""}
        data-columns={kinds}
      />
    )
  },
}))

const summary = (within: number, mean: number) => ({
  junctions: 10, unreachable_junctions: 0, mean_seconds: mean, median_seconds: mean, p90_seconds: mean * 1.5,
  worst_seconds: mean * 2, junctions_within_target_percent: within, population_within_target_percent: within,
  population_beyond_target: Math.round((100 - within) * 100),
})
const stage = (label: string, seconds: number[], within: number, mean: number): Stage => ({
  label, summary: summary(within, mean),
  cells: { seconds, within: seconds.map(() => 1), beyond_people: seconds.map(() => 0) },
})

function makePlan(query: URLSearchParams): Plan {
  const service = query.get("service") as Plan["service"]
  const stations = Number(query.get("stations"))
  const existing = service === "ambulance" ? [] : [{
    name: "Sprowston", osm: "node/1", latitude: 52.65, longitude: 1.31,
    snapped: { latitude: 52.65, longitude: 1.31 }, snap_metres: 5,
  }]
  const picks = Array.from({ length: stations }, (_, i) => i + 1)
  return {
    service, speed: 17, target_seconds: Number(query.get("target_seconds")),
    disruption: query.get("disruption") as Plan["disruption"], closed_roads: 0,
    model: "road distance / speed", hex_metres: 180, existing, existing_left_out: [],
    candidates: Array.from({ length: 6 }, (_, i) => ({
      latitude: 52.6 + i / 100, longitude: 1.28, rank: i + 1, average_seconds: 200, within_target_percent: 60,
    })),
    search: { candidates: 6, possible_sets: 20, method: "every possible set" },
    optimised: picks.map((order) => ({
      order, candidate: order, latitude: 52.63, longitude: 1.29 + order / 100,
      average_seconds: 200 - order * 10, worst_seconds: 500, within_target_percent: 80 + order,
    })),
    cells: [0, 1].map((i) => ({ polygon: [[1.29 + i / 100, 52.63], [1.3, 52.63], [1.3, 52.64], [1.29 + i / 100, 52.63]], junctions: 5, population: 300 })),
    stages: [
      ...(existing.length ? [stage("existing", [300, 400], 71.5, 260)] : []),
      ...picks.map((n) => stage(`optimised:${n}`, [200 - n, 220 - n], 80 + n, 200 - n * 10)),
    ],
  }
}

function planningBackend() {
  const asked: FakeRequest[] = []
  const requests = mockBackend({
    ...workingBackend(),
    "GET /optimiser/plan": (request) => {
      asked.push(request)
      return { body: makePlan(request.query) }
    },
  })
  return { asked, requests }
}

const openPlanning = async () => {
  await userEvent.click(screen.getByRole("button", { name: "Plan" }))
  return await screen.findByTestId("planning-map")
}

beforeEach(() => {
  localStorage.clear()
  latest.props = null
})
afterEach(() => {
  vi.useRealTimers()
  localStorage.clear()
})

describe("planning view", () => {
  it("asks for a plan and shows the existing and optimised figures", async () => {
    const { asked } = planningBackend()
    render(<App />)
    const map = await openPlanning()

    await waitFor(() => expect(asked).toHaveLength(1))
    expect(asked[0].query.get("service")).toBe("fire")
    expect(asked[0].query.get("stations")).toBe("3")
    expect(asked[0].query.get("target_seconds")).toBe("240")
    expect(asked[0].query.get("disruption")).toBe("none")
    expect(localStorage.getItem("norwich-sim-map-view")).toBe("plan")

    const table = await screen.findByRole("table", { name: "Existing and optimised layouts" })
    const row = within(table).getByRole("row", { name: /Residents within 4 min/ })
    expect(within(row).getByText("71.5%")).toBeTruthy()
    expect(within(row).getByText("83.0%").className).toBe("planning-better")
    // The map shows the optimised layout's own values, with its stations
    await waitFor(() => expect(map.getAttribute("data-seconds")).toBe("197,217"))
    expect(map.getAttribute("data-columns")).toBe("existing,proposed,proposed,proposed")
  })

  it("asks again when a setting changes", async () => {
    const { asked } = planningBackend()
    render(<App />)
    await openPlanning()
    await screen.findByRole("table", { name: "Existing and optimised layouts" })
    const controls = screen.getByRole("region", { name: "Planning controls" })

    await userEvent.selectOptions(within(controls).getByRole("combobox", { name: "Service" }), "police")
    await userEvent.selectOptions(within(controls).getByRole("combobox", { name: "Disruption" }), "flood")
    await userEvent.selectOptions(within(controls).getByRole("combobox", { name: "Stations" }), "2")

    await waitFor(() => expect(asked.at(-1)!.query.get("stations")).toBe("2"))
    const last = asked.at(-1)!.query
    expect(last.get("service")).toBe("police")
    expect(last.get("disruption")).toBe("flood")
    expect(await screen.findByText(/Police: existing against optimised/)).toBeTruthy()
  })

  it("switches between the existing, optimised and difference layouts", async () => {
    planningBackend()
    render(<App />)
    const map = await openPlanning()
    await screen.findByRole("table", { name: "Existing and optimised layouts" })
    const views = screen.getByRole("group", { name: "Layout shown" })

    await userEvent.click(within(views).getByRole("button", { name: "Existing" }))
    expect(map.getAttribute("data-seconds")).toBe("300,400")
    expect(map.getAttribute("data-columns")).toBe("existing")

    await userEvent.click(within(views).getByRole("button", { name: "Difference" }))
    expect(map.getAttribute("data-fixed")).toBe("difference")
    expect(screen.getByText("Time saved by the optimised layout")).toBeTruthy()
  })

  it("has no existing layout to show when none is mapped", async () => {
    planningBackend()
    render(<App />)
    await openPlanning()
    await screen.findByRole("table", { name: "Existing and optimised layouts" })
    const controls = screen.getByRole("region", { name: "Planning controls" })
    await userEvent.selectOptions(within(controls).getByRole("combobox", { name: "Service" }), "ambulance")

    expect(await screen.findByText(/No road ambulance stations are mapped/)).toBeTruthy()
    const views = screen.getByRole("group", { name: "Layout shown" })
    expect((within(views).getByRole("button", { name: "Existing" }) as HTMLButtonElement).disabled).toBe(true)
    expect((within(views).getByRole("button", { name: "Difference" }) as HTMLButtonElement).disabled).toBe(true)
  })

  it("steps through the optimisation as the algorithm ran", async () => {
    planningBackend()
    render(<App />)
    const map = await openPlanning()
    await screen.findByRole("table", { name: "Existing and optimised layouts" })
    const next = screen.getByRole("button", { name: "Next step" })

    await userEvent.click(next)
    const step = () => screen.getByRole("region", { name: "Optimisation step" })
    expect(step().textContent).toMatch(/Step 1 of 6.*Existing fire stations/)
    expect(map.getAttribute("data-seconds")).toBe("300,400")

    await userEvent.click(next)
    expect(step().textContent).toMatch(/6 candidate sites/)
    expect(map.getAttribute("data-columns")).toBe("existing" + ",candidate".repeat(6))

    await userEvent.click(next)
    await userEvent.click(next)
    expect(step().textContent).toMatch(/Station 1 of 3/)
    expect(map.getAttribute("data-seconds")).toBe("199,219")
    expect(map.getAttribute("data-columns")).toBe("existing,proposed")

    await userEvent.click(screen.getByRole("button", { name: "Previous step" }))
    expect(step().textContent).toMatch(/most promising/)
  })

  it("plays every step and stops at the last", async () => {
    planningBackend()
    render(<App />)
    await openPlanning()
    await screen.findByRole("table", { name: "Existing and optimised layouts" })

    const play = screen.getByRole("group", { name: "Optimisation steps" })
    vi.useFakeTimers()
    act(() => within(play).getByRole("button", { name: "Play the optimisation" }).click())
    expect(within(play).getByRole("button", { name: "Pause" })).toBeTruthy()
    for (let i = 0; i < 6; i++) await act(async () => { vi.advanceTimersByTime(2600) })

    expect(screen.getByRole("region", { name: "Optimisation step" }).textContent).toMatch(/Step 6 of 6.*Station 3 of 3/)
    expect(within(play).getByRole("button", { name: "Play the optimisation" })).toBeTruthy()
  })

  it("says why when the plan cannot be calculated", async () => {
    mockBackend({ ...workingBackend(), "GET /optimiser/plan": { status: 503, body: { detail: "The road network is not loaded" } } })
    render(<App />)
    await openPlanning()
    expect((await screen.findByRole("alert")).textContent).toBe("The road network is not loaded")
  })

  it("falls back to the 2D map when the planning map cannot start", async () => {
    planningBackend()
    render(<App />)
    await openPlanning()

    act(() => latest.props!.onFailed("WebGL is not available"))

    expect(screen.getByTestId("map")).toBeTruthy()
    expect(screen.queryByTestId("planning-map")).toBeNull()
    expect(screen.getByText(/planning map could not start \(WebGL is not available\)/)).toBeTruthy()
    expect(localStorage.getItem("norwich-sim-map-view")).toBe("2d")
  })
})
