// Turns what the app already knows (routes, vehicles, congestion, stations,
// scenario results) into GeoJSON for the 3D map. Nothing here is invented:
// every feature comes from state the 2D map draws too.
//
// The app stores points as [latitude, longitude] (Leaflet's order); GeoJSON
// wants [longitude, latitude], so every coordinate is flipped here.

import { congestionColour, type Coordinate, type Vehicle } from "./simulationHelpers.ts"
import { COLOURS, SIGNAL_LIGHTS } from "./mapStyles.ts"
import { approachPoint, type FloodInfo, type SignalJunction } from "./live.ts"
import { minutes, percent } from "./optimiser.ts"

type ApiPoint = { latitude: number; longitude: number }
type Position = [number, number]

export type Feature = {
  type: "Feature"
  geometry:
    | { type: "Point"; coordinates: Position }
    | { type: "LineString"; coordinates: Position[] }
  properties: Record<string, string | number | boolean>
}

export type FeatureCollection = { type: "FeatureCollection"; features: Feature[] }

// Everything the map shows, as the app holds it
export type MapState = {
  start: Coordinate | null
  destination: Coordinate | null
  route: Coordinate[]
  congestion: {
    start: ApiPoint
    end: ApiPoint
    vehicle_count: number
    level: string
    // Cellular-automaton runs only
    stopped?: number
    occupancy?: number
  }[]
  emergencyRoute: Coordinate[]
  blockedAhead: Coordinate[]
  incidentRoute: Coordinate[]
  emergencyLocation: Coordinate | null
  candidates: {
    rank: number
    latitude: number
    longitude: number
    average_seconds: number | null
    worst_seconds: number | null
    within_target_percent: number
  }[]
  stations: {
    order: number
    latitude: number
    longitude: number
    average_seconds_after: number | null
    within_target_percent_after: number
  }[]
  hotspots: { rank: number; start: ApiPoint; end: ApiPoint }[]
  scenarioClosures: { start: Coordinate; end: Coordinate }[]
  vehicles: Vehicle[]
  signals: SignalJunction[]
  floods: FloodInfo[]
}

// One GeoJSON source per kind of thing, so a vehicle update only re-sends
// the vehicles
export const SOURCES = [
  "congestion",
  "hotspots",
  "closures",
  "routes",
  "points",
  "stations",
  "vehicles",
  "signals",
  "floods",
] as const

export type SourceId = (typeof SOURCES)[number]

// Emergency vehicles: colour and the letter drawn on the marker
export const SERVICE_MARKERS: Record<string, { colour: string; letter: string; label: string }> = {
  ambulance: { colour: COLOURS.emergency, letter: "A", label: "Ambulance" },
  fire: { colour: "#d0611f", letter: "F", label: "Fire engine" },
  police: { colour: "#2d4fa3", letter: "P", label: "Police car" },
}

const flip = ([latitude, longitude]: Coordinate): Position => [longitude, latitude]
const fromApi = (point: ApiPoint): Position => [point.longitude, point.latitude]

function point(position: Position, properties: Feature["properties"]): Feature {
  return { type: "Feature", geometry: { type: "Point", coordinates: position }, properties }
}

function line(positions: Position[], properties: Feature["properties"]): Feature {
  return { type: "Feature", geometry: { type: "LineString", coordinates: positions }, properties }
}

const collection = (features: Feature[]): FeatureCollection => ({
  type: "FeatureCollection",
  features,
})

// A path is only drawable with two or more points
const path = (coordinates: Coordinate[]) => coordinates.length > 1

export function vehicleFeatures(vehicles: Vehicle[]): FeatureCollection {
  return collection(
    vehicles
      .filter((vehicle) => !vehicle.finished)
      .map((vehicle) => {
        const service = SERVICE_MARKERS[vehicle.vehicle_type]
        // As on the 2D map: a vehicle counts as an emergency vehicle when
        // it is one of the services or is answering an emergency
        const emergency = Boolean(service) || vehicle.on_emergency
        const marker = service ?? SERVICE_MARKERS.ambulance
        return point([vehicle.longitude, vehicle.latitude], {
          id: vehicle.vehicle_id,
          emergency,
          colour: emergency ? marker.colour : COLOURS.route,
          letter: emergency ? marker.letter : "",
          type: vehicle.vehicle_type,
          answering: vehicle.on_emergency,
          // 1 = driving freely; lower means held up
          speed: vehicle.speed_factor ?? 1,
          ...(vehicle.journey_seconds === undefined ? {} : { journey: vehicle.journey_seconds }),
        })
      }),
  )
}

// One builder per source. Each reads only its own part of the state, so
// the app can rebuild just the sources whose inputs changed.

type Parts<K extends keyof MapState> = Pick<MapState, K>

export function routeFeatures(state: Parts<"route" | "incidentRoute" | "emergencyRoute">): FeatureCollection {
  const routes: Feature[] = []
  // Drawn in this order, bottom to top
  if (path(state.route)) {
    routes.push(line(state.route.map(flip), { kind: "route", colour: COLOURS.route }))
  }
  if (path(state.incidentRoute)) {
    routes.push(line(state.incidentRoute.map(flip), { kind: "reroute", colour: COLOURS.reroute }))
  }
  if (path(state.emergencyRoute)) {
    routes.push(line(state.emergencyRoute.map(flip), { kind: "emergency", colour: COLOURS.emergency }))
  }
  return collection(routes)
}

export function closureFeatures(state: Parts<"blockedAhead" | "scenarioClosures">): FeatureCollection {
  return collection([
    ...(path(state.blockedAhead) ? [line(state.blockedAhead.map(flip), { kind: "ahead" })] : []),
    ...state.scenarioClosures.map((road) => line([flip(road.start), flip(road.end)], { kind: "scenario" })),
  ])
}

export function pointFeatures(state: Parts<"start" | "destination" | "emergencyLocation">): FeatureCollection {
  const points: Feature[] = []
  if (state.start) points.push(point(flip(state.start), { kind: "start", colour: COLOURS.start }))
  if (state.destination) {
    points.push(point(flip(state.destination), { kind: "destination", colour: COLOURS.destination }))
  }
  if (state.emergencyLocation) {
    points.push(point(flip(state.emergencyLocation), { kind: "incident", colour: COLOURS.emergency }))
  }
  return collection(points)
}

export function stationFeatures(state: Parts<"candidates" | "stations">): FeatureCollection {
  return collection([
    ...state.candidates.map((candidate) =>
      point([candidate.longitude, candidate.latitude], {
        kind: "candidate",
        label: String(candidate.rank),
        colour: candidate.rank === 1 ? COLOURS.rankBest : COLOURS.rank,
        // The same lines as the 2D map's popup, one per line
        popup: [
          `Candidate #${candidate.rank}`,
          `Average response: ${minutes(candidate.average_seconds)}`,
          `Worst case: ${minutes(candidate.worst_seconds)}`,
          `Within target: ${candidate.within_target_percent.toFixed(0)}%`,
        ].join("\n"),
      }),
    ),
    ...state.stations.map((station) =>
      point([station.longitude, station.latitude], {
        kind: "station",
        label: String(station.order),
        colour: COLOURS.chosen,
        popup: [
          `Station ${station.order}`,
          `Average with stations 1-${station.order}: ${minutes(station.average_seconds_after)}`,
          `Within target: ${percent(station.within_target_percent_after)}`,
        ].join("\n"),
      }),
    ),
  ])
}

export function congestionFeatures(congestion: MapState["congestion"]): FeatureCollection {
  return collection(
    congestion.map((segment) =>
      line([fromApi(segment.start), fromApi(segment.end)], {
        level: segment.level,
        colour: congestionColour(segment.level),
        vehicles: segment.vehicle_count,
        ...(segment.stopped === undefined ? {} : { stopped: segment.stopped }),
        ...(segment.occupancy === undefined ? {} : { occupancy: segment.occupancy }),
      }),
    ),
  )
}

export function hotspotFeatures(hotspots: MapState["hotspots"]): FeatureCollection {
  return collection(
    hotspots.map((road) => line([fromApi(road.start), fromApi(road.end)], { rank: road.rank })),
  )
}

// One light per approach of every signalised junction, a little way up the
// approach, in the colour its drivers see; bigger, and numbered, with a queue
export function signalFeatures(junctions: SignalJunction[]): FeatureCollection {
  return collection(
    junctions.flatMap((junction) =>
      junction.approaches.map((approach) => {
        const [latitude, longitude] = approachPoint(junction, approach.bearing)
        return point([longitude, latitude], {
          node: junction.node,
          light: approach.light,
          colour: SIGNAL_LIGHTS[approach.light],
          queue: approach.queue,
          label: approach.queue > 0 ? String(approach.queue) : "",
        })
      }),
    ),
  )
}

// Flooded roads, strongest while under water and fading as they recover
export function floodFeatures(floods: FloodInfo[]): FeatureCollection {
  return collection(
    floods.flatMap((flood) =>
      flood.roads.map((road) =>
        line([fromApi(road.start), fromApi(road.end)], {
          flood: flood.id,
          stage: flood.stage,
          closed: flood.closed,
          opacity: flood.stage === "flooded" ? 0.9 : 0.25 + 0.6 * (1 - flood.recovered_percent / 100),
        }),
      ),
    ),
  )
}

export function buildMapData(state: MapState): Record<SourceId, FeatureCollection> {
  return {
    congestion: congestionFeatures(state.congestion),
    hotspots: hotspotFeatures(state.hotspots),
    closures: closureFeatures(state),
    routes: routeFeatures(state),
    points: pointFeatures(state),
    stations: stationFeatures(state),
    vehicles: vehicleFeatures(state.vehicles),
    signals: signalFeatures(state.signals),
    floods: floodFeatures(state.floods),
  }
}

// ------------------------------------------------------------
// Inspect mode: what the 3D map says about the thing clicked, one line
// each, the first line being the title. Only facts the app has.
// ------------------------------------------------------------

export function describeVehicle(properties: Feature["properties"]): string[] {
  const service = SERVICE_MARKERS[String(properties.type)]
  const lines = [`${service?.label ?? "Vehicle"} ${properties.id}`]
  if (properties.answering) lines.push("Answering an emergency")
  const speed = Number(properties.speed)
  lines.push(
    speed >= 0.95
      ? "Driving freely"
      : `Held up: ${Math.round(speed * 100)}% of its free-flow speed`,
  )
  if (typeof properties.journey === "number") {
    lines.push(`Journey so far: ${minutes(properties.journey)}`)
  }
  return lines
}

// OpenMapTiles uses this height for a building with no height or floor
// count in OpenStreetMap
export const DEFAULT_BUILDING_HEIGHT = 5

export function describeBuilding(height: number | undefined, base: number | undefined): string[] {
  if (height === undefined) return ["Building", "No height data"]
  const lines = ["Building", `Height: ${Math.round(height)} m`]
  if (base) lines.push(`Starts ${Math.round(base)} m above the ground`)
  if (height === DEFAULT_BUILDING_HEIGHT) {
    lines.push("Probably estimated: the height used when none is mapped")
  }
  return lines
}

// The fields of /scenarios/nearest-road this uses (its distance_metres is
// how far the click was from the road, not the road's length)
export type InspectedRoad = {
  name?: string | null
  start_point: ApiPoint
  end_point: ApiPoint
}

// Metres between two points (haversine)
export function metresBetween(a: ApiPoint, b: ApiPoint): number {
  const radians = (degrees: number) => (degrees * Math.PI) / 180
  const dLat = radians(b.latitude - a.latitude)
  const dLon = radians(b.longitude - a.longitude)
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(radians(a.latitude)) * Math.cos(radians(b.latitude)) * Math.sin(dLon / 2) ** 2
  return 2 * 6_371_000 * Math.asin(Math.sqrt(h))
}

const LEVELS = ["light", "moderate", "heavy"]

// The same road segment, either way round. Positions drawn on the map
// come back from MapLibre rounded to its tile grid, so allow about 3 m.
export function sameSegment(a: Position[], b: Position[], tolerance = 3e-5): boolean {
  if (a.length !== 2 || b.length !== 2) return false
  const close = (p: Position, q: Position) =>
    Math.abs(p[0] - q[0]) <= tolerance && Math.abs(p[1] - q[1]) <= tolerance
  return (close(a[0], b[0]) && close(a[1], b[1])) || (close(a[0], b[1]) && close(a[1], b[0]))
}

function liveTraffic(segments: Feature[]): string {
  const vehicles = segments.reduce((total, feature) => total + Number(feature.properties.vehicles), 0)
  const level = segments
    .map((feature) => String(feature.properties.level))
    .sort((a, b) => LEVELS.indexOf(b) - LEVELS.indexOf(a))[0]
  return vehicles > 0
    ? `Now: ${vehicles} vehicle${vehicles === 1 ? "" : "s"}, ${level} traffic`
    : "Now: no vehicles on it"
}

// The cellular automaton's queue on these segments (null: not a CA run)
function queueLine(segments: Feature[]): string | null {
  const measured = segments.filter((feature) => typeof feature.properties.stopped === "number")
  if (measured.length === 0) return null
  const stopped = measured.reduce((total, feature) => total + Number(feature.properties.stopped), 0)
  const full = Math.max(...measured.map((feature) => Number(feature.properties.occupancy ?? 0)))
  return `Queue: ${stopped} stopped, road ${Math.round(full * 100)}% full`
}

// The road's live traffic comes from the congestion layer: the same road
// segment (by its two junctions' positions), in either direction
export function describeRoad(road: InspectedRoad, congestion: FeatureCollection): string[] {
  const ends = [fromApi(road.start_point), fromApi(road.end_point)]
  const live = congestion.features.filter((feature) =>
    sameSegment(feature.geometry.coordinates as Position[], ends, 1e-9),
  )
  const queue = queueLine(live)
  return [
    road.name || "Unnamed road",
    `${Math.round(metresBetween(road.start_point, road.end_point))} m, junction to junction`,
    liveTraffic(live),
    ...(queue ? [queue] : []),
  ]
}

// A congestion segment the road lookup could not name
export function describeSegment(segment: Feature): string[] {
  const queue = queueLine([segment])
  return ["Road", liveTraffic([segment]), ...(queue ? [queue] : [])]
}
