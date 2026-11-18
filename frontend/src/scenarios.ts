// Scenario analysis: types, requests to /scenarios/..., and the plain
// functions the scenario screen uses. No React or Leaflet in here, so it
// can be tested on its own (tests/scenarios.test.ts).

import type { TrafficModel } from "./services.ts"
import { apiUrl } from "./optimiser.ts"

// How often a running study is checked (ms)
export const STUDY_POLL_MS = 2000

// ------------------------------------------------
// Types (the shapes the backend answers with)
// ------------------------------------------------

export type Road = { start: number; destination: number }

export type ScenarioSpec = {
  name: string
  seed: number
  demand: { vehicles: number; spread_seconds: number }
  rerouting: boolean
  // Not given: the server's default model
  traffic_model?: TrafficModel
  // Not given: no traffic signals
  signals?: { controller: "fixed" | "adaptive"; emergency_priority: boolean }
  // Disruptions beyond closures and accidents, and resilience measures
  floods?: {
    area: { latitude: number; longitude: number; radius_metres: number }
    severity: "minor" | "moderate" | "severe" | "closed"
    start_at: number
    duration_seconds: number
    recovery_seconds: number
  }[]
  failures?: { names: string[]; start_at: number; end_at: number }[]
  resilience?: boolean
  closures: Road[]
  accidents: (Road & {
    severity: "minor" | "moderate" | "severe"
    start_at: number
    duration_seconds: number | null
  })[]
  surges: {
    latitude: number
    longitude: number
    radius_metres: number
    direction: "inbound" | "outbound"
    vehicles_per_minute: number
    start_at: number
    duration_seconds: number
  }[]
  stations?: { latitude: number; longitude: number; ambulances: number }[]
  emergencies?: {
    count: number
    first_at: number
    interval_seconds: number
    target_seconds: number
  }
}

export type MetricDefinition = {
  key: string
  label: string
  unit: string
  better: "lower" | "higher"
  definition: string
}

export type Metrics = Record<string, number | null>

export type StudyScenario = {
  scenario_id: string
  position: number
  name: string
  is_baseline: boolean
  seed: number
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted"
  error: string | null
  metrics: Metrics | null
  wall_seconds: number | null
}

export type RankingRow = {
  rank: number
  id: string
  name: string
  value: number | null
  gap_to_best: number | null
  gap_percent: number | null
  metrics: Metrics
}

export type Study = {
  study_id: string
  name: string
  status: "queued" | "running" | "completed" | "cancelled" | "interrupted"
  metric: string
  scenario_count: number
  completed: number
  failed: number
  progress_percent: number
  workers: number
  wall_seconds: number | null
  scenarios: StudyScenario[]
  ranking_metric: MetricDefinition
  ranking: RankingRow[] | null
  explanation: string[]
}

export type RoadImpact = {
  rank: number
  start_node: number | null
  end_node: number | null
  start: { latitude: number; longitude: number }
  end: { latitude: number; longitude: number }
  delay_vehicle_seconds: number
  vehicle_seconds: number
  peak_vehicles: number
  delay_share: number
  // The road's name from OpenStreetMap, when the map knows it
  name?: string | null
}

export type TimeseriesRow = {
  time: number
  active_vehicles: number
  delay_vehicle_seconds: number
  mean_speed_factor: number | null
}

export type ScenarioDetail = {
  scenario_id: string
  name: string
  is_baseline: boolean
  status: StudyScenario["status"]
  spec: ScenarioSpec
  // The scenario's closures and accidents, with their coordinates
  disruptions: {
    kind: "closure" | "accident"
    start_node: number
    end_node: number
    start: { latitude: number; longitude: number }
    end: { latitude: number; longitude: number }
  }[]
  result: {
    summary: Record<string, number | boolean | null>
    metrics: Metrics
    roads: RoadImpact[]
    areas: { rank: number; south: number; west: number; north: number; east: number; delay_vehicle_seconds: number; roads_affected: number }[]
    timeseries: TimeseriesRow[]
    trip_counts: Record<string, number>
  } | null
  vs_baseline: {
    baseline_name: string
    paired: {
      trips_compared: number
      extra_vehicle_hours: number
      trips_slower: number
      trips_faster: number
      worst_increase_seconds: number | null
      newly_cut_off: number
    } | null
  } | null
}

export type NearestRoad = Road & {
  start_point: { latitude: number; longitude: number }
  end_point: { latitude: number; longitude: number }
  name?: string | null
}

// How a picked road is shown: its name, or its two junctions
export const pickedRoadLabel = (road: NearestRoad) =>
  road.name ? road.name : `${road.start}-${road.destination}`

// The metrics a study can be ranked by: the same keys as METRICS in
// backend/scenarios/compare.py (a backend test checks they exist there).
// Kept here so the page needs no request before the user runs anything.
export const METRIC_OPTIONS: Pick<MetricDefinition, "key" | "label" | "unit" | "better">[] = [
  { key: "total_delay_vehicle_hours", label: "Network delay", unit: "vehicle-hours", better: "lower" },
  { key: "total_travel_vehicle_hours", label: "Total travel time", unit: "vehicle-hours", better: "lower" },
  { key: "mean_increase_seconds", label: "Mean journey-time increase", unit: "s", better: "lower" },
  { key: "p95_increase_seconds", label: "95th percentile journey-time increase", unit: "s", better: "lower" },
  { key: "max_increase_seconds", label: "Worst journey-time increase", unit: "s", better: "lower" },
  { key: "completion_percent", label: "Trips completed", unit: "%", better: "higher" },
  { key: "emergency_mean_response_seconds", label: "Mean emergency response", unit: "s", better: "lower" },
  { key: "emergency_within_target_percent", label: "Emergencies reached within target", unit: "%", better: "higher" },
]

// ------------------------------------------------
// Requests
// ------------------------------------------------

export type Fetcher = (url: string, init?: RequestInit) => Promise<Response>

// The backend's explanation of a failure: a string for our own errors,
// a list of problems for an invalid study
export function describeDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail
  if (Array.isArray(detail) && detail.every((item) => typeof item === "string")) {
    return detail.join("; ")
  }
  return fallback
}

export async function request<T>(
  path: string,
  init?: RequestInit,
  fetcher: Fetcher = (url, options) => fetch(url, options)
): Promise<T> {
  const response = await fetcher(apiUrl(path), init)

  let body: unknown
  try {
    body = await response.json()
  } catch {
    throw new Error(response.ok ? "The server sent an unreadable answer" : "The request failed")
  }

  if (!response.ok) {
    throw new Error(
      describeDetail((body as { detail?: unknown } | null)?.detail, "The request failed")
    )
  }

  return body as T
}

export const postJson = <T>(path: string, body: unknown, fetcher?: Fetcher) =>
  request<T>(
    path,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    fetcher
  )

// ------------------------------------------------
// Building a study from the scenarios on screen
// ------------------------------------------------

// A scenario being edited (what the builder shows)
export type Draft = {
  key: number
  name: string
  vehicles: number
  seed: number
  rerouting: boolean
  // undefined: the server's default
  trafficModel?: TrafficModel
  // undefined: no signals
  signals?: StudySignals
  // Disruptions (illustrative: see RIVER_FLOOD and CENTRAL_BRIDGES)
  flood?: boolean
  bridgesOut?: boolean
  surge: boolean
  closures: (Road & { label: string })[]
  accidents: (Road & { label: string; severity: "minor" | "moderate" | "severe"; start_at: number })[]
}

export const CITY_CENTRE = { latitude: 52.6286, longitude: 1.2927 }

// A flood for the scenario builder: every road within 350 m of a point on
// the Wensum by Riverside closed from 5 minutes for 20, then 10 minutes to
// recover. An illustrative area chosen by hand, not a flood map.
export const RIVER_FLOOD = {
  area: { latitude: 52.6305, longitude: 1.3045, radius_metres: 350 },
  severity: "closed" as const,
  start_at: 300,
  duration_seconds: 1200,
  recovery_seconds: 600,
}

// Central river bridges (OpenStreetMap bridge=* roads), out of use from 5
// minutes for 20
export const CENTRAL_BRIDGES = ["Prince of Wales Road", "Whitefriars", "Fye Bridge Street", "Duke Street"]

// Traffic signals for a scenario, at the junctions OpenStreetMap shows with lights
export type StudySignals = "fixed" | "adaptive" | "priority"
export const STUDY_SIGNALS: { value: StudySignals; label: string }[] = [
  { value: "fixed", label: "Fixed time" },
  { value: "adaptive", label: "Adaptive" },
  { value: "priority", label: "Adaptive + emergency priority" },
]

export function draftToSpec(draft: Draft): ScenarioSpec {
  return {
    name: draft.name.trim() || "Scenario",
    seed: draft.seed,
    demand: { vehicles: draft.vehicles, spread_seconds: 300 },
    rerouting: draft.rerouting,
    ...(draft.trafficModel ? { traffic_model: draft.trafficModel } : {}),
    ...(draft.flood ? { floods: [RIVER_FLOOD] } : {}),
    ...(draft.bridgesOut
      ? { failures: [{ names: CENTRAL_BRIDGES, start_at: 300, end_at: 1500 }] }
      : {}),
    ...(draft.flood || draft.bridgesOut ? { resilience: true } : {}),
    ...(draft.signals
      ? {
          signals: {
            controller: draft.signals === "fixed" ? "fixed" : "adaptive",
            emergency_priority: draft.signals === "priority",
          },
        }
      : {}),
    closures: draft.closures.map(({ start, destination }) => ({ start, destination })),
    accidents: draft.accidents.map(({ start, destination, severity, start_at }) => ({
      start,
      destination,
      severity,
      start_at,
      duration_seconds: 900,
    })),
    surges: draft.surge
      ? [{
          ...CITY_CENTRE,
          radius_metres: 1000,
          direction: "inbound",
          vehicles_per_minute: 120,
          start_at: 0,
          duration_seconds: 900,
        }]
      : [],
  }
}

export function studyRequest(
  name: string,
  drafts: Draft[],
  baselineKey: number | null,
  metric: string
) {
  const baseline = drafts.findIndex((draft) => draft.key === baselineKey)

  return {
    name: name.trim() || "Study",
    scenarios: drafts.map(draftToSpec),
    baseline: baseline >= 0 ? baseline : null,
    metric,
  }
}

export function randomClosureRequest(
  base: Draft,
  count: number,
  closuresPerScenario: number,
  radiusMetres: number,
  metric: string
) {
  return {
    name: `Random closures (${count})`,
    base: draftToSpec({ ...base, closures: [], accidents: [] }),
    kind: "random_closures",
    count,
    closures_per_scenario: closuresPerScenario,
    zone: { ...CITY_CENTRE, radius_metres: radiusMetres },
    sweep_seed: base.seed,
    metric,
  }
}

// ------------------------------------------------
// Showing results
// ------------------------------------------------

export const isFinished = (study: Study) =>
  study.status !== "queued" && study.status !== "running"

export function formatValue(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined) return "-"
  // Two decimals below 10: scenarios often differ by hundredths
  if (unit === "vehicle-hours") return `${value.toFixed(Math.abs(value) < 10 ? 2 : 1)} veh-h`
  if (unit === "%") return `${value.toFixed(0)}%`
  if (unit === "s") return formatSeconds(value)
  return value.toLocaleString("en-GB", { maximumFractionDigits: 1 })
}

export function formatSeconds(seconds: number): string {
  const sign = seconds < 0 ? "-" : ""
  const whole = Math.round(Math.abs(seconds))
  if (whole < 60) return `${sign}${whole} s`
  const minutes = Math.floor(whole / 60)
  const rest = whole % 60
  return rest === 0 ? `${sign}${minutes} min` : `${sign}${minutes} min ${rest} s`
}

export function progressLabel(study: Study): string {
  const done = study.completed + study.failed
  const failed = study.failed ? `, ${study.failed} failed` : ""
  return `${done} of ${study.scenario_count} scenarios${failed}`
}

// Points of a line chart in a width x height box (0,0 at the top left)
export function linePoints(
  series: { x: number; y: number }[],
  width: number,
  height: number,
  maxX: number,
  maxY: number
): string {
  if (maxX <= 0 || maxY <= 0) return ""

  return series
    .map(({ x, y }) => `${((x / maxX) * width).toFixed(1)},${(height - (y / maxY) * height).toFixed(1)}`)
    .join(" ")
}

// Delay per simulated minute, in vehicle-minutes (what the chart shows)
export const delaySeries = (rows: TimeseriesRow[]) =>
  rows.map((row) => ({ x: row.time / 60, y: row.delay_vehicle_seconds / 60 }))

// Counts of values in `bins` equal-width bins between their min and max
export function histogram(values: number[], bins: number) {
  if (values.length === 0) return []

  const low = Math.min(...values)
  const high = Math.max(...values)
  const width = high > low ? (high - low) / bins : 1

  const counts = Array.from({ length: bins }, (_, index) => ({
    from: low + index * width,
    to: low + (index + 1) * width,
    count: 0,
  }))

  for (const value of values) {
    const index = high > low ? Math.min(bins - 1, Math.floor((value - low) / width)) : 0
    counts[index].count += 1
  }

  return counts
}

// "0.71-0.74": enough decimals that a bin's two ends differ
export function binLabel(bin: { from: number; to: number }): string {
  const width = bin.to - bin.from
  const digits = width > 0 ? Math.min(4, Math.max(1, Math.ceil(-Math.log10(width)))) : 1
  return `${bin.from.toFixed(digits)}-${bin.to.toFixed(digits)}`
}

export const roadLabel = (road: {
  start_node: number | null
  end_node: number | null
  name?: string | null
}) =>
  road.name
    ? road.name
    : road.start_node !== null && road.end_node !== null
      ? `${road.start_node}-${road.end_node}`
      : "part of a road (after a reroute)"
