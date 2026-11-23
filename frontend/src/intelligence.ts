// The AI City Analyst and scenario builder on the client (backend
// intelligence/: POST /intelligence/ask, /intelligence/scenario/plan and
// /run). No React here: tests/intelligence.test.ts.

import { API_URL } from "./config.ts"

export type Source = { tool: string; input: Record<string, unknown>; provenance: "calculated"; result: Record<string, unknown> }

export type Fact = { tool: string; text: string }

export type Answer = {
  answer: string
  mode: "ai" | "rules"
  model: string | null
  sources: Source[]
  notice: string | null
  // The calculated facts (templates over the results: exact) and, with a
  // local model, its explanation of them (checked: every figure in it
  // comes from a calculation, or it is withheld and explanation_note says why)
  facts?: Fact[]
  explanation?: string | null
  explanation_note?: string | null
  // A scenario the user asked to simulate: proposed, not run
  proposal?: Proposal | null
}

export type Status = {
  provider: string
  model: string | null
  available: boolean
  reason: string | null
  local?: boolean
  fallback: string
  capabilities: { name: string; description: string }[]
}

export const EXAMPLES = [
  "Which areas have the worst emergency response coverage, and why?",
  "What would happen if two major bridges closed?",
  "Find the areas most vulnerable to flooding.",
  "How could we improve ambulance response times?",
  "Where should we add a fire station, and what are the trade-offs?",
  "Why did this simulation perform poorly?",
]

// Who answers, in a few words, for the analyst's header
export function modeLabel(status: Status | null): { text: string; ai: boolean } {
  if (status === null) return { text: "Connecting…", ai: false }
  if (status.available && status.local) return { text: `Local AI: ${status.model} (Ollama, on this machine)`, ai: true }
  if (status.available) return { text: `AI: ${status.model}`, ai: true }
  if (status.provider === "ollama") return { text: `Local AI unavailable (${status.reason ?? "Ollama not running"}): built-in rules`, ai: false }
  return { text: "No AI model on this server: built-in rules", ai: false }
}

export const TOOL_LABELS: Record<string, string> = {
  city_overview: "City model figures",
  coverage_gaps: "Emergency coverage gaps",
  recommend_station: "Best extra station",
  critical_roads: "Roads trips depend on",
  closure_impact: "Road closure impact",
  station_plan: "Station layout plan",
  flood_vulnerability: "Flood vulnerability",
  live_bottlenecks: "Live bottlenecks",
  run_report: "Simulation report",
  scenario_results: "Scenario study ranking",
}

// What an answer is: who wrote it, from what
export function provenanceOf(answer: Answer): { label: string; detail: string; tone: "ai" | "calc" } {
  const count = answer.sources.length
  const calculations = `${count} calculation${count === 1 ? "" : "s"}`
  if (answer.mode === "ai" && answer.facts) {
    return {
      label: `Calculated facts, explained by ${answer.model ?? "the local model"}`,
      detail: `The local model chose ${calculations}; the facts are the calculations' own results, and the explanation is checked against them.`,
      tone: "ai",
    }
  }
  if (answer.mode === "ai") {
    return {
      label: "AI-written from calculated results",
      detail: `Worded by ${answer.model ?? "the AI model"} from ${calculations}; numbers come from the calculations below.`,
      tone: "ai",
    }
  }
  return {
    label: "Calculated (built-in rules)",
    detail: `Worded by the built-in rules from ${calculations}; no AI model was used.`,
    tone: "calc",
  }
}

// A calculation's inputs in a few words ("service fire, target 4 min")
export function describeInput(input: Record<string, unknown>): string {
  const parts: string[] = []
  for (const [key, value] of Object.entries(input)) {
    if (value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0)) continue
    if (key === "target_minutes") parts.push(`target ${value} min`)
    else if (key === "central_bridges") { if (value) parts.push("central bridges") }
    else if (key === "disruption") { if (value !== "none") parts.push(`${value}`) }
    else if (Array.isArray(value)) parts.push(value.join(", "))
    else parts.push(`${key.replace(/_/g, " ")} ${value}`)
  }
  return parts.join(", ")
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail
      : Array.isArray(data.detail) ? "The request was not valid" : `Request failed (${response.status})`
    throw new Error(detail)
  }
  return data as T
}

export function ask(question: string, simulationId: string | null, history: { role: "user" | "assistant"; content: string }[]) {
  return post<Answer>("/intelligence/ask", { question, simulation_id: simulationId, history: history.slice(-12) })
}

// What a local model is doing while it answers (POST /intelligence/ask/stream)
export type Stage = "planning" | "calculated" | "proposing" | "explaining" | "done"

export type StageEvent = { stage: Stage; answer?: Answer & { pending?: boolean } }

export const STAGE_TEXT: Record<Exclude<Stage, "done">, string> = {
  planning: "Reading the question and choosing the calculations",
  calculated: "Calculated",
  proposing: "Drafting a scenario for you to check",
  explaining: "Writing an explanation of the calculated facts",
}

// The answer, streamed: onStage hears each stage (and the calculated facts
// as soon as they exist, before the model explains them). Falls back to a
// plain JSON answer from a server that does not stream.
export async function askStream(
  question: string,
  simulationId: string | null,
  history: { role: "user" | "assistant"; content: string }[],
  onStage: (event: StageEvent) => void,
): Promise<Answer> {
  const response = await fetch(`${API_URL}/intelligence/ask/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, simulation_id: simulationId, history: history.slice(-12) }),
  })
  if (response.status === 404 || response.status === 405) return ask(question, simulationId, history)
  const streamed = (response.headers?.get?.("Content-Type") ?? "").includes("ndjson") && response.body
  if (!response.ok || !streamed) {
    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${response.status})`)
    }
    return data as Answer
  }
  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffered = ""
  let final: Answer | null = null
  const read = (line: string) => {
    if (!line.trim()) return
    const event = JSON.parse(line) as StageEvent | { stage: "error"; detail: string }
    if (event.stage === "error") throw new Error((event as { detail: string }).detail)
    if (event.stage === "done" && event.answer) final = event.answer
    onStage(event as StageEvent)
  }
  for (;;) {
    const { value, done } = await reader.read()
    buffered += decoder.decode(value ?? new Uint8Array(), { stream: !done })
    const lines = buffered.split("\n")
    buffered = lines.pop() ?? ""
    lines.forEach(read)
    if (done) break
  }
  read(buffered)
  if (!final) throw new Error("The answer stopped before it was finished. Try again.")
  return final
}

// ------------------------------------------------
// Scenarios from ordinary language
// ------------------------------------------------

export type ProposedAction = { type: string } & Record<string, unknown>

export type Proposal = {
  proposal: { title: string; notes: string; actions: ProposedAction[] }
  summary: string[]
  mode: "ai" | "rules"
  model: string | null
  notice: string | null
  // The model's description of what will be simulated (checked like answers)
  explanation?: string | null
}

export type ScenarioResult = {
  simulation_id: string
  title: string
  applied: string[]
  impact: {
    provenance: "calculated"
    method: string
    roads_closed: number
    trips_longer_percent: number
    mean_extra_seconds: number
    trips_cut_off: number
  } | null
}

export const SCENARIO_EXAMPLES = [
  "Simulate a major flood in Norwich city centre.",
  "Create a rush-hour traffic surge.",
  "Close a major bridge and measure the consequences.",
  "Simulate multiple simultaneous emergency incidents.",
]

export function planScenario(text: string) {
  return post<Proposal>("/intelligence/scenario/plan", { text })
}

// Runs only what the visitor confirmed: the proposal as shown
export function runScenario(proposal: Proposal["proposal"]) {
  return post<ScenarioResult>("/intelligence/scenario/run", { proposal, confirm: true })
}
