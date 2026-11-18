// Turns what the app already knows (routes, vehicles, congestion, stations,
// scenario results) into GeoJSON for the 3D map. Nothing here is invented:
// every feature comes from state the 2D map draws too.
//
// The app stores points as [latitude, longitude] (Leaflet's order); GeoJSON
// wants [longitude, latitude], so every coordinate is flipped here.

import { congestionColour, type Coordinate, type Vehicle } from "./simulationHelpers.ts"
import { COLOURS } from "./mapStyles.ts"
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
  congestion: { start: ApiPoint; end: ApiPoint; vehicle_count: number; level: string }[]
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
          // 1 = driving freely; lower means held up
          speed: vehicle.speed_factor ?? 1,
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
      }),
    ),
  )
}

export function hotspotFeatures(hotspots: MapState["hotspots"]): FeatureCollection {
  return collection(
    hotspots.map((road) => line([fromApi(road.start), fromApi(road.end)], { rank: road.rank })),
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
  }
}
