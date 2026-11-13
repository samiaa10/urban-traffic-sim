// Helpers for the interaction tests: a fake backend (fetch), control over
// the app's one-second polling timer, "clicking" the stand-in map, finding
// what was drawn on it, and realistic backend answers.

import { act, screen } from "@testing-library/react"
import { vi } from "vitest"

import { MARKER } from "../src/mapStyles.ts"
import type {
  OptimiserResponse,
  StationSetResponse,
} from "../src/optimiser.ts"
import type { EmergencyStatus, Vehicle } from "../src/simulationHelpers.ts"
import { getMapHandlers } from "./mapState.ts"

// ------------------------------------------------
// Places on the map
// ------------------------------------------------

export type MapPoint = { lat: number; lng: number }

export const START: MapPoint = { lat: 52.63, lng: 1.29 }
export const DESTINATION: MapPoint = { lat: 52.65, lng: 1.31 }

// Marker sizes (the stand-in map reports each marker's radius), taken
// from mapStyles.ts so the tests follow the design
export const POINT = MARKER.point
export const VEHICLE = MARKER.vehicle
export const EMERGENCY_SPOT = MARKER.incident
export const RANKED_STATION = MARKER.candidate
export const CHOSEN_STATION = MARKER.chosenStation

// ------------------------------------------------
// The fake backend
// ------------------------------------------------

// What one endpoint answers. status defaults to 200; unreadable makes the
// body impossible to parse as JSON.
export type Reply = { status?: number; body?: unknown; unreadable?: boolean }

export type FakeRequest = {
  method: string
  path: string
  query: URLSearchParams
}

// An endpoint answers with a reply, fails like a network error (an Error
// is thrown by fetch), or works out its reply from the request
export type Answer =
  | Reply
  | Error
  | ((request: FakeRequest) => Reply | Promise<Reply>)

// Keys look like "GET /route" or "POST /simulation/start"
export type Backend = Record<string, Answer>

function fakeResponse(reply: Reply): Response {
  const status = reply.status ?? 200

  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      if (reply.unreadable) {
        throw new SyntaxError("Unexpected token < in JSON")
      }
      return reply.body
    },
  } as Response
}

// Replace fetch with the fake backend. Returns a record of every request.
export function mockBackend(backend: Backend) {
  const requests: FakeRequest[] = []

  const fakeFetch = async (
    input: string | URL | Request,
    init?: RequestInit
  ): Promise<Response> => {
    const url = new URL(input instanceof Request ? input.url : String(input))
    const method = (init?.method ?? "GET").toUpperCase()
    const request = { method, path: url.pathname, query: url.searchParams }
    const key = `${method} ${url.pathname}`

    requests.push(request)

    const answer = backend[key]

    if (answer === undefined) {
      throw new Error(`The fake backend has no answer for ${key}`)
    }

    if (answer instanceof Error) {
      throw answer
    }

    return fakeResponse(
      typeof answer === "function" ? await answer(request) : answer
    )
  }

  vi.stubGlobal("fetch", vi.fn(fakeFetch))

  const matching = (key: string) =>
    requests.filter((request) => `${request.method} ${request.path}` === key)

  return {
    // Every request so far, as "METHOD /path", in order
    paths: () => requests.map((request) => `${request.method} ${request.path}`),
    count: (key: string) => matching(key).length,
    last: (key: string): FakeRequest | undefined => matching(key).at(-1),
  }
}

// A promise the test resolves when it chooses (to see loading states)
export function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void

  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })

  return { promise, resolve, reject }
}

// ------------------------------------------------
// The polling timer
// ------------------------------------------------

// How often App.tsx steps the simulation
const POLL_MILLISECONDS = 1000

// Take over the app's polling timer. Other timers (for example the ones
// Testing Library uses while waiting) still run normally.
//   running()  how many polling timers are active
//   tick()     deliver one update: run each active timer once and wait
//              for the requests it makes
export function controlPolling() {
  const active = new Map<number, () => unknown>()
  let nextId = 1_000_000

  const realSetInterval = globalThis.setInterval.bind(globalThis)
  const realClearInterval = globalThis.clearInterval.bind(globalThis)

  const fakeSetInterval = (
    callback: () => unknown,
    delay?: number,
    ...rest: unknown[]
  ) => {
    if (delay !== POLL_MILLISECONDS) {
      return realSetInterval(callback, delay, ...rest)
    }

    const id = nextId++
    active.set(id, callback)
    return id
  }

  const fakeClearInterval = (id?: number) => {
    if (id !== undefined && active.has(id)) {
      active.delete(id)
    } else {
      realClearInterval(id)
    }
  }

  // App.tsx calls window.setInterval and the global clearInterval;
  // under jsdom both are the same object, but cover both in case
  for (const target of new Set([globalThis, window])) {
    vi.spyOn(target, "setInterval").mockImplementation(
      fakeSetInterval as unknown as typeof setInterval
    )
    vi.spyOn(target, "clearInterval").mockImplementation(
      fakeClearInterval as unknown as typeof clearInterval
    )
  }

  return {
    running: () => active.size,
    tick: async () => {
      await act(async () => {
        await Promise.all([...active.values()].map((callback) => callback()))
      })
    },
  }
}

// ------------------------------------------------
// The map
// ------------------------------------------------

export function clickMap(point: MapPoint) {
  const click = getMapHandlers().click

  if (!click) {
    throw new Error("The map has no click handler (is the app rendered?)")
  }

  act(() => {
    click({ latlng: point })
  })
}

// Circle markers of one size (see the constants above)
export const markers = (radius: number) =>
  screen
    .queryAllByTestId("circle-marker")
    .filter((marker) => marker.getAttribute("data-radius") === String(radius))

// Lines of one colour (see COLOURS in mapStyles.ts)
export const lines = (colour: string) =>
  screen
    .queryAllByTestId("polyline")
    .filter((line) => line.getAttribute("data-color") === colour)

// The numbers written on markers of one size
export const markerNumbers = (radius: number) =>
  markers(radius).map(
    (marker) =>
      marker.querySelector('[data-testid="tooltip"]')?.textContent ?? ""
  )

// ------------------------------------------------
// Backend answers
// ------------------------------------------------

// n points along a line, in the backend's format
const points = (count: number, latitude = 52.631, longitude = 1.291) =>
  Array.from({ length: count }, (_, index) => ({
    latitude: latitude + index * 0.001,
    longitude: longitude + index * 0.001,
  }))

// A vehicle as /simulation/step reports it
export function car(overrides: Partial<Vehicle> = {}): Vehicle {
  return {
    vehicle_id: 0,
    latitude: 52.631,
    longitude: 1.291,
    distance_travelled: 0,
    finished: false,
    vehicle_type: "normal",
    on_emergency: false,
    speed_factor: 1,
    journey_seconds: 1,
    ...overrides,
  }
}

// An ambulance on its way, as /emergency/status reports it
export const enRoute: EmergencyStatus & Record<string, unknown> = {
  state: "en_route",
  latitude: DESTINATION.lat,
  longitude: DESTINATION.lng,
  vehicle_id: 1,
  elapsed_seconds: 5,
  response_time_seconds: null,
  remaining_metres: 800,
  eta_seconds: 40,
  reroutes: 0,
  closed_roads: 0,
}

export function rankingAnswer(count: number): OptimiserResponse {
  return {
    scenarios: 500,
    target_seconds: 480,
    speed: 20,
    closed_roads: 0,
    explanation: ["Candidate #1 has the lowest average response time."],
    results: Array.from({ length: count }, (_, index) => ({
      node: 1000 + index + 1,
      latitude: 52.6 + (index + 1) / 1000,
      longitude: 1.29,
      average_seconds: 150 + index * 6,
      worst_seconds: 300 + index * 6,
      within_target_percent: 99.6,
      unreachable: 0,
      rank: index + 1,
    })),
  }
}

export function stationSetAnswer(count: number): StationSetResponse {
  const baseline = (name: string, label: string, stations: number, average: number) => ({
    name,
    label,
    stations,
    average_seconds: average,
    worst_seconds: average * 2,
    within_target_percent: 90,
    unreachable: 0,
  })

  return {
    count,
    candidates_considered: 38,
    scenarios: 500,
    target_seconds: 480,
    speed: 20,
    closed_roads: 0,
    explanation: [`${count} stations together average 1.8 min per emergency.`],
    stations: Array.from({ length: count }, (_, index) => ({
      order: index + 1,
      node: 2000 + index + 1,
      latitude: 52.62 + (index + 1) / 100,
      longitude: 1.28 + (index + 1) / 100,
      average_seconds_after: 160 - index * 15,
      within_target_percent_after: 85 + index * 3,
    })),
    result: {
      average_seconds: 108,
      worst_seconds: 252,
      within_target_percent: 98.6,
      unreachable: 0,
    },
    baselines: [
      baseline("best_single", "Best single station", 1, 160),
      baseline("top_individual", `Top ${count} ranked one by one`, count, 120),
      baseline("random", `${count} random stations (average of 100)`, count, 162),
    ],
  }
}

// A backend where everything works. Tests replace single endpoints.
export function workingBackend(): Backend {
  return {
    // The start point snaps to junction 11, the destination to 22
    "GET /nearest-node": ({ query }) => {
      const latitude = Number(query.get("latitude"))
      const node =
        latitude === START.lat ? 11 : latitude === DESTINATION.lat ? 22 : 33

      return {
        body: {
          node,
          latitude,
          longitude: Number(query.get("longitude")),
        },
      }
    },
    "GET /route": {
      body: {
        algorithm: "A*",
        start: 11,
        destination: 22,
        path: [11, 12, 13, 14, 22],
        path_coordinates: points(5),
        distance_metres: 1234,
        nodes_explored: 77,
      },
    },
    "POST /simulation/start": {
      body: {
        status: "simulation started",
        distance_metres: 1234,
        nodes_explored: 77,
        vehicle_position: { latitude: 52.631, longitude: 1.291 },
      },
    },
    "POST /simulation/step": { body: { vehicles: [car()] } },
    "GET /simulation/congestion": { body: { congestion: [] } },
    "GET /emergency/status": { body: { simulation_time: 1, emergencies: [] } },
    "POST /incident/block": {
      body: {
        status: "incident created",
        rerouting: {
          vehicles_checked: 10,
          affected: 3,
          recalculations: 3,
          rerouted: 3,
          stranded: 0,
          skipped: 0,
        },
      },
    },
    "POST /simulation/reroute": {
      body: {
        status: "vehicle rerouted",
        vehicle_id: 0,
        distance_metres: 1500,
        nodes_explored: 55,
        route: points(4, 52.632),
      },
    },
    "POST /station/create": {
      body: {
        status: "station created",
        station: { latitude: 52.63143, longitude: 1.2970518 },
        station_type: "ambulance",
      },
    },
    "POST /station/add-vehicle": {
      body: {
        status: "emergency vehicle added",
        station_id: 0,
        vehicle_id: 1,
        vehicle_type: "ambulance",
      },
    },
    "POST /emergency/create": {
      body: {
        status: "emergency dispatched",
        emergency_type: "ambulance",
        vehicle_id: 1,
        emergency_location: {
          latitude: DESTINATION.lat,
          longitude: DESTINATION.lng,
        },
        route_distance_metres: 2346,
        nodes_explored: 99,
        route: points(3, 52.6314),
      },
    },
    "POST /emergency/block-ahead": {
      body: {
        status: "road closed, ambulance rerouted",
        vehicle_id: 1,
        blocked_segment: {
          start: { latitude: 52.64, longitude: 1.3 },
          end: { latitude: 52.641, longitude: 1.301 },
        },
        distance_before_metres: 2000,
        distance_after_metres: 2322,
        extra_metres: 322,
        extra_seconds: 16.1,
        route: points(5, 52.6314),
      },
    },
    "GET /optimiser/stations": { body: rankingAnswer(3) },
    "GET /optimiser/station-set": { body: stationSetAnswer(3) },
  }
}
