// The planning view's logic (GET /optimiser/plan, backend
// optimiser/planning.py): response-time bands, the map's features, the
// optimisation's steps and the comparison of layouts. No React or MapLibre
// here, so it can be tested on its own (tests/planning.test.ts).
//
// Every value drawn comes from the backend's calculation: the road-network
// response time of each junction from its nearest station, grouped into
// hexagons (the median of their junctions). Nothing is interpolated into
// places without roads, and no straight-line distances are used.

import type { Feature, FeatureCollection } from "./mapData.ts"

export type ServiceName = "ambulance" | "fire" | "police"
export type Disruption = "none" | "flood" | "bridges"
export type View = "existing" | "optimised" | "difference"

type Point = { latitude: number; longitude: number }

export type Summary = {
  junctions: number
  unreachable_junctions: number
  mean_seconds: number | null
  median_seconds: number | null
  p90_seconds: number | null
  worst_seconds: number | null
  junctions_within_target_percent: number
  population_within_target_percent: number
  population_beyond_target: number
}

export type Stage = {
  label: string   // "existing" or "optimised:<n stations>"
  summary: Summary
  cells: { seconds: (number | null)[]; within: number[]; beyond_people: number[] }
}

export type Plan = {
  service: ServiceName
  speed: number
  target_seconds: number
  disruption: Disruption
  closed_roads: number
  model: string
  hex_metres: number
  existing: (Point & { name: string; osm: string; snapped: Point; snap_metres: number })[]
  existing_left_out: { name: string; reason: string }[]
  candidates: (Point & { rank: number; average_seconds: number | null; within_target_percent: number })[]
  search: { candidates: number; possible_sets: number; method: string }
  optimised: (Point & { order: number; candidate: number; average_seconds: number | null;
    worst_seconds: number | null; within_target_percent: number })[]
  cells: { polygon: [number, number][]; junctions: number; population: number }[]
  stages: Stage[]
}

export type PlanSettings = {
  service: ServiceName
  stations: number
  targetMinutes: number
  disruption: Disruption
}

export const DEFAULT_PLAN: PlanSettings = { service: "fire", stations: 3, targetMinutes: 4, disruption: "none" }

export function planQuery(settings: PlanSettings): string {
  return `/optimiser/plan?service=${settings.service}&stations=${settings.stations}` +
    `&target_seconds=${settings.targetMinutes * 60}&disruption=${settings.disruption}`
}

// ------------------------------------------------
// Colours
// ------------------------------------------------

export const SERVICE_COLOURS: Record<ServiceName, string> = {
  ambulance: "#e5534b",
  fire: "#e8833a",
  police: "#4f8fd1",
}
export const PROPOSED_COLOUR = "#3fd1b0"
export const CANDIDATE_COLOUR = "#8a99ab"
export const HIGHLIGHT_COLOUR = "#f2c94c"

// Response time against the target, in four bands, plus "not reachable"
export type Band = "fast" | "within" | "near" | "slow" | "unreachable"

export const BANDS: { band: Band; colour: string; label: (target: number) => string }[] = [
  { band: "fast", colour: "#1a9e77", label: (t) => `Within ${minutes(t / 2)}` },
  { band: "within", colour: "#8fd16a", label: (t) => `${minutes(t / 2)} to ${minutes(t)} (target)` },
  { band: "near", colour: "#f0b44c", label: (t) => `${minutes(t)} to ${minutes(t * 1.5)}` },
  { band: "slow", colour: "#e0533d", label: (t) => `Over ${minutes(t * 1.5)}` },
  { band: "unreachable", colour: "#4a5566", label: () => "No road route" },
]

export function minutes(seconds: number): string {
  const value = Math.round((seconds / 60) * 10) / 10
  return Number.isInteger(value) ? `${value} min` : `${value.toFixed(1)} min`
}

export function bandOf(seconds: number | null, target: number): Band {
  if (seconds === null) return "unreachable"
  if (seconds <= target / 2) return "fast"
  if (seconds <= target) return "within"
  if (seconds <= target * 1.5) return "near"
  return "slow"
}

const bandColour = (band: Band) => BANDS.find((b) => b.band === band)!.colour

// Difference: seconds saved by the optimised layout (blue) or lost (red)
export function differenceColour(saved: number | null): string {
  if (saved === null) return "#4a5566"
  if (saved >= 60) return "#2f7fd1"
  if (saved >= 15) return "#7fb3e6"
  if (saved > -15) return "#56606d"
  return "#e0533d"
}

// Metres of column per second of response time (480 s: 240 m)
export const METRES_PER_SECOND = 0.5
// Each hexagon is drawn a little smaller than its cell, so the streets and
// buildings show between the columns (the values are the whole cell's)
export const HEX_DRAWN = 0.86
// Metres of column per second saved or lost, in the difference view
export const METRES_PER_SECOND_SAVED = 1.2

// ------------------------------------------------
// Stages and steps
// ------------------------------------------------

export function existingStage(plan: Plan): Stage | null {
  return plan.stages.find((stage) => stage.label === "existing") ?? null
}

// The optimised layout with its first `stations` stations
export function optimisedStage(plan: Plan, stations = plan.optimised.length): Stage {
  return plan.stages.find((stage) => stage.label === `optimised:${stations}`)!
}

// Values a fraction t of the way from one stage's cells to another's (the
// morph between layouts). A hexagon unreachable at either end takes the
// other end's value only at the end of the morph.
export function interpolateSeconds(from: (number | null)[], to: (number | null)[], t: number): (number | null)[] {
  return to.map((end, i) => {
    const start = from[i]
    if (start === null || end === null) return t < 1 ? start : end
    return start + (end - start) * t
  })
}

export type Step = {
  key: string
  title: string
  detail: string
  // The coverage drawn: the existing layout, or the optimised one with this many stations
  coverage: "existing" | number
  candidates: "none" | "all" | "top"
  proposed: number
}

// The optimisation as it ran (see backend planning.py): each step shows
// genuine output, in the order the algorithm produced it
export function optimisationSteps(plan: Plan): Step[] {
  const steps: Step[] = []
  const service = plan.service
  const existing = existingStage(plan)
  if (existing) {
    steps.push({
      key: "existing", coverage: "existing", candidates: "none", proposed: 0,
      title: `Existing ${service} stations`,
      detail: `${plan.existing.length} stations from OpenStreetMap: ${existing.summary.population_within_target_percent.toFixed(1)}% of residents reached within ${minutes(plan.target_seconds)}.`,
    })
  }
  steps.push({
    key: "candidates", coverage: existing ? "existing" : 1, candidates: "all", proposed: 0,
    title: `${plan.search.candidates} candidate sites`,
    detail: "Each well-connected junction on a grid over the city is scored on its own against the same sampled emergencies: taller columns respond faster.",
  })
  steps.push({
    key: "promising", coverage: existing ? "existing" : 1, candidates: "top", proposed: 0,
    title: "The most promising sites",
    detail: `The five best single sites. The best set is not just the best singles: ${plan.search.method === "every possible set"
      ? `all ${plan.search.possible_sets.toLocaleString("en-GB")} possible sets of ${plan.optimised.length} were compared`
      : `${plan.search.possible_sets.toLocaleString("en-GB")} sets are too many to compare, so it searched with ${plan.search.method}`}.`,
  })
  for (const pick of plan.optimised) {
    const stage = optimisedStage(plan, pick.order)
    steps.push({
      key: `pick-${pick.order}`, coverage: pick.order, candidates: "none", proposed: pick.order,
      title: `Station ${pick.order} of ${plan.optimised.length}`,
      detail: `With ${pick.order} station${pick.order === 1 ? "" : "s"}: ${stage.summary.population_within_target_percent.toFixed(1)}% of residents within ${minutes(plan.target_seconds)}, mean ${minutes(Math.round(stage.summary.mean_seconds ?? 0))}.`,
    })
  }
  return steps
}

// ------------------------------------------------
// Map features
// ------------------------------------------------

function polygonFeature(ring: [number, number][], properties: Feature["properties"]): Feature {
  return { type: "Feature", geometry: { type: "Polygon", coordinates: [ring] }, properties }
}

// A closed ring scaled about the middle of its corners
export function shrink(ring: [number, number][], scale: number): [number, number][] {
  const corners = ring.slice(0, -1)
  const lon = corners.reduce((sum, p) => sum + p[0], 0) / corners.length
  const lat = corners.reduce((sum, p) => sum + p[1], 0) / corners.length
  return ring.map(([x, y]) => [lon + (x - lon) * scale, lat + (y - lat) * scale])
}

// One hexagon per feature: its response time, band, colour and column height
export function coverageFeatures(plan: Plan, seconds: (number | null)[], beyond: number[]): FeatureCollection {
  return {
    type: "FeatureCollection",
    features: plan.cells.map((cell, i) => {
      const value = seconds[i]
      const band = bandOf(value, plan.target_seconds)
      return polygonFeature(shrink(cell.polygon, HEX_DRAWN), {
        cell: i,
        seconds: value === null ? -1 : Math.round(value),
        band,
        colour: bandColour(band),
        height: value === null ? 4 : Math.max(4, value * METRES_PER_SECOND),
        population: cell.population,
        beyond: beyond[i] ?? 0,
        // Residents beyond the target here: an underserved area
        underserved: (beyond[i] ?? 0) >= 50,
      })
    }),
  }
}

// Seconds saved per hexagon by the optimised layout against the existing one
export function differenceFeatures(plan: Plan): FeatureCollection | null {
  const existing = existingStage(plan)
  if (!existing) return null
  const optimised = optimisedStage(plan)
  return {
    type: "FeatureCollection",
    features: plan.cells.map((cell, i) => {
      const before = existing.cells.seconds[i]
      const after = optimised.cells.seconds[i]
      const saved = before === null || after === null ? null : before - after
      return polygonFeature(shrink(cell.polygon, HEX_DRAWN), {
        cell: i,
        saved: saved === null ? 0 : Math.round(saved),
        colour: differenceColour(saved),
        height: saved === null ? 4 : Math.max(4, Math.abs(saved) * METRES_PER_SECOND_SAVED),
        band: saved === null ? "unreachable" : saved > 0 ? "faster" : "slower",
        population: cell.population,
        beyond: 0,
        underserved: false,
      })
    }),
  }
}

const METRES_PER_DEGREE = 111_320

// A square footprint `size` metres across, centred on a point (a station column)
export function footprint(point: Point, size: number): [number, number][] {
  const half = size / 2
  const dLat = half / METRES_PER_DEGREE
  const dLon = half / (METRES_PER_DEGREE * Math.cos((point.latitude * Math.PI) / 180))
  const { latitude: lat, longitude: lon } = point
  return [[lon - dLon, lat - dLat], [lon + dLon, lat - dLat], [lon + dLon, lat + dLat], [lon - dLon, lat + dLat], [lon - dLon, lat - dLat]]
}

// Station columns: existing (service colour), proposed (teal) and
// candidates (grey, taller the faster they respond on their own; gold for
// the five best)
export function stationFeatures(plan: Plan, step: Pick<Step, "candidates" | "proposed">, showExisting: boolean): {
  columns: FeatureCollection
  labels: FeatureCollection
} {
  const columns: Feature[] = []
  const labels: Feature[] = []
  const label = (point: Point, text: string, kind: string) => labels.push({
    type: "Feature", geometry: { type: "Point", coordinates: [point.longitude, point.latitude] },
    properties: { text, kind },
  })
  if (showExisting) {
    for (const station of plan.existing) {
      columns.push(polygonFeature(footprint(station.snapped, 70), {
        kind: "existing", colour: SERVICE_COLOURS[plan.service], height: 260, base: 0,
      }))
      label(station.snapped, station.name, "existing")
    }
  }
  if (step.candidates !== "none") {
    const n = plan.candidates.length
    for (const candidate of plan.candidates) {
      const top = candidate.rank <= 5
      if (step.candidates === "top" && !top) continue
      columns.push(polygonFeature(footprint(candidate, 45), {
        kind: "candidate",
        colour: step.candidates === "top" ? HIGHLIGHT_COLOUR : CANDIDATE_COLOUR,
        height: 40 + 200 * (1 - (candidate.rank - 1) / Math.max(1, n - 1)),
        base: 0,
      }))
      if (step.candidates === "top") label(candidate, `#${candidate.rank}`, "candidate")
    }
  }
  for (const pick of plan.optimised.slice(0, step.proposed)) {
    columns.push(polygonFeature(footprint(pick, 70), { kind: "proposed", colour: PROPOSED_COLOUR, height: 300, base: 0 }))
    label(pick, `Proposed ${pick.order}`, "proposed")
  }
  return {
    columns: { type: "FeatureCollection", features: columns },
    labels: { type: "FeatureCollection", features: labels },
  }
}

// ------------------------------------------------
// Comparison
// ------------------------------------------------

export type Comparison = {
  measure: string
  existing: string
  optimised: string
  better: "optimised" | "existing" | "same" | null
}

export function compare(plan: Plan): Comparison[] {
  const before = existingStage(plan)?.summary ?? null
  const after = optimisedStage(plan).summary
  const seconds = (value: number | null | undefined) =>
    value === null || value === undefined ? "-" : minutes(Math.round(value))
  const row = (
    measure: string,
    pick: (s: Summary) => number | null,
    show: (v: number | null) => string,
    higherIsBetter: boolean,
  ): Comparison => {
    const a = before ? pick(before) : null
    const b = pick(after)
    let better: Comparison["better"] = null
    if (a !== null && b !== null) {
      better = Math.abs(a - b) < 1e-9 ? "same" : (b > a) === higherIsBetter ? "optimised" : "existing"
    }
    return { measure, existing: before ? show(a) : "-", optimised: show(b), better }
  }
  const percent = (v: number | null) => (v === null ? "-" : `${v.toFixed(1)}%`)
  const count = (v: number | null) => (v === null ? "-" : Math.round(v).toLocaleString("en-GB"))
  return [
    row(`Residents within ${minutes(plan.target_seconds)}`, (s) => s.population_within_target_percent, percent, true),
    row("Residents beyond the target", (s) => s.population_beyond_target, count, false),
    row("Mean response", (s) => s.mean_seconds, seconds, false),
    row("90th percentile", (s) => s.p90_seconds, seconds, false),
    row("Worst response", (s) => s.worst_seconds, seconds, false),
    row("Junctions with no route", (s) => s.unreachable_junctions, count, false),
  ]
}
