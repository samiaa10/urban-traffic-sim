// Saved runs and studies: types and the plain logic the History section
// uses (tests/history.test.ts). Requests go through scenarios.ts.

import type { ApiPoint } from "./simulationHelpers.ts"

// One saved simulation run (GET /simulations)
export type SavedRun = {
  simulation_id: string
  status: "active" | "stopped" | "replaced" | "interrupted"
  live: boolean
  is_default: boolean
  created_at: string
  ended_at: string | null
  start: number
  destination: number
  simulation_time: number
  vehicles: number
  restarted_from: string | null
}

// A run's saved history in figures (GET /simulations/{id}/history)
export type RunHistory = {
  simulation_id: string
  status: SavedRun["status"]
  counts: {
    closures: number
    accidents: number
    emergencies: number
    emergencies_arrived: number
    stations: number
    fleets: number
    events: number
  }
  mean_response_seconds: number | null
  restarted_from: string | null
}

// One study in the list (GET /scenarios/studies)
export type StudySummary = {
  study_id: string
  name: string
  status: "queued" | "running" | "completed" | "cancelled" | "interrupted"
  scenario_count: number
  completed: number
  failed: number
  created_at: string
}

// What POST /simulations/{id}/restart answers
export type RestartedRun = {
  simulation_id: string
  restarted_from: string
  distance_metres: number
  nodes_explored: number
  vehicle_position: ApiPoint
  route: ApiPoint[]
  applied: {
    stations: number
    ambulances: number
    closures: number
    accidents: number
    fleet_vehicles: number
  }
}

// A live run is still going; any other saved run can be restarted
export const canRestart = (run: SavedRun) => !run.live

// Studies a restart interrupted, or that were cancelled, can run again
export const canResume = (study: StudySummary) =>
  study.status === "interrupted" || study.status === "cancelled"

export function formatWhen(iso: string): string {
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return iso
  return when.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  })
}

export function runStatusLabel(run: SavedRun): string {
  if (run.live) return run.is_default ? "running (current)" : "running"
  return run.status
}

export function restartSummary(restarted: RestartedRun): string {
  const { applied } = restarted
  const parts = [
    applied.stations && `${applied.stations} station${applied.stations === 1 ? "" : "s"}`,
    applied.closures && `${applied.closures} closure${applied.closures === 1 ? "" : "s"}`,
    applied.accidents && `${applied.accidents} accident${applied.accidents === 1 ? "" : "s"}`,
    applied.fleet_vehicles && `${applied.fleet_vehicles} fleet vehicles`,
  ].filter(Boolean)

  return (
    "Restarted from a saved run with the same route" +
    (parts.length ? `, ${parts.join(", ")}` : "") +
    ". Vehicles start again from the beginning."
  )
}
