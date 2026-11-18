// The emergency services (backend/simulation/services.py) and the traffic
// models (backend/simulation/road_model.py), as the interface offers them.
// Plain logic, tested in tests/services.test.ts.

export type ServiceName = "ambulance" | "fire" | "police"

export type Service = {
  name: ServiceName
  label: string        // "Fire engine"
  short: string        // "Fire" (button text)
  speed: number        // metres per second, the backend's default
  targetMinutes: number
}

export const SERVICES: Service[] = [
  { name: "ambulance", label: "Ambulance", short: "Ambulance", speed: 20, targetMinutes: 7 },
  { name: "fire", label: "Fire engine", short: "Fire", speed: 17, targetMinutes: 8 },
  { name: "police", label: "Police car", short: "Police", speed: 22, targetMinutes: 15 },
]

export const serviceOf = (name: ServiceName): Service =>
  SERVICES.find((service) => service.name === name) ?? SERVICES[0]

export type TrafficModel = "osm" | "simple" | "ca"

export const TRAFFIC_MODELS: { value: TrafficModel; label: string }[] = [
  { value: "osm", label: "Realistic (OpenStreetMap)" },
  { value: "simple", label: "Simple (original rules)" },
  { value: "ca", label: "Cellular automaton (experimental)" },
]

export type Distribution = "uniform" | "population"

// The three requests that set up and raise one emergency: a station of
// the service, one of its vehicles (the station is the run's
// stationIndex-th), and the emergency. [original path, per-run path].
export function emergencyRequests(
  service: ServiceName,
  stationIndex: number,
  station: [number, number],
  emergency: [number, number],
): { station: [string, string]; vehicle: [string, string]; emergency: [string, string] } {
  const { speed } = serviceOf(service)
  const stationQuery = `latitude=${station[0]}&longitude=${station[1]}&station_type=${service}`
  const vehicleQuery = `station_id=${stationIndex}&speed=${speed}&vehicle_type=${service}`
  const emergencyQuery = `latitude=${emergency[0]}&longitude=${emergency[1]}&emergency_type=${service}`

  return {
    station: [`/station/create?${stationQuery}`, `/stations?${stationQuery}`],
    vehicle: [`/station/add-vehicle?${vehicleQuery}`, `/stations/add-vehicle?${vehicleQuery}`],
    emergency: [`/emergency/create?${emergencyQuery}`, `/emergencies?${emergencyQuery}`],
  }
}

// The message when an emergency waits for a vehicle (202 from the backend)
export function queuedMessage(service: ServiceName, position: number): string {
  const { label } = serviceOf(service)
  return (
    `Every ${label.toLowerCase()} is busy: the emergency is queued (number ${position}) ` +
    `and will get the first one free.`
  )
}

// ------------------------------------------------
// The optimiser tested in traffic (/optimiser/evaluations)
// ------------------------------------------------

export type Figure = { mean: number | null; ci95: number | null }

export type EvaluationDemand = {
  vehicles: number
  runs: number
  mean_response_seconds: Figure
  p90_response_seconds: number | null
  worst_response_seconds: number | null
  within_target_percent: Figure
  unreached: number
  network_delay_vehicle_hours: Figure
  added_delay_vehicle_hours: Figure
}

export type EvaluationCandidate = {
  label: string
  stations: { latitude: number; longitude: number }[]
  estimate: { mean_response_seconds: number | null }
  paired_estimate_seconds: number | null
  simulated_mean_response_seconds: number | null
  estimate_gap_percent: number | null
  estimated_rank: number
  simulated_rank: number
  by_demand: EvaluationDemand[]
}

export type Evaluation = {
  evaluation_id: string
  status: string
  report: {
    scenarios_done: number
    scenarios_total: number
    complete: boolean
    candidates: EvaluationCandidate[]
    rank_agreement_kendall_tau: number | null
    explanation: string[]
  }
}

export const EVALUATION_POLL_MS = 3000

export function evaluationRequest(options: {
  kind: "ranking" | "station_set"
  stations: number
  service: ServiceName
  distribution: Distribution
}) {
  return {
    kind: options.kind,
    stations: options.stations,
    top: options.kind === "ranking" ? 5 : 3,
    service: options.service,
    distribution: options.distribution,
  }
}

export const minutesWithInterval = (figure: Figure) =>
  figure.mean === null
    ? "-"
    : `${(figure.mean / 60).toFixed(1)}` +
      (figure.ci95 === null ? "" : ` ± ${(figure.ci95 / 60).toFixed(1)}`) +
      " min"

export const evaluationProgress = (evaluation: Evaluation) =>
  `${evaluation.report.scenarios_done} of ${evaluation.report.scenarios_total} simulations done`
