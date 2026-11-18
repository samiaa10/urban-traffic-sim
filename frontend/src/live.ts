// The live simulation: speed, the requests the live controls send, and the
// time series the live charts draw (from the summary every step returns).
// Plain logic, tested in tests/live.test.ts.

import { CITY_CENTRE } from "./scenarios.ts"

// Simulated seconds per polling tick (one tick a second)
export const SPEEDS = [1, 5, 10] as const
export type Speed = (typeof SPEEDS)[number]

export const stepQuery = (speed: number) => (speed > 1 ? `?seconds=${speed}` : "")

// What /simulation/step returns besides the vehicles
export type StepSummary = {
  active_vehicles: number
  held_up: number
  mean_speed_factor: number | null
  arrived: number
  mean_journey_seconds: number | null
  reroutes: number
  closed_roads: number
  accidents: number
  // Only from the cellular-automaton traffic model: cars standing still
  stopped?: number
}

export type Sample = StepSummary & { time: number }

// Keep the last hour of simulated time at most
export const MAX_SAMPLES = 3600

export function appendSample(history: Sample[], sample: Sample): Sample[] {
  // A new run starts again from 0: start a new series
  if (history.length > 0 && sample.time <= history[history.length - 1].time) {
    return [sample]
  }
  const next = [...history, sample]
  return next.length > MAX_SAMPLES ? next.slice(next.length - MAX_SAMPLES) : next
}

// Points for a chart: x = simulated time, y = the chosen value
export function series(history: Sample[], value: (sample: Sample) => number | null) {
  return history
    .map((sample) => ({ x: sample.time, y: value(sample) }))
    .filter((point): point is { x: number; y: number } => point.y !== null)
}

export const heldUpPercent = (sample: Sample) =>
  sample.active_vehicles > 0 ? (100 * sample.held_up) / sample.active_vehicles : null

export const stoppedPercent = (sample: Sample) =>
  sample.stopped === undefined || sample.active_vehicles === 0
    ? null
    : (100 * sample.stopped) / sample.active_vehicles

export const speedPercent = (sample: Sample) =>
  sample.mean_speed_factor === null ? null : 100 * sample.mean_speed_factor

// ------------------------------------------------
// The live controls' requests: [original path, per-run path]
// ------------------------------------------------

export type Severity = "minor" | "moderate" | "severe"

export const FLEET_SIZES = [100, 500, 1000, 2000]

export function fleetRequest(count: number, seed: number): [string, string] {
  const query = `count=${count}&seed=${seed}`
  return [`/simulation/fleet?${query}`, `/fleet?${query}`]
}

// A rush hour into the city centre: 60 trips a minute for 15 minutes
export function surgeRequest(direction: "inbound" | "outbound" = "inbound"): [string, string] {
  const query =
    `latitude=${CITY_CENTRE.latitude}&longitude=${CITY_CENTRE.longitude}` +
    `&radius_metres=800&direction=${direction}&vehicles_per_minute=60&duration_seconds=900`
  return [`/traffic/surge?${query}`, `/traffic/surge?${query}`]
}

export function accidentRequest(
  start: number,
  destination: number,
  severity: Severity,
  minutes: number,
): [string, string] {
  const query = `start=${start}&destination=${destination}&severity=${severity}&duration_seconds=${minutes * 60}`
  return [`/incident/accident?${query}`, `/incidents/accident?${query}`]
}

export function closureRequest(start: number, destination: number, open: boolean): [string, string] {
  const query = `start=${start}&destination=${destination}`
  return open
    ? [`/incident/unblock?${query}`, `/incidents/unblock?${query}`]
    : [`/incident/block?${query}`, `/incidents/block?${query}`]
}

export function clearAccidentRequest(start: number, destination: number): [string, string] {
  const query = `start=${start}&destination=${destination}`
  return [`/incident/accident/clear?${query}`, `/incidents/accident/clear?${query}`]
}

export function reroutingRequest(enabled: boolean): [string, string] {
  return [`/traffic/rerouting?enabled=${enabled}`, `/traffic/rerouting?enabled=${enabled}`]
}

// One line about the latest sample, for the analytics header
export function describeSample(sample: Sample | undefined): string {
  if (!sample) return "No data yet: start a route."
  const speed = sample.mean_speed_factor === null ? "-" : `${Math.round(100 * sample.mean_speed_factor)}%`
  return (
    `${sample.active_vehicles} driving, ${sample.held_up} held up, average speed ${speed} of free flow, ` +
    `${sample.arrived} arrived`
  )
}

// ------------------------------------------------
// Traffic signals (backend: simulation/signals.py)
// ------------------------------------------------

export type SignalController = "none" | "fixed" | "adaptive"

export type SignalSettings = {
  controller: SignalController
  priority: boolean
  green: number
  minGreen: number
  maxGreen: number
}

// The backend's experimental defaults (not Norwich's real timings)
export const DEFAULT_SIGNALS: SignalSettings = {
  controller: "none", priority: false, green: 20, minGreen: 7, maxGreen: 45,
}

export const SIGNAL_CONTROLLERS: { value: SignalController; label: string }[] = [
  { value: "none", label: "Off" },
  { value: "fixed", label: "Fixed time" },
  { value: "adaptive", label: "Adaptive" },
]

export function signalsRequest(settings: SignalSettings): [string, string] {
  const query =
    `controller=${settings.controller}&emergency_priority=${settings.priority}` +
    `&green=${settings.green}&min_green=${settings.minGreen}&max_green=${settings.maxGreen}`
  return [`/traffic/signals?${query}`, `/traffic/signals?${query}`]
}

export type SignalLight = "green" | "amber" | "red"

// One junction as GET /traffic/signals describes it
export type SignalJunction = {
  node: number
  latitude: number
  longitude: number
  phase: number
  stage: "green" | "amber" | "all_red"
  approaches: { bearing: number; light: SignalLight; queue: number }[]
}

// Where to draw an approach's light: a little way up its road from the
// junction, so the approaches of one junction can be told apart
export function approachPoint(junction: SignalJunction, bearing: number, metres = 22): [number, number] {
  const radians = (bearing * Math.PI) / 180
  const north = (metres * Math.cos(radians)) / 111_320
  const east = (metres * Math.sin(radians)) / (111_320 * Math.cos((junction.latitude * Math.PI) / 180))
  return [junction.latitude + north, junction.longitude + east]
}

// Vehicles queued at all signals, and how many junctions have any
export function signalQueues(junctions: SignalJunction[]) {
  let queued = 0
  let busy = 0
  for (const junction of junctions) {
    const here = junction.approaches.reduce((total, approach) => total + approach.queue, 0)
    queued += here
    if (here > 0) busy += 1
  }
  return { queued, busy }
}
