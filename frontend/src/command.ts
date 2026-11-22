// The emergency response command centre's logic (backend
// simulation/command.py, GET /simulations/{id}/command): incident kinds,
// the map's features, replay from recorded tracks, and the figures. No
// React or MapLibre here, so it can be tested on its own
// (tests/command.test.ts).
//
// Everything drawn is the backend's: units where the simulation has them,
// their planned routes, incidents and their timelines. Replay draws the
// positions the backend recorded each simulated second, joined by straight
// lines between consecutive samples (at most one second apart while a unit
// moves).

import {
  SERVICE_MARKERS,
  congestionFeatures,
  floodFeatures,
  vehicleFeatures,
  type Feature,
  type FeatureCollection,
  type MapState,
  type SourceId,
} from "./mapData.ts"
import type { FloodInfo } from "./live.ts"
import type { ServiceName } from "./services.ts"
import type { ApiPoint, Vehicle } from "./simulationHelpers.ts"

export type Kind = "medical" | "fire" | "crime" | "road_accident" | "flood" | "road_closure"

// What each kind sends (backend RESPONSE_PLANS) and how it is drawn
export const KINDS: { kind: Kind; label: string; colour: string; services: ServiceName[]; effect: string }[] = [
  { kind: "medical", label: "Medical", colour: "#e5534b", services: ["ambulance"], effect: "" },
  { kind: "fire", label: "Fire", colour: "#f08a3c", services: ["fire", "ambulance"], effect: "" },
  { kind: "crime", label: "Crime", colour: "#5b8def", services: ["police"], effect: "" },
  { kind: "road_accident", label: "Road accident", colour: "#f2c94c", services: ["ambulance", "police"], effect: "a severe accident on the nearest road" },
  { kind: "flood", label: "Flood", colour: "#3fb8d8", services: ["fire"], effect: "every road within 200 m closed for 20 min" },
  { kind: "road_closure", label: "Road closure", colour: "#b88cf0", services: [], effect: "the nearest road closed until you reopen it" },
]

export const kindOf = (kind: string) => KINDS.find((k) => k.kind === kind) ?? KINDS[0]

export const SERVICE_LABELS: Record<ServiceName, string> = { ambulance: "Ambulance", fire: "Fire engine", police: "Police car" }

export type TimelineEvent = {
  time: number
  event: "reported" | "road" | "refused" | "queued" | "dispatched" | "arrived" | "cleared" | "road_cleared" | "resolved"
  label: string
  service?: ServiceName
  vehicle_id?: number
  response_seconds?: number
  within_target?: boolean
}

export type IncidentStatus = "queued" | "en_route" | "on_scene" | "active" | "unanswered" | "resolved"

export type CommandIncident = {
  id: number
  kind: Kind
  label: string
  latitude: number
  longitude: number
  status: IncidentStatus
  created_at: number
  resolved_at: number | null
  services: ServiceName[]
  on_scene_seconds: number
  units: (number | null)[]
  road: ApiPoint[] | null
  flood_roads: number
  target: ApiPoint
  timeline: TimelineEvent[]
}

export type UnitState = "available" | "en_route" | "blocked" | "on_scene" | "returning"

export type Unit = {
  vehicle_id: number
  service: ServiceName
  station: string
  station_id: number
  state: UnitState
  incident_id: number | null
  latitude: number
  longitude: number
  eta_seconds: number | null
  reroutes: number
  route: ApiPoint[]
}

export type CommandStation = {
  station_id: number
  name: string
  source: string
  service: ServiceName
  latitude: number
  longitude: number
}

export type Metrics = {
  responses: number
  mean_response_seconds: number | null
  worst_response_seconds: number | null
  within_target_percent: number | null
  units_total: number
  units_available: number
  units_en_route: number
  units_on_scene: number
  units_returning: number
  incidents_open: number
  incidents_resolved: number
  queued_units: number
  by_service: Record<ServiceName, { responses: number; mean_seconds: number | null; target_seconds: number; available: number; units: number }>
}

export type CommandState = {
  simulation_time: number
  incidents: CommandIncident[]
  units: Unit[]
  stations: CommandStation[]
  metrics: Metrics
}

// [simulated second, latitude, longitude]
export type Sample = [number, number, number]
export type Track = { vehicle_id: number; service: ServiceName; samples: Sample[] }
export type Replay = { simulation_time: number; tracks: Track[]; incidents: CommandIncident[] }

// ------------------------------------------------
// Words and numbers
// ------------------------------------------------

export function clock(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "-"
  const whole = Math.max(0, Math.round(seconds))
  const hours = Math.floor(whole / 3600)
  const minutes = Math.floor((whole % 3600) / 60)
  const rest = String(whole % 60).padStart(2, "0")
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`
}

export const STATUS_LABELS: Record<IncidentStatus, string> = {
  queued: "Waiting for a unit",
  en_route: "Units en route",
  on_scene: "On scene",
  active: "Active",
  unanswered: "No unit can reach it",
  resolved: "Resolved",
}

export const UNIT_LABELS: Record<UnitState, string> = {
  available: "Available",
  en_route: "En route",
  blocked: "Blocked",
  on_scene: "On scene",
  returning: "Returning",
}

export type Tile = { label: string; value: string; detail: string; tone: "good" | "warn" | "bad" | "plain" }

export function metricTiles(metrics: Metrics): Tile[] {
  const within = metrics.within_target_percent
  return [
    {
      label: "Average response",
      value: clock(metrics.mean_response_seconds),
      detail: `${metrics.responses} unit${metrics.responses === 1 ? "" : "s"} arrived`,
      tone: "plain",
    },
    {
      label: "Worst response",
      value: clock(metrics.worst_response_seconds),
      detail: "from report to arrival",
      tone: "plain",
    },
    {
      label: "Within target",
      value: within === null ? "-" : `${within.toFixed(0)}%`,
      detail: "targets 7 / 8 / 15 min",
      tone: within === null ? "plain" : within >= 90 ? "good" : within >= 70 ? "warn" : "bad",
    },
    {
      label: "Units available",
      value: `${metrics.units_available}/${metrics.units_total}`,
      detail: `${metrics.units_en_route} en route, ${metrics.units_on_scene} on scene`,
      tone: metrics.units_available === 0 && metrics.units_total > 0 ? "bad" : metrics.units_available <= metrics.units_total / 3 ? "warn" : "good",
    },
    {
      label: "Open incidents",
      value: String(metrics.incidents_open),
      detail: metrics.queued_units ? `${metrics.queued_units} waiting for a unit` : `${metrics.incidents_resolved} resolved`,
      tone: metrics.queued_units ? "bad" : "plain",
    },
  ]
}

// Units grouped by service, in a fixed order
export function unitsByService(units: Unit[]): [ServiceName, Unit[]][] {
  return (["ambulance", "fire", "police"] as ServiceName[])
    .map((service) => [service, units.filter((u) => u.service === service)] as [ServiceName, Unit[]])
    .filter(([, list]) => list.length > 0)
}

// Whether the operator can close an incident now (the backend refuses
// while a unit is still on its way or waiting)
export function canResolve(incident: CommandIncident): boolean {
  return incident.status === "on_scene" || incident.status === "active"
}

// ------------------------------------------------
// Replay
// ------------------------------------------------

// Where a track has the unit at time t: interpolated between the two
// recorded samples around it; null before the first or after the last
export function positionAt(samples: Sample[], t: number): [number, number] | null {
  if (samples.length === 0 || t < samples[0][0] || t > samples[samples.length - 1][0]) return null
  let low = 0
  let high = samples.length - 1
  while (high - low > 1) {
    const middle = (low + high) >> 1
    if (samples[middle][0] <= t) low = middle
    else high = middle
  }
  const [t0, lat0, lon0] = samples[low]
  const [t1, lat1, lon1] = samples[high]
  if (t1 === t0) return [lat0, lon0]
  const f = Math.min(1, Math.max(0, (t - t0) / (t1 - t0)))
  return [lat0 + (lat1 - lat0) * f, lon0 + (lon1 - lon0) * f]
}

// The span replay covers: from the first incident or sample to the time
// of the recording
export function replaySpan(replay: Replay): [number, number] {
  const starts = [
    ...replay.incidents.map((i) => i.created_at),
    ...replay.tracks.filter((t) => t.samples.length).map((t) => t.samples[0][0]),
  ]
  const start = starts.length ? Math.min(...starts) : replay.simulation_time
  return [start, Math.max(start, replay.simulation_time)]
}

// The units out at time t, as vehicles for the map (heading from the
// direction of travel between samples)
export function replayVehicles(replay: Replay, t: number): Vehicle[] {
  const vehicles: Vehicle[] = []
  for (const track of replay.tracks) {
    const here = positionAt(track.samples, t)
    if (!here) continue
    const ahead = positionAt(track.samples, Math.min(t + 1, track.samples[track.samples.length - 1][0]))
    const heading = ahead && (ahead[0] !== here[0] || ahead[1] !== here[1])
      ? (Math.atan2((ahead[1] - here[1]) * Math.cos((here[0] * Math.PI) / 180), ahead[0] - here[0]) * 180) / Math.PI
      : 0
    vehicles.push({
      vehicle_id: track.vehicle_id,
      latitude: here[0],
      longitude: here[1],
      distance_travelled: 0,
      finished: false,
      vehicle_type: track.service,
      on_emergency: true,
      heading: (heading + 360) % 360,
    })
  }
  return vehicles
}

// What had happened to an incident by time t (null: not reported yet)
export function statusAt(incident: CommandIncident, t: number): IncidentStatus | null {
  if (t < incident.created_at) return null
  if (incident.resolved_at !== null && t >= incident.resolved_at) return "resolved"
  const seen = incident.timeline.filter((e) => e.time <= t)
  const count = (event: TimelineEvent["event"]) => seen.filter((e) => e.event === event).length
  const asked = incident.units.length
  if (asked > 0 && count("dispatched") < asked) return "queued"
  if (count("arrived") < count("dispatched")) return "en_route"
  if (count("cleared") < count("arrived")) return "on_scene"
  return incident.status === "unanswered" ? "unanswered" : "active"
}

// ------------------------------------------------
// The map
// ------------------------------------------------

const collection = (features: Feature[]): FeatureCollection => ({ type: "FeatureCollection", features })
const EMPTY = collection([])
const lonLat = (point: ApiPoint): [number, number] => [point.longitude, point.latitude]

export type CommandView = {
  state: CommandState | null
  vehicles: Vehicle[]
  congestion: MapState["congestion"]
  floods: FloodInfo[]
  // Replay: the recording and the time shown (null: live)
  replay?: { data: Replay; time: number } | null
  selected?: number | null
}

export function commandMapData(view: CommandView): Record<SourceId, FeatureCollection> {
  const { state, replay } = view
  const incidents = replay ? replay.data.incidents : state?.incidents ?? []
  const shownIncidents = incidents
    .map((incident) => ({ incident, status: replay ? statusAt(incident, replay.time) : incident.status }))
    .filter(({ status }) => status !== null && status !== "resolved")

  const points = shownIncidents.map(({ incident, status }) => ({
    type: "Feature" as const,
    geometry: { type: "Point" as const, coordinates: [incident.longitude, incident.latitude] as [number, number] },
    properties: {
      kind: "incident",
      colour: kindOf(incident.kind).colour,
      label: `#${incident.id} ${kindOf(incident.kind).label}`,
      incident: incident.id,
      status: status ?? "",
      selected: incident.id === view.selected,
    },
  }))

  const closures = shownIncidents
    .filter(({ incident }) => incident.road && incident.road.length === 2)
    .map(({ incident }) => ({
      type: "Feature" as const,
      geometry: { type: "LineString" as const, coordinates: incident.road!.map(lonLat) },
      properties: { kind: incident.kind === "road_closure" ? "command" : "scenario", incident: incident.id },
    }))

  // Live: each unit on a call's remaining planned route
  const routes = replay ? [] : (state?.units ?? [])
    .filter((unit) => (unit.state === "en_route" || unit.state === "blocked") && unit.route.length > 1)
    .map((unit) => ({
      type: "Feature" as const,
      geometry: { type: "LineString" as const, coordinates: [[unit.longitude, unit.latitude] as [number, number], ...unit.route.slice(1).map(lonLat)] },
      properties: { kind: "emergency", colour: SERVICE_MARKERS[unit.service].colour, unit: unit.vehicle_id },
    }))

  const stations = (state?.stations ?? []).map((station) => {
    const units = (state?.units ?? []).filter((u) => u.station_id === station.station_id)
    const free = units.filter((u) => u.state === "available").length
    return {
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [station.longitude, station.latitude] as [number, number] },
      properties: {
        kind: "station",
        label: SERVICE_MARKERS[station.service].letter,
        colour: SERVICE_MARKERS[station.service].colour,
        popup: [station.name, `${free} of ${units.length} units at the station`,
          station.source === "optimiser" ? "Standby point chosen by the optimiser" : "Location from OpenStreetMap"].join("\n"),
      },
    }
  })

  // Live: the simulation's vehicles; units at a scene are drawn there too
  // (the simulation counts them as finished, which the map would hide)
  const onScene = new Set((state?.units ?? []).filter((u) => u.state === "on_scene").map((u) => u.vehicle_id))
  const vehicles = replay
    ? replayVehicles(replay.data, replay.time)
    : view.vehicles.map((v) => (onScene.has(v.vehicle_id) ? { ...v, finished: false, on_emergency: true } : v))

  return {
    congestion: replay ? EMPTY : congestionFeatures(view.congestion),
    hotspots: EMPTY,
    closures: collection(closures),
    routes: collection(routes),
    points: collection(points),
    stations: collection(stations),
    vehicles: vehicleFeatures(vehicles),
    signals: EMPTY,
    floods: replay ? EMPTY : floodFeatures(view.floods),
  }
}
