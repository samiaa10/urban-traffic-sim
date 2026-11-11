// Plain functions the simulation screen uses, kept apart from React and
// Leaflet so they can be tested on their own (tests/simulation.test.ts).

export type Coordinate = [number, number]

export type ApiPoint = { latitude: number; longitude: number }

export type Vehicle = {
  vehicle_id: number
  latitude: number
  longitude: number
  distance_travelled: number
  finished: boolean
  vehicle_type: string
  on_emergency: boolean
  // 1 = driving freely, lower = held up by traffic.
  // Journey time in simulated seconds, including time spent waiting.
  speed_factor?: number
  journey_seconds?: number
}

export type EmergencyStatus = {
  state: "waiting" | "en_route" | "blocked" | "arrived"
  elapsed_seconds: number
  response_time_seconds: number | null
  remaining_metres: number
  eta_seconds: number
  reroutes: number
}

// Backend points -> the [lat, lon] pairs Leaflet draws
export const toCoordinates = (points: ApiPoint[]): Coordinate[] =>
  points.map((point) => [point.latitude, point.longitude])

// Colour of a road on the congestion layer
export function congestionColour(level: string): string {
  if (level === "heavy") return "#c0392b"
  if (level === "moderate") return "#e0932f"
  return "#2e9e6b"
}

// ------------------------------------------------
// Traffic summary
// ------------------------------------------------

export type TrafficSummary = {
  // Vehicles still driving (not yet arrived)
  driving: number
  // How many of them are going slower than full speed
  heldUp: number
  // Average speed as a percentage of full speed, 100 = free flowing
  averageSpeedPercent: number
}

// How badly traffic is slowing the vehicles that are still on their way.
// Returns null when nobody is driving (nothing to report).
export function summariseTraffic(vehicles: Vehicle[]): TrafficSummary | null {
  const driving = vehicles.filter((vehicle) => !vehicle.finished)

  if (driving.length === 0) return null

  // A vehicle the backend gave no speed for counts as free flowing
  const factors = driving.map((vehicle) => vehicle.speed_factor ?? 1)

  const average =
    factors.reduce((total, factor) => total + factor, 0) / factors.length

  return {
    driving: driving.length,
    heldUp: factors.filter((factor) => factor < 1).length,
    averageSpeedPercent: Math.round(average * 100),
  }
}

export function describeTraffic(summary: TrafficSummary): string {
  const who =
    summary.heldUp === 0
      ? "no vehicles held up"
      : `${summary.heldUp} of ${summary.driving} vehicles held up`

  return `Traffic: ${who} | average speed ${summary.averageSpeedPercent}% of free flow`
}

// ------------------------------------------------
// Status panel
// ------------------------------------------------

export type SimulationState = {
  label: string
  hint: string
  colour: string
}

export function describeSimulationState(input: {
  connectionLost: boolean
  hasStart: boolean
  hasDestination: boolean
  hasRoute: boolean
  running: boolean
}): SimulationState {
  if (input.connectionLost) {
    return {
      label: "Connection lost",
      hint: "The backend stopped responding. Is it still running?",
      colour: "#b4423a",
    }
  }

  const waiting = {
    label: "Waiting for input",
    hint: "Click the map to choose a start point.",
    colour: "#b7791f",
  }

  if (!input.hasStart) return waiting

  if (!input.hasDestination) {
    return { ...waiting, hint: "Click the map to choose a destination." }
  }

  if (!input.hasRoute) {
    return {
      ...waiting,
      hint: "Click Calculate Route to start the simulation.",
    }
  }

  if (input.running) {
    return {
      label: "Running",
      hint: "Vehicles are moving.",
      colour: "#2f7d6b",
    }
  }

  return {
    label: "Completed",
    hint: "All vehicles have finished their journeys.",
    colour: "#2a5d8f",
  }
}

// The one-line emergency summary under "Emergency response:"
export function describeEmergency(status: EmergencyStatus): string {
  let progress: string

  if (status.state === "arrived") {
    progress = `Arrived. Response time ${Math.round(
      status.response_time_seconds ?? 0
    )} s`
  } else if (status.state === "blocked") {
    progress = "Stopped by a closed road"
  } else if (status.state === "waiting") {
    progress = "Waiting for an ambulance"
  } else {
    progress = `En route | ETA ${Math.round(
      status.eta_seconds
    )} s | ${Math.round(status.remaining_metres)} m to go`
  }

  const rerouted =
    status.reroutes === 0 ? "not rerouted" : `rerouted ${status.reroutes}x`

  return `${progress} | ${rerouted} | ${Math.round(
    status.elapsed_seconds
  )} s elapsed`
}

// Has every vehicle arrived? (An empty list does not count.)
export const allFinished = (vehicles: Vehicle[]): boolean =>
  vehicles.length > 0 && vehicles.every((vehicle) => vehicle.finished)
