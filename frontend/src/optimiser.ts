// Station optimiser: response types, requests to the backend, and the
// small formatting helpers. No React or Leaflet in here, so it can be
// tested on its own (see tests/optimiser.test.ts).

// Same backend address the rest of the app uses
export const API = "http://127.0.0.1:8000"

// ------------------------------------------------
// One-station ranking: /optimiser/stations
// ------------------------------------------------

export type OptimiserResult = {
  node: number
  latitude: number
  longitude: number
  average_seconds: number | null
  worst_seconds: number | null
  within_target_percent: number
  unreachable: number
  rank: number
}

export type OptimiserResponse = {
  scenarios: number
  target_seconds: number
  speed: number
  closed_roads: number
  explanation: string[]
  results: OptimiserResult[]
}

// ------------------------------------------------
// Several stations at once: /optimiser/station-set
// ------------------------------------------------

export type StationSetMetrics = {
  average_seconds: number | null
  worst_seconds: number | null
  within_target_percent: number
  // A whole number for the chosen set, an average for the random baseline
  unreachable: number
}

export type StationSetPick = {
  order: number
  node: number
  latitude: number
  longitude: number
  average_seconds_after: number | null
  within_target_percent_after: number
}

export type StationSetBaseline = StationSetMetrics & {
  name: string
  label: string
  stations: number
}

export type StationSetResponse = {
  count: number
  candidates_considered: number
  scenarios: number
  target_seconds: number
  speed: number
  closed_roads: number
  explanation: string[]
  stations: StationSetPick[]
  result: StationSetMetrics
  baselines: StationSetBaseline[]
}

// ------------------------------------------------
// Formatting
// ------------------------------------------------

export const minutes = (seconds: number | null) =>
  seconds === null ? "n/a" : `${(seconds / 60).toFixed(1)} min`

export const percent = (value: number) => `${value.toFixed(1)}%`

// Whole numbers stay whole ("2"), averages get a decimal ("1.9")
export const count = (value: number) =>
  Number.isInteger(value) ? String(value) : value.toFixed(1)

export const place = (latitude: number, longitude: number) =>
  `${latitude.toFixed(4)}, ${longitude.toFixed(4)}`

// ------------------------------------------------
// Requests
// ------------------------------------------------

export type FetchLike = (url: string) => Promise<Response>

// Ask the backend for JSON. Failures become an Error whose message is
// something a person can read: the backend's own explanation when it
// gave one, otherwise a generic sentence.
export async function requestJson<T>(
  path: string,
  fetchImpl: FetchLike = (url) => fetch(url),
  fallback = "The optimiser failed"
): Promise<T> {
  const response = await fetchImpl(`${API}${path}`)

  let body: unknown

  try {
    body = await response.json()
  } catch {
    throw new Error(
      response.ok
        ? "The server sent an unreadable answer"
        : fallback
    )
  }

  if (!response.ok) {
    // FastAPI uses a string for our own errors, but a list of objects
    // for 422 validation errors, which is not worth showing.
    const detail = (body as { detail?: unknown } | null)?.detail

    throw new Error(typeof detail === "string" ? detail : fallback)
  }

  return body as T
}

export const SCENARIOS = 500

export function fetchRanking(
  fetchImpl?: FetchLike
): Promise<OptimiserResponse> {
  return requestJson<OptimiserResponse>(
    `/optimiser/stations?scenarios=${SCENARIOS}`,
    fetchImpl
  )
}

export function fetchStationSet(
  stations: number,
  targetMinutes: number,
  fetchImpl?: FetchLike
): Promise<StationSetResponse> {
  return requestJson<StationSetResponse>(
    `/optimiser/station-set?stations=${stations}` +
      `&scenarios=${SCENARIOS}&target_seconds=${targetMinutes * 60}`,
    fetchImpl
  )
}

// What to show when a request fails
export function describeFailure(caught: unknown): string {
  return caught instanceof Error
    ? `Optimiser failed: ${caught.message}`
    : "Optimiser failed."
}
