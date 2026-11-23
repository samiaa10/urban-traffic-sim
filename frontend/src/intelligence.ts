// The AI City Analyst and scenario builder on the client (backend
// intelligence/: POST /intelligence/ask, /intelligence/scenario/plan and
// /run). No React here: tests/intelligence.test.ts.

import { API_URL } from "./config.ts"

export type Source = { tool: string; input: Record<string, unknown>; provenance: "calculated"; result: Record<string, unknown> }

export type Answer = {
  answer: string
  mode: "ai" | "rules"
  model: string | null
  sources: Source[]
  notice: string | null
}

export type Status = {
  provider: string
  model: string | null
  available: boolean
  reason: string | null
  fallback: string
  capabilities: { name: string; description: string }[]
}

export const EXAMPLES = [
  "Which areas have the worst emergency response coverage?",
  "Where are the biggest traffic bottlenecks?",
  "What would happen if the central bridges closed?",
  "Which emergency station locations would improve fire coverage?",
  "Why did this simulation perform poorly?",
  "Which scenario produced the best results?",
]

export const TOOL_LABELS: Record<string, string> = {
  city_overview: "City model figures",
  coverage_gaps: "Emergency coverage gaps",
  recommend_station: "Best extra station",
  critical_roads: "Roads trips depend on",
  closure_impact: "Road closure impact",
  live_bottlenecks: "Live bottlenecks",
  run_report: "Simulation report",
  scenario_results: "Scenario study ranking",
}

// What an answer is: who wrote it, from what
export function provenanceOf(answer: Answer): { label: string; detail: string; tone: "ai" | "calc" } {
  const count = answer.sources.length
  const calculations = `${count} calculation${count === 1 ? "" : "s"}`
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
