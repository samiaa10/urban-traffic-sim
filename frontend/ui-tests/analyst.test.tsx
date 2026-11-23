// The City Analyst drawer, the scenario builder and the insights panel:
// what is asked of the backend, how answers say who wrote them and what
// they came from, that a scenario runs only after it is confirmed, and that
// it then opens in the command centre.

import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import App from "../src/App.tsx"
import { mockBackend, workingBackend, type FakeRequest } from "./helpers.ts"

vi.mock("react-leaflet", async () => await import("./fakeLeaflet.tsx"))
vi.mock("../src/CommandCentre.tsx", () => ({
  CommandCentre: ({ attachTo }: { attachTo?: string | null }) => <div data-testid="command-centre" data-run={attachTo ?? ""} />,
}))

const STATUS = {
  provider: "none", model: null, available: false, reason: "No AI provider is configured",
  fallback: "built-in rules over the same calculations", capabilities: [],
}

const RULES_ANSWER = {
  answer: "From the existing (A, B), 91.6% of residents are within 4 min by road (12,051 beyond it).\nThe areas with the most residents beyond the target:\n- Bowthorpe: 1,081 residents, median 4.4 min",
  mode: "rules", model: null, notice: "No AI model is configured, so this answer comes from the built-in rules (docs/AI.md).",
  sources: [{ tool: "coverage_gaps", input: { service: "fire", target_minutes: 4, disruption: "none" }, provenance: "calculated",
    result: { method: "road-network distance from the nearest station / service speed; no traffic" } }],
}

const AI_ANSWER = { ...RULES_ANSWER, mode: "ai", model: "claude-opus-5-5", notice: null, answer: "Bowthorpe is the weakest area." }

const PROPOSAL = {
  proposal: { title: "Close a major bridge", notes: "Assumed: 800 vehicles of background traffic", actions: [{ type: "traffic", vehicles: 800 }, { type: "close_bridges", roads: ["Duke Street"] }] },
  summary: ["800 vehicles of ordinary traffic across Norwich", "Close the central bridges: Duke Street"],
  mode: "rules", model: null, notice: "No AI model is configured, so the built-in rules read the request (docs/AI.md).",
}

function backend(answer: object = RULES_ANSWER) {
  const asked: FakeRequest[] = []
  const ran: FakeRequest[] = []
  const requests = mockBackend({
    ...workingBackend(),
    "GET /intelligence/status": { body: STATUS },
    "POST /intelligence/ask": (request) => { asked.push(request); return { body: answer } },
    "POST /intelligence/scenario/plan": { body: PROPOSAL },
    "POST /intelligence/scenario/run": (request) => {
      ran.push(request)
      return { status: 201, body: {
        simulation_id: "scenario-1", title: "Close a major bridge", applied: ["800 vehicles added", "Close the central bridges: Duke Street"],
        impact: { provenance: "calculated", method: "200 trips", roads_closed: 23, trips_longer_percent: 19.9, mean_extra_seconds: 31.6, trips_cut_off: 0 },
      } }
    },
  })
  return { asked, ran, requests }
}

const openAnalyst = async () => {
  await userEvent.click(screen.getByRole("button", { name: "Analyst" }))
  return screen.getByRole("dialog", { name: "City Analyst" })
}

beforeEach(() => localStorage.clear())
afterEach(() => localStorage.clear())

describe("the City Analyst", () => {
  it("asks the backend and shows a calculated answer with its sources", async () => {
    const { asked } = backend()
    render(<App />)
    const analyst = await openAnalyst()
    expect(await within(analyst).findByText("Built-in rules (no AI model configured)")).toBeTruthy()

    await userEvent.click(within(analyst).getByRole("button", { name: "Which areas have the worst emergency response coverage?" }))
    expect(await within(analyst).findByText("Calculated (built-in rules)")).toBeTruthy()
    expect(asked[0].body).toEqual({ question: "Which areas have the worst emergency response coverage?", simulation_id: null, history: [] })
    expect(within(analyst).getByText("Bowthorpe: 1,081 residents, median 4.4 min").className).toBe("analyst-bullet")
    expect(within(analyst).getByText(/No AI model is configured/)).toBeTruthy()
    await userEvent.click(within(analyst).getByText("1 calculation used"))
    expect(within(analyst).getByText("Emergency coverage gaps")).toBeTruthy()
    expect(within(analyst).getByText("service fire, target 4 min")).toBeTruthy()

    // A follow-up carries the conversation so far
    await userEvent.type(within(analyst).getByLabelText("Your question"), "And for police?{Enter}")
    await waitFor(() => expect(asked).toHaveLength(2))
    expect((asked[1].body as { history: unknown[] }).history).toEqual([
      { role: "user", content: "Which areas have the worst emergency response coverage?" },
      { role: "assistant", content: RULES_ANSWER.answer },
    ])
  })

  it("labels an AI-written answer as such", async () => {
    backend(AI_ANSWER)
    render(<App />)
    const analyst = await openAnalyst()
    await userEvent.type(within(analyst).getByLabelText("Your question"), "Where is cover weakest?")
    await userEvent.click(within(analyst).getByRole("button", { name: "Ask" }))
    const badge = await within(analyst).findByText("AI-written from calculated results")
    expect(badge.getAttribute("title")).toMatch(/Worded by claude-opus-5-5 from 1 calculation/)
  })

  it("says when the backend cannot answer", async () => {
    mockBackend({ ...workingBackend(), "GET /intelligence/status": { body: STATUS },
      "POST /intelligence/ask": { status: 422, body: { detail: "Questions are limited to 1000 characters" } } })
    render(<App />)
    const analyst = await openAnalyst()
    await userEvent.type(within(analyst).getByLabelText("Your question"), "Why?{Enter}")
    expect((await within(analyst).findByRole("alert")).textContent).toBe("Questions are limited to 1000 characters")
  })
})

describe("scenarios from ordinary language", () => {
  it("proposes, runs only once confirmed, and opens the run in the command centre", async () => {
    const { ran, requests } = backend()
    render(<App />)
    const analyst = await openAnalyst()
    await userEvent.click(within(analyst).getByRole("tab", { name: "Create a scenario" }))
    await userEvent.type(within(analyst).getByLabelText("Describe a scenario"), "Close a major bridge")
    await userEvent.click(within(analyst).getByRole("button", { name: "Propose a scenario" }))

    const proposal = await within(analyst).findByRole("region", { name: "Proposed scenario" })
    expect(within(proposal).getByText("Close the central bridges: Duke Street")).toBeTruthy()
    expect(within(proposal).getByText("Proposed by the built-in rules")).toBeTruthy()
    expect(within(proposal).getByText(/Nothing has run yet/)).toBeTruthy()
    expect(requests.count("POST /intelligence/scenario/run")).toBe(0)

    await userEvent.click(within(proposal).getByRole("button", { name: "Run this scenario" }))
    const running = await within(analyst).findByRole("region", { name: "Scenario running" })
    expect(ran[0].body).toEqual({ proposal: PROPOSAL.proposal, confirm: true })
    expect(within(running).getByText(/make 19.9% of sampled trips longer, by 32 s/)).toBeTruthy()

    await userEvent.click(within(running).getByRole("button", { name: "Watch it in the command centre" }))
    expect(screen.queryByRole("dialog", { name: "City Analyst" })).toBeNull()
    expect(screen.getByTestId("command-centre").getAttribute("data-run")).toBe("scenario-1")
  })

  it("can be discarded without running anything", async () => {
    const { requests } = backend()
    render(<App />)
    const analyst = await openAnalyst()
    await userEvent.click(within(analyst).getByRole("tab", { name: "Create a scenario" }))
    await userEvent.click(within(analyst).getByRole("button", { name: "Simulate a major flood in Norwich city centre." }))
    await userEvent.click(within(await within(analyst).findByRole("region", { name: "Proposed scenario" })).getByRole("button", { name: "Discard" }))
    expect(within(analyst).queryByRole("region", { name: "Proposed scenario" })).toBeNull()
    expect(requests.count("POST /intelligence/scenario/run")).toBe(0)
  })
})

describe("the insights panel", () => {
  it("asks nothing until run, then shows the calculated findings", async () => {
    const insight = (within: number) => ({
      layout: "existing", stations: ["A"], method: "road-network distance", summary: { population_within_target_percent: within, population_beyond_target: 100 },
      worst_areas: [{ where: "Bowthorpe", residents_beyond_target: 50, median_seconds: 270 }],
    })
    const station = { before: { population_within_target_percent: 90 }, candidates_tried: 38,
      best: [{ where: "about 0.9 km from Bowthorpe", population_within_target_percent: 97.7, residents_brought_within_target: 8716 }] }
    const requests = mockBackend({
      ...workingBackend(),
      "GET /intelligence/insights": { body: {
        coverage: { fire: insight(91.6), police: insight(98.3) }, stations: { fire: station, police: station },
        critical_roads: { trips_sampled: 300, method: "300 trips", roads: [{ road: "Dereham Road", where: "Heigham", trips_using_it_percent: 17, mean_extra_seconds_if_closed: 12.2, trips_cut_off_if_closed: 0 }] },
        run: null,
      } },
    })
    render(<App />)
    expect(requests.count("GET /intelligence/insights")).toBe(0)
    await userEvent.click(screen.getByRole("button", { name: "Run the analysis" }))
    const fire = await screen.findByRole("article", { name: "Fire coverage" })
    expect(within(fire).getByText("91.6%")).toBeTruthy()
    expect(within(fire).getByText(/one more fire station about 0.9 km from Bowthorpe would cover 97.7%/)).toBeTruthy()
    expect(within(screen.getByRole("article", { name: "Critical roads" })).getByText("Dereham Road")).toBeTruthy()
    expect(within(screen.getByRole("article", { name: "Live simulation" })).getByText(/No live simulation is running/)).toBeTruthy()
  })
})
