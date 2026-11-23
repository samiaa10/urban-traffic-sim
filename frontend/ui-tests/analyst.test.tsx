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
    "POST /intelligence/ask/stream": (request) => { asked.push(request); return { body: answer } },
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
    expect(await within(analyst).findByText("No AI model on this server: built-in rules")).toBeTruthy()

    await userEvent.click(within(analyst).getByRole("button", { name: "Which areas have the worst emergency response coverage, and why?" }))
    expect(await within(analyst).findByText("Calculated (built-in rules)")).toBeTruthy()
    expect(asked[0].body).toEqual({ question: "Which areas have the worst emergency response coverage, and why?", simulation_id: null, history: [] })
    expect(within(analyst).getByText("Bowthorpe: 1,081 residents, median 4.4 min").className).toBe("analyst-bullet")
    expect(within(analyst).getByText(/No AI model is configured/)).toBeTruthy()
    await userEvent.click(within(analyst).getByText("1 calculation used"))
    expect(within(analyst).getByText("Emergency coverage gaps")).toBeTruthy()
    expect(within(analyst).getByText("service fire, target 4 min")).toBeTruthy()

    // A follow-up carries the conversation so far
    await userEvent.type(within(analyst).getByLabelText("Your question"), "And for police?{Enter}")
    await waitFor(() => expect(asked).toHaveLength(2))
    expect((asked[1].body as { history: unknown[] }).history).toEqual([
      { role: "user", content: "Which areas have the worst emergency response coverage, and why?" },
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
      "POST /intelligence/ask/stream": { status: 422, body: { detail: "Questions are limited to 1000 characters" } } })
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
  const insight = (within: number) => ({
    layout: "existing", stations: ["A"], method: "road-network distance", summary: { population_within_target_percent: within, population_beyond_target: 100 },
    worst_areas: [{ where: "Bowthorpe", residents_beyond_target: 50, median_seconds: 270 }],
  })
  const station = { before: { population_within_target_percent: 90 }, candidates_tried: 38,
    best: [{ where: "about 0.9 km from Bowthorpe", population_within_target_percent: 97.7, residents_brought_within_target: 8716 }] }
  const INSIGHTS = {
    coverage: { fire: insight(91.6), police: insight(98.3) }, stations: { fire: station, police: station },
    critical_roads: { trips_sampled: 300, method: "300 trips", roads: [{ road: "Dereham Road", where: "Heigham", trips_using_it_percent: 17, mean_extra_seconds_if_closed: 12.2, trips_cut_off_if_closed: 0 }] },
    run: null,
  }

  it("asks nothing until run, then shows the calculated findings", async () => {
    const requests = mockBackend({
      ...workingBackend(),
      "GET /intelligence/insights": { body: INSIGHTS },
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

  it("has the local model explain the findings, labelled as AI-generated", async () => {
    const LOCAL = { ...STATUS, provider: "ollama", model: "qwen2.5:3b", available: true, reason: null, local: true }
    const explained = { answer: "…", mode: "ai", model: "qwen2.5:3b", notice: null, sources: [], facts: [],
      explanation: "Bowthorpe is furthest from a fire station by road.", explanation_note: null, proposal: null }
    mockBackend({
      ...workingBackend(), "GET /intelligence/insights": { body: INSIGHTS }, "GET /intelligence/status": { body: LOCAL },
      "POST /intelligence/ask/stream": { lines: [{ stage: "planning" }, { stage: "explaining" }, { stage: "done", answer: explained }] },
    })
    render(<App />)
    await userEvent.click(screen.getByRole("button", { name: "Run the analysis" }))
    await userEvent.click(await screen.findByRole("button", { name: "Explain these findings with qwen2.5:3b" }))
    expect(await screen.findByText("Bowthorpe is furthest from a fire station by road.")).toBeTruthy()
    expect(screen.getByText("AI-generated · qwen2.5:3b")).toBeTruthy()
  })

  it("says when no AI model is available to explain them", async () => {
    mockBackend({ ...workingBackend(), "GET /intelligence/insights": { body: INSIGHTS },
      "GET /intelligence/status": { body: { ...STATUS, available: false, reason: null } } })
    render(<App />)
    await userEvent.click(screen.getByRole("button", { name: "Run the analysis" }))
    expect(await screen.findByText(/No AI model is available on this server/)).toBeTruthy()
    expect(screen.queryByRole("button", { name: /Explain these findings/ })).toBeNull()
  })
})

describe("the local AI analyst", () => {
  const LOCAL = { ...STATUS, provider: "ollama", model: "qwen2.5:3b", available: true, reason: null, local: true }
  const FACTS = [{ tool: "coverage_gaps", text: "From the existing (A), 91.6% of residents are within 4 min by road.\n- Bowthorpe: 1,081 residents, median 4.4 min, 4.4 km by road from North Earlham Fire Station" }]
  const LOCAL_ANSWER = {
    answer: "…", mode: "ai", model: "qwen2.5:3b", notice: null, sources: RULES_ANSWER.sources, facts: FACTS,
    explanation: "Bowthorpe is furthest by road from North Earlham Fire Station, so it is the weakest area.",
    explanation_note: null, proposal: null,
  }

  it("shows the calculated facts apart from the AI's explanation", async () => {
    mockBackend({ ...workingBackend(), "GET /intelligence/status": { body: LOCAL }, "POST /intelligence/ask/stream": { body: LOCAL_ANSWER } })
    render(<App />)
    const analyst = await openAnalyst()
    expect(await within(analyst).findByText("Local AI: qwen2.5:3b (Ollama, on this machine)")).toBeTruthy()
    await userEvent.type(within(analyst).getByLabelText("Your question"), "Where is cover weakest, and why?{Enter}")
    const facts = await within(analyst).findByRole("region", { name: "Calculated facts" })
    expect(within(facts).getByText("Calculated")).toBeTruthy()
    expect(within(facts).getByText(/Bowthorpe: 1,081 residents/)).toBeTruthy()
    const explanation = within(analyst).getByRole("region", { name: "AI explanation" })
    expect(within(explanation).getByText("AI-generated")).toBeTruthy()
    expect(within(explanation).getByText(/Explanation by qwen2.5:3b/)).toBeTruthy()
    expect(within(explanation).getByText(/furthest by road from North Earlham/)).toBeTruthy()
  })

  it("shows each stage while the local model works, and the facts before the explanation", async () => {
    let finish = () => {}
    const until = new Promise<void>((resolve) => { finish = resolve })
    mockBackend({
      ...workingBackend(), "GET /intelligence/status": { body: LOCAL },
      "POST /intelligence/ask/stream": { until, lines: [
        { stage: "planning" },
        { stage: "calculated", answer: { ...LOCAL_ANSWER, explanation: null, pending: true } },
        { stage: "explaining" },
        { stage: "done", answer: LOCAL_ANSWER },
      ] },
    })
    render(<App />)
    const analyst = await openAnalyst()
    await within(analyst).findByText("Local AI: qwen2.5:3b (Ollama, on this machine)")
    await userEvent.type(within(analyst).getByLabelText("Your question"), "Where is cover weakest, and why?{Enter}")
    // The facts are there while the model is still explaining them
    const facts = await within(analyst).findByRole("region", { name: "Calculated facts" })
    expect(within(facts).getByText(/Bowthorpe: 1,081 residents/)).toBeTruthy()
    expect(within(analyst).getByText("qwen2.5:3b is explaining these facts…")).toBeTruthy()
    const progress = within(analyst).getByRole("status", { name: "Progress" })
    expect(within(progress).getByText("Ran the simulation's calculations").className).toBe("is-done")
    expect(within(progress).getByText("Writing an explanation of the calculated facts").className).toBe("is-active")
    expect(within(progress).getByText(/qwen2.5:3b, running on this machine · \d+ s/)).toBeTruthy()
    finish()
    expect(await within(analyst).findByText(/furthest by road from North Earlham/)).toBeTruthy()
    expect(within(analyst).queryByRole("status", { name: "Progress" })).toBeNull()
    expect(within(analyst).getAllByRole("region", { name: "Calculated facts" })).toHaveLength(1)
  })

  it("says why an explanation was withheld, and keeps the facts", async () => {
    const withheld = { ...LOCAL_ANSWER, explanation: null,
      explanation_note: "The local model's explanation used figures no calculation produced (45), so only the calculated facts are shown." }
    mockBackend({ ...workingBackend(), "GET /intelligence/status": { body: LOCAL }, "POST /intelligence/ask/stream": { body: withheld } })
    render(<App />)
    const analyst = await openAnalyst()
    await userEvent.type(within(analyst).getByLabelText("Your question"), "Why?{Enter}")
    const explanation = await within(analyst).findByRole("region", { name: "AI explanation" })
    expect(within(explanation).getByText(/figures no calculation produced \(45\)/)).toBeTruthy()
    expect(within(analyst).getByRole("region", { name: "Calculated facts" })).toBeTruthy()
  })

  it("offers a scenario it proposed in the conversation, and runs it only when confirmed", async () => {
    const withProposal = { ...LOCAL_ANSWER, proposal: { ...PROPOSAL, mode: "ai", model: "qwen2.5:3b", explanation: "Duke Street closes." } }
    const ran: FakeRequest[] = []
    mockBackend({
      ...workingBackend(), "GET /intelligence/status": { body: LOCAL }, "POST /intelligence/ask/stream": { body: withProposal },
      "POST /intelligence/scenario/run": (request) => { ran.push(request); return { status: 201, body: { simulation_id: "s-9", title: "x", applied: [], impact: null } } },
    })
    render(<App />)
    const analyst = await openAnalyst()
    await userEvent.type(within(analyst).getByLabelText("Your question"), "Simulate two bridges closing{Enter}")
    const proposal = await within(analyst).findByRole("region", { name: "Proposed scenario" })
    expect(within(proposal).getByText("Scenario proposed by qwen2.5:3b")).toBeTruthy()
    expect(within(proposal).getByText(/Duke Street closes/)).toBeTruthy()
    expect(ran).toHaveLength(0)
    await userEvent.click(within(proposal).getByRole("button", { name: "Run this scenario" }))
    await userEvent.click(await within(proposal).findByRole("button", { name: "Watch it in the command centre" }))
    expect((ran[0].body as { confirm: boolean }).confirm).toBe(true)
    expect(screen.getByTestId("command-centre").getAttribute("data-run")).toBe("s-9")
  })

  it("says plainly when the local model is not running", async () => {
    mockBackend({ ...workingBackend(), "GET /intelligence/status": { body: { ...LOCAL, available: false, reason: "Ollama is not running at http://localhost:11434" } } })
    render(<App />)
    const analyst = await openAnalyst()
    expect(await within(analyst).findByText(/Local AI unavailable \(Ollama is not running at http:\/\/localhost:11434\): built-in rules/)).toBeTruthy()
  })
})
