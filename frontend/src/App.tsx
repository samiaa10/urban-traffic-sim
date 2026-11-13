import { useEffect, useRef, useState } from "react"
import {
  MapContainer,
  TileLayer,
  Polyline,
  CircleMarker,
  useMapEvents,
} from "react-leaflet"
import "leaflet/dist/leaflet.css"
import {
  OptimiserMarkers,
  OptimiserOutput,
  OptimiserPanel,
  StationSetMarkers,
} from "./OptimiserPanel.tsx"
import type {
  OptimiserResponse,
  StationSetResponse,
} from "./optimiser.ts"
import { API_URL } from "./config.ts"
import {
  Icon,
  KpiCard,
  Logo,
  MapLegend,
  Panel,
  Spinner,
  type LegendEntry,
} from "./components.tsx"
import { COLOURS, LINE, MARKER } from "./mapStyles.ts"
import {
  allFinished,
  congestionColour,
  describeEmergency,
  describeSimulationState,
  describeTraffic,
  summariseTraffic,
  toCoordinates,
  type Coordinate,
  type EmergencyStatus,
  type Vehicle,
} from "./simulationHelpers.ts"

type CongestionSegment = {
  start: { latitude: number; longitude: number }
  end: { latitude: number; longitude: number }
  vehicle_count: number
  level: string
}

type RouteInfo = {
  distance: number
  nodesExplored: number
}

function MapClickHandler({
  onClick,
}: {
  onClick: (location: Coordinate) => void
}) {
  useMapEvents({
    click(event) {
      onClick([event.latlng.lat, event.latlng.lng])
    },
  })

  return null
}

function App() {
  const [start, setStart] = useState<Coordinate | null>(null)
  const [destination, setDestination] =
    useState<Coordinate | null>(null)

  const [route, setRoute] = useState<Coordinate[]>([])

  const [congestion, setCongestion] =
    useState<CongestionSegment[]>([])

  const [vehicles, setVehicles] =
    useState<Vehicle[]>([])

  const [emergencyLocation, setEmergencyLocation] =
    useState<Coordinate | null>(null)

  const [emergencyRoute, setEmergencyRoute] =
    useState<Coordinate[]>([])

  const [emergencyActive, setEmergencyActive] =
    useState(false)

  const [incidentActive, setIncidentActive] =
    useState(false)

  const [incidentRoute, setIncidentRoute] =
    useState<Coordinate[]>([])

  const [emergencyStatus, setEmergencyStatus] =
    useState<EmergencyStatus | null>(null)

  const [blockedAhead, setBlockedAhead] =
    useState<Coordinate[]>([])

  // Details of the route the simulation was started with
  const [routeInfo, setRouteInfo] =
    useState<RouteInfo | null>(null)

  // Is the polling timer running, and did the backend stop answering?
  const [running, setRunning] = useState(false)
  const [connectionLost, setConnectionLost] = useState(false)

  // Simulated seconds, reported by the backend
  const [simulationTime, setSimulationTime] =
    useState<number | null>(null)

  // Feedback about the last thing the user did
  const [result, setResult] = useState("")

  // Which button is waiting for the backend (it shows a spinner)
  const [busy, setBusy] = useState<string | null>(null)

  const run = async (name: string, action: () => Promise<void>) => {
    setBusy(name)

    try {
      await action()
    } finally {
      setBusy(null)
    }
  }

  // Is the station optimiser waiting for the backend?
  const [optimiserLoading, setOptimiserLoading] = useState(false)

  // Latest answer from the emergency station optimiser
  const [optimiser, setOptimiser] =
    useState<OptimiserResponse | null>(null)

  // Latest answer from the several-station optimiser
  const [stationSet, setStationSet] =
    useState<StationSetResponse | null>(null)

  const simulationInterval =
    useRef<number | null>(null)

  const stopSimulationPolling = () => {
    if (simulationInterval.current !== null) {
      clearInterval(simulationInterval.current)
      simulationInterval.current = null
    }

    setRunning(false)
  }

  // ------------------------------------------------
  // MAP CLICKING
  // ------------------------------------------------

  const handleMapClick = (location: Coordinate) => {
    // First click = start
    if (!start) {
      setStart(location)
      setDestination(null)
      setRoute([])
      setVehicles([])
      setCongestion([])
      setEmergencyLocation(null)
      setEmergencyRoute([])
      setEmergencyActive(false)
      setIncidentActive(false)
      setIncidentRoute([])
      setEmergencyStatus(null)
      setBlockedAhead([])
      setRouteInfo(null)
      setSimulationTime(null)
      setConnectionLost(false)
      stopSimulationPolling()
      setResult("")
    }

    // Second click = destination
    else if (!destination) {
      setDestination(location)
      setResult("")
    }

    // Third click = new start
    else {
      setStart(location)
      setDestination(null)
      setRoute([])
      setVehicles([])
      setCongestion([])
      setEmergencyLocation(null)
      setEmergencyRoute([])
      setEmergencyActive(false)
      setIncidentActive(false)
      setIncidentRoute([])
      setEmergencyStatus(null)
      setBlockedAhead([])
      setRouteInfo(null)
      setSimulationTime(null)
      setConnectionLost(false)
      stopSimulationPolling()
      setResult("")
    }
  }

  // ------------------------------------------------
  // SIMULATION POLLING
  // ------------------------------------------------

  const startSimulationPolling = () => {
    if (simulationInterval.current !== null) {
      clearInterval(simulationInterval.current)
    }

    setRunning(true)
    setConnectionLost(false)

    simulationInterval.current =
      window.setInterval(
        async () => {
          try {
            const response = await fetch(
              `${API_URL}/simulation/step`,
              {
                method: "POST",
              }
            )

            if (!response.ok) {
              throw new Error("Simulation step failed")
            }

            const stepData = await response.json()

            setVehicles(stepData.vehicles)

            // Fetch live congestion data
            try {
              const congestionResponse = await fetch(
                `${API_URL}/simulation/congestion`
              )

              if (congestionResponse.ok) {
                const congestionData =
                  await congestionResponse.json()

                setCongestion(
                  congestionData.congestion ?? []
                )
              }
            } catch {
              // Keep the simulation running if congestion
              // data is temporarily unavailable.
            }

            // Fetch the emergency response status (ETA, response time)
            try {
              const statusResponse = await fetch(
                `${API_URL}/emergency/status`
              )

              if (statusResponse.ok) {
                const statusData = await statusResponse.json()
                const emergencies = statusData.emergencies ?? []

                setSimulationTime(
                  statusData.simulation_time ?? null
                )

                setEmergencyStatus(
                  emergencies.length > 0
                    ? emergencies[emergencies.length - 1]
                    : null
                )
              }
            } catch {
              // Status is optional; keep the simulation running.
            }

            if (allFinished(stepData.vehicles)) {
              stopSimulationPolling()
            }
          } catch {
            stopSimulationPolling()
            setConnectionLost(true)
          }
        },
        1000
      )
  }

  // ------------------------------------------------
  // CALCULATE ROUTE
  // ------------------------------------------------

  const calculateRoute = async () => {
    if (!start || !destination) return

    try {
      // 1. Find nearest start road node
      const startResponse = await fetch(
        `${API_URL}/nearest-node?latitude=${start[0]}&longitude=${start[1]}`
      )

      const startNode = await startResponse.json()

      // 2. Find nearest destination node
      const destinationResponse = await fetch(
        `${API_URL}/nearest-node?latitude=${destination[0]}&longitude=${destination[1]}`
      )

      const destinationNode =
        await destinationResponse.json()

      // 3. Calculate A* route
      const routeResponse = await fetch(
        `${API_URL}/route?start=${startNode.node}&destination=${destinationNode.node}`
      )

      const data = await routeResponse.json()

      if (!routeResponse.ok) {
        throw new Error("Could not calculate route")
      }

      // Convert backend coordinates into Leaflet coordinates
      const coordinates: Coordinate[] =
        toCoordinates(data.path_coordinates)

      setRoute(coordinates)

      // Clear previous emergency and incident displays
      setEmergencyLocation(null)
      setEmergencyRoute([])
      setEmergencyActive(false)
      setIncidentRoute([])
      setIncidentActive(false)
      setEmergencyStatus(null)
      setBlockedAhead([])
      setCongestion([])
      setRouteInfo(null)
      setSimulationTime(null)

      // 4. Start backend simulation
      const simulationResponse = await fetch(
        `${API_URL}/simulation/start?start=${startNode.node}&destination=${destinationNode.node}`,
        {
          method: "POST",
        }
      )

      const simulationData =
        await simulationResponse.json()

      if (!simulationResponse.ok) {
        throw new Error("Could not start simulation")
      }

      // Put first vehicle at starting position
      if (simulationData.vehicle_position) {
        setVehicles([
          {
            vehicle_id: 0,
            latitude:
              simulationData.vehicle_position.latitude,
            longitude:
              simulationData.vehicle_position.longitude,
            distance_travelled: 0,
            finished: false,
            vehicle_type: "normal",
            on_emergency: false,
          },
        ])
      }

      // 5. Start simulation polling
      startSimulationPolling()

      // 6. Remember the route numbers for the status panel
      setRouteInfo({
        distance: data.distance_metres,
        nodesExplored: data.nodes_explored,
      })
      setResult("")
    } catch {
      setResult("Could not calculate route. Is the backend running?")
    }
  }

  // ------------------------------------------------
  // TRIGGER ROAD CLOSURE + DYNAMIC REROUTING
  // ------------------------------------------------

  const triggerIncident = async () => {
    try {
      if (route.length < 4) {
        setResult(
          "Route is too short to create an incident."
        )
        return
      }

      setResult(
        "Closing a road on the route and rerouting..."
      )

      // Choose a road segment ahead of the vehicle
      const blockedStart = route[2]
      const blockedEnd = route[3]

      // Find graph node for first point
      const startResponse = await fetch(
        `${API_URL}/nearest-node?latitude=${blockedStart[0]}&longitude=${blockedStart[1]}`
      )

      const startNode = await startResponse.json()

      // Find graph node for second point
      const endResponse = await fetch(
        `${API_URL}/nearest-node?latitude=${blockedEnd[0]}&longitude=${blockedEnd[1]}`
      )

      const endNode = await endResponse.json()

      // Block road segment
      const blockResponse = await fetch(
        `${API_URL}/incident/block?start=${startNode.node}&destination=${endNode.node}`,
        {
          method: "POST",
        }
      )

      const blockData = await blockResponse.json()

      if (!blockResponse.ok) {
        throw new Error(
          blockData.detail ||
          "Could not block road segment"
        )
      }

      const stats = blockData.rerouting

      // Reroute vehicle 0
      const rerouteResponse = await fetch(
        `${API_URL}/simulation/reroute?vehicle_id=0`,
        {
          method: "POST",
        }
      )

      const rerouteData = await rerouteResponse.json()

      if (!rerouteResponse.ok) {
        throw new Error(
          rerouteData.detail ||
          "Could not reroute vehicle"
        )
      }

      // Convert new route to Leaflet coordinates
      const newRoute: Coordinate[] =
        toCoordinates(rerouteData.route)

      setIncidentRoute(newRoute)
      setIncidentActive(true)

      setResult(
        `Road closed: ${stats.affected} of ${
          stats.vehicles_checked
        } active vehicles used it, ${stats.rerouted} rerouted${
          stats.stranded > 0
            ? `, ${stats.stranded} stranded`
            : ""
        } | Vehicle 0 new route cost ${Math.round(
          rerouteData.distance_metres
        )} m (${rerouteData.nodes_explored} nodes explored)`
      )
    } catch (error) {
      if (error instanceof Error) {
        setResult(`Incident failed: ${error.message}`)
      } else {
        setResult("Incident failed.")
      }
    }
  }

  // ------------------------------------------------
  // TRIGGER EMERGENCY
  // ------------------------------------------------

  const triggerEmergency = async () => {
    try {
      setResult("Setting up ambulance response...")

      // Emergency location
      if (!destination) {
        setResult(
          "Please select a destination first."
        )
        return
      }

      const emergencyLatitude = destination[0]
      const emergencyLongitude = destination[1]

      // Ambulance station location
      const stationLatitude = 52.63143
      const stationLongitude = 1.2970518

      // 1. Create ambulance station
      const stationResponse = await fetch(
        `${API_URL}/station/create?latitude=${stationLatitude}&longitude=${stationLongitude}&station_type=ambulance`,
        {
          method: "POST",
        }
      )

      if (!stationResponse.ok) {
        throw new Error(
          "Could not create ambulance station"
        )
      }

      // 2. Add ambulance to station
      const vehicleResponse = await fetch(
        `${API_URL}/station/add-vehicle?station_id=0&speed=20&vehicle_type=ambulance`,
        {
          method: "POST",
        }
      )

      if (!vehicleResponse.ok) {
        throw new Error("Could not add ambulance")
      }

      // 3. Create emergency and dispatch ambulance
      const emergencyResponse = await fetch(
        `${API_URL}/emergency/create?latitude=${emergencyLatitude}&longitude=${emergencyLongitude}&emergency_type=ambulance`,
        {
          method: "POST",
        }
      )

      const emergencyData =
        await emergencyResponse.json()

      if (!emergencyResponse.ok) {
        throw new Error(
          emergencyData.detail ||
          "Could not dispatch ambulance"
        )
      }

      // 4. Show emergency on map
      setEmergencyLocation([
        emergencyLatitude,
        emergencyLongitude,
      ])

      const emergencyCoordinates: Coordinate[] =
        toCoordinates(emergencyData.route)

      setEmergencyRoute(emergencyCoordinates)
      setEmergencyActive(true)

      // 5. Restart polling
      startSimulationPolling()

      setResult(
        `Ambulance ${emergencyData.vehicle_id} dispatched | ${Math.round(
          emergencyData.route_distance_metres
        )} m response route cost | ${
          emergencyData.nodes_explored
        } nodes explored`
      )
    } catch (error) {
      if (error instanceof Error) {
        setResult(`Emergency failed: ${error.message}`)
      } else {
        setResult("Emergency dispatch failed.")
      }
    }
  }

  // ------------------------------------------------
  // CLOSE A ROAD AHEAD OF THE AMBULANCE
  // ------------------------------------------------

  const blockRoadAheadOfAmbulance = async () => {
    try {
      const response = await fetch(
        `${API_URL}/emergency/block-ahead`,
        {
          method: "POST",
        }
      )

      const data = await response.json()

      if (!response.ok) {
        throw new Error(
          data.detail ||
          "Could not close a road ahead"
        )
      }

      // Show the ambulance's new route
      setEmergencyRoute(
        toCoordinates(data.route)
      )

      // Show the closed road
      setBlockedAhead([
        [
          data.blocked_segment.start.latitude,
          data.blocked_segment.start.longitude,
        ],
        [
          data.blocked_segment.end.latitude,
          data.blocked_segment.end.longitude,
        ],
      ])

      setResult(
        `Road ahead closed | Ambulance rerouted: +${Math.round(
          data.extra_metres
        )} m (about +${Math.round(
          data.extra_seconds
        )} s on the response time)`
      )
    } catch (error) {
      if (error instanceof Error) {
        setResult(`Could not close road: ${error.message}`)
      } else {
        setResult("Could not close road.")
      }
    }
  }

  // ------------------------------------------------
  // CLEAN UP TIMER
  // ------------------------------------------------

  useEffect(() => {
    return () => {
      if (simulationInterval.current !== null) {
        clearInterval(simulationInterval.current)
      }
    }
  }, [])

  // ------------------------------------------------
  // WHAT THE STATUS PANEL SHOWS
  // ------------------------------------------------

  const finishedVehicles = vehicles.filter(
    (vehicle) => vehicle.finished
  ).length

  const simulationState = describeSimulationState({
    connectionLost,
    hasStart: start !== null,
    hasDestination: destination !== null,
    hasRoute: routeInfo !== null,
    running,
  })

  const traffic = summariseTraffic(vehicles)

  // ------------------------------------------------
  // UI
  // ------------------------------------------------

  // Short description of a point on the map, for the route steps
  const formatPoint = (point: Coordinate) =>
    `${point[0].toFixed(4)}, ${point[1].toFixed(4)}`

  // What the last action told the user: an error or plain information
  const resultIsError = /failed|could not/i.test(result)

  // The state of the three route-planning steps
  const bothPointsChosen = start !== null && destination !== null

  // What the next click on the map will do (see handleMapClick)
  const nextClick = !start
    ? { label: "Start point", colour: COLOURS.start }
    : !destination
      ? { label: "Destination", colour: COLOURS.destination }
      : { label: "New start point", colour: COLOURS.start }

  // Legend entries, matching how each layer is drawn on the map
  const congestionLegend: LegendEntry[] = [
    ["light", "Light"],
    ["moderate", "Moderate"],
    ["heavy", "Heavy"],
  ].map(([level, label]) => ({
    label,
    colour: congestionColour(level),
    symbol: "wide",
  }))

  const ambulanceOnMap = vehicles.some(
    (vehicle) => vehicle.vehicle_type === "ambulance" || vehicle.on_emergency
  )
  const otherVehiclesOnMap = vehicles.some(
    (vehicle) => !(vehicle.vehicle_type === "ambulance" || vehicle.on_emergency)
  )

  const layerLegend = (
    [
    route.length > 0 && {
      label: "Planned route",
      colour: COLOURS.route,
      symbol: "line",
    },
    incidentActive && incidentRoute.length > 0 && {
      label: "Diversion",
      colour: COLOURS.reroute,
      symbol: "line",
    },
    emergencyActive && emergencyRoute.length > 0 && {
      label: "Ambulance route",
      colour: COLOURS.emergency,
      symbol: "dash",
    },
    blockedAhead.length > 0 && {
      label: "Closed road",
      colour: COLOURS.closure,
      symbol: "wide",
    },
    otherVehiclesOnMap && {
      label: "Vehicle",
      colour: COLOURS.route,
      symbol: "dot",
    },
    ambulanceOnMap && {
      label: "Ambulance",
      colour: COLOURS.emergency,
      symbol: "dot",
    },
    emergencyLocation !== null && {
      label: "Incident",
      colour: COLOURS.emergency,
      symbol: "ring",
    },
    optimiser !== null && {
      label: "Best candidate",
      colour: COLOURS.rankBest,
      symbol: "dot",
    },
    optimiser !== null && {
      label: "Other candidates",
      colour: COLOURS.rank,
      symbol: "dot",
    },
    stationSet !== null && {
      label: "Chosen stations",
      colour: COLOURS.chosen,
      symbol: "dot",
    },
    ] as (LegendEntry | false)[]
  ).filter((entry): entry is LegendEntry => entry !== false)

  const emergencyBadge: Record<
    EmergencyStatus["state"],
    { label: string; tone: string }
  > = {
    waiting: { label: "Waiting", tone: "badge--warn" },
    en_route: { label: "En route", tone: "" },
    blocked: { label: "Blocked", tone: "badge--err" },
    arrived: { label: "Arrived", tone: "badge--ok" },
  }

  return (
    <div className="app">
      <a className="sr-only skip" href="#map">
        Skip to the map
      </a>

      {/* ---------------- HEADER ---------------- */}
      <header className="topbar">
        <div className="brand">
          <Logo />
          <div>
            <h1>
              NORWICH<span className="slashes">//</span>SIM
            </h1>
            <p>Urban Traffic Simulation &amp; Optimisation Platform</p>
          </div>
        </div>

        <nav className="nav" aria-label="Sections">
          <a href="#simulation">
            <Icon name="route" size={16} />
            <span>Simulation</span>
          </a>
          <a href="#emergency">
            <Icon name="siren" size={16} />
            <span>Emergency</span>
          </a>
          <a href="#optimiser">
            <Icon name="stations" size={16} />
            <span>Optimiser</span>
          </a>
          <a
            href="https://github.com/samiaa10/urban-traffic-sim"
            target="_blank"
            rel="noreferrer"
            aria-label="Source code on GitHub"
          >
            <Icon name="github" size={16} />
            <span>Source</span>
          </a>
        </nav>
      </header>

      <main className="workspace">
        {/* ---------------- CONTROLS ---------------- */}
        <aside className="sidebar" aria-label="Controls">
          <Panel
            id="simulation"
            title="Route simulation"
            caption="Plan a journey and watch traffic respond"
          >
            <ol className="steps">
              <li
                className={`step ${start ? "step--done" : "step--current"}`}
              >
                <span className="step-dot">
                  {start ? <Icon name="check" size={13} /> : "1"}
                </span>
                <span className="step-text">
                  Start point
                  {start && <small>{formatPoint(start)}</small>}
                </span>
              </li>
              <li
                className={`step ${
                  destination
                    ? "step--done"
                    : start
                      ? "step--current"
                      : ""
                }`}
              >
                <span className="step-dot">
                  {destination ? <Icon name="check" size={13} /> : "2"}
                </span>
                <span className="step-text">
                  Destination
                  {destination && <small>{formatPoint(destination)}</small>}
                </span>
              </li>
              <li
                className={`step ${
                  routeInfo
                    ? "step--done"
                    : bothPointsChosen
                      ? "step--current"
                      : ""
                }`}
              >
                <span className="step-dot">
                  {routeInfo ? <Icon name="check" size={13} /> : "3"}
                </span>
                <span className="step-text">Run the simulation</span>
              </li>
            </ol>

            {/* ROUTE BUTTON */}
            {start && destination && (
              <button
                className="btn btn--primary btn--block"
                onClick={() => run("route", calculateRoute)}
                disabled={busy === "route"}
                aria-busy={busy === "route"}
              >
                {busy === "route" ? <Spinner /> : <Icon name="route" size={16} />}
                Calculate Route
              </button>
            )}

            <p className="hint">
              Click the map to place points. Once both are set, another click
              starts over.
            </p>
          </Panel>

          <Panel
            title="Road closure"
            caption="Block a road on the route and reroute traffic"
          >
            {/* ROAD CLOSURE BUTTON */}
            {start && destination ? (
              <button
                className="btn btn--block"
                onClick={() => run("incident", triggerIncident)}
                disabled={!routeInfo || busy === "incident"}
                aria-busy={busy === "incident"}
                title={routeInfo ? "" : "Calculate a route first"}
              >
                {busy === "incident" ? (
                  <Spinner />
                ) : (
                  <Icon name="barrier" size={16} />
                )}
                Cause Road Closure &amp; Reroute
              </button>
            ) : (
              <div className="empty">
                <strong>No route yet</strong>
                Plan a route to test how the network copes with a closed road.
              </div>
            )}
          </Panel>

          <Panel
            id="emergency"
            title="Emergency response"
            caption="Dispatch an ambulance to the destination"
          >
            {/* EMERGENCY BUTTON */}
            {start && destination ? (
              <button
                className="btn btn--emergency btn--block"
                onClick={() => run("emergency", triggerEmergency)}
                disabled={!routeInfo || busy === "emergency"}
                aria-busy={busy === "emergency"}
                title={routeInfo ? "" : "Calculate a route first"}
              >
                {busy === "emergency" ? (
                  <Spinner />
                ) : (
                  <Icon name="siren" size={16} />
                )}
                Trigger Ambulance Emergency
              </button>
            ) : (
              <div className="empty">
                <strong>No active emergency</strong>
                Plan a route, then dispatch an ambulance to the destination.
              </div>
            )}

            {/* EMERGENCY PROGRESS */}
            {emergencyStatus && (
              <>
                <div className="emergency-head">
                  <span className="eyebrow">Ambulance status</span>
                  <span
                    className={`badge ${emergencyBadge[emergencyStatus.state].tone}`}
                  >
                    {emergencyBadge[emergencyStatus.state].label}
                  </span>
                </div>

                <dl className="stat-grid stats-list">
                  {emergencyStatus.state === "arrived" ? (
                    <div className="stat">
                      <dt>Response time</dt>
                      <dd>
                        {Math.round(emergencyStatus.response_time_seconds ?? 0)}
                        <small>s</small>
                      </dd>
                    </div>
                  ) : (
                    <>
                      <div className="stat">
                        <dt>ETA</dt>
                        <dd>
                          {Math.round(emergencyStatus.eta_seconds)}
                          <small>s</small>
                        </dd>
                      </div>
                      <div className="stat">
                        <dt>To go</dt>
                        <dd>
                          {Math.round(emergencyStatus.remaining_metres)}
                          <small>m</small>
                        </dd>
                      </div>
                    </>
                  )}
                  <div className="stat">
                    <dt>Elapsed</dt>
                    <dd>
                      {Math.round(emergencyStatus.elapsed_seconds)}
                      <small>s</small>
                    </dd>
                  </div>
                  <div className="stat">
                    <dt>Reroutes</dt>
                    <dd>{emergencyStatus.reroutes}</dd>
                  </div>
                </dl>

                {/* The same information as one sentence, for screen readers */}
                <p className="sr-only">{describeEmergency(emergencyStatus)}</p>
              </>
            )}

            {/* CLOSE ROAD AHEAD OF AMBULANCE BUTTON */}
            {emergencyStatus &&
              (emergencyStatus.state === "en_route" ||
                emergencyStatus.state === "blocked") && (
                <button
                  className="btn btn--block"
                  onClick={() => run("block", blockRoadAheadOfAmbulance)}
                  disabled={busy === "block"}
                  aria-busy={busy === "block"}
                >
                  {busy === "block" ? (
                    <Spinner />
                  ) : (
                    <Icon name="barrier" size={16} />
                  )}
                  Close Road Ahead of Ambulance
                </button>
              )}
          </Panel>

          {/* EMERGENCY STATION OPTIMISER */}
          <OptimiserPanel
            data={optimiser}
            onResults={setOptimiser}
            setData={stationSet}
            onSetResults={setStationSet}
            onLoadingChange={setOptimiserLoading}
          />
        </aside>

        {/* ---------------- STATUS CARDS ---------------- */}
        <section className="kpis" aria-label="Simulation metrics">
          <KpiCard label="Status" sub={simulationState.hint}>
            <div className="kpi-status" role="status">
              <span
                className="dot"
                style={{ background: simulationState.colour }}
                aria-hidden="true"
              />
              {simulationState.label}
            </div>
          </KpiCard>

          <KpiCard
            label="Route length"
            value={
              routeInfo ? (
                <>
                  {Math.round(routeInfo.distance).toLocaleString()}
                  <small>m</small>
                </>
              ) : (
                "-"
              )
            }
            sub={
              routeInfo
                ? `A*, ${routeInfo.nodesExplored.toLocaleString()} nodes explored`
                : "No route calculated"
            }
          />

          <KpiCard
            label="Vehicles"
            value={vehicles.length}
            sub={`${finishedVehicles} finished`}
          />

          <KpiCard
            label="Simulation time"
            value={
              <>
                {simulationTime ?? 0}
                <small>s</small>
              </>
            }
            sub="Simulated seconds"
          />

          <KpiCard
            label="Traffic flow"
            value={traffic ? `${traffic.averageSpeedPercent}%` : "-"}
            sub={
              traffic
                ? `of free-flow speed, ${traffic.heldUp} of ${traffic.driving} held up`
                : "No vehicles driving"
            }
          />

          {/* The same numbers as plain sentences, for screen readers */}
          {routeInfo && (
            <p className="sr-only">
              {`Route: ${Math.round(routeInfo.distance).toLocaleString()} m (A*, ${routeInfo.nodesExplored.toLocaleString()} nodes explored) | Vehicles: ${vehicles.length} (${finishedVehicles} finished) | Simulation time: ${simulationTime ?? 0} s`}
            </p>
          )}
          {routeInfo && traffic && (
            <p className="sr-only">{describeTraffic(traffic)}</p>
          )}
        </section>

        {/* ---------------- MAP ---------------- */}
        <section className="map-card" id="map" aria-label="Norwich map">
          <div className="map-toolbar">
            <div>
              <h2>Norwich road network</h2>
              <p>OpenStreetMap road graph with A* routing</p>
            </div>
            <div className="map-mode" aria-live="polite">
              <span
                className="swatch-dot"
                style={{ background: nextClick.colour }}
                aria-hidden="true"
              />
              <span>
                Next click: <strong>{nextClick.label}</strong>
              </span>
            </div>
          </div>

          <div className="map-stage">
            {/* WHAT THE LAST ACTION TOLD THE USER (floats over the map) */}
            {result && (
              <div
                className={`notice map-notice ${
                  resultIsError ? "notice--error" : "notice--info"
                }`}
                role="status"
              >
                <Icon name={resultIsError ? "alert" : "info"} size={17} />
                <span>{result}</span>
              </div>
            )}

            <MapContainer
              className="map"
              center={[52.6309, 1.2974]}
              zoom={13}
            >
              <TileLayer
                attribution="&copy; OpenStreetMap contributors"
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              />

              <MapClickHandler onClick={handleMapClick} />

              {/* START MARKER */}
              {start && (
                <CircleMarker
                  center={start}
                  radius={MARKER.point}
                  pathOptions={{
                    color: MARKER.outline,
                    weight: MARKER.outlineWeight,
                    fillColor: COLOURS.start,
                    fillOpacity: 1,
                  }}
                />
              )}

              {/* DESTINATION MARKER */}
              {destination && (
                <CircleMarker
                  center={destination}
                  radius={MARKER.point}
                  pathOptions={{
                    color: MARKER.outline,
                    weight: MARKER.outlineWeight,
                    fillColor: COLOURS.destination,
                    fillOpacity: 1,
                  }}
                />
              )}

              {/* LIVE TRAFFIC CONGESTION */}
              {congestion.map((segment, index) => (
                <Polyline
                  key={`congestion-${index}`}
                  positions={[
                    [segment.start.latitude, segment.start.longitude],
                    [segment.end.latitude, segment.end.longitude],
                  ]}
                  pathOptions={{
                    color: congestionColour(segment.level),
                    weight: LINE.congestion,
                    opacity: 0.55,
                    lineCap: "butt",
                  }}
                />
              ))}

              {/* NORMAL A* ROUTE */}
              <Polyline
                positions={route}
                pathOptions={{
                  color: COLOURS.route,
                  weight: LINE.route,
                  opacity: 0.95,
                }}
              />

              {/* EMERGENCY ROUTE */}
              {emergencyActive && emergencyRoute.length > 0 && (
                <Polyline
                  positions={emergencyRoute}
                  pathOptions={{
                    color: COLOURS.emergency,
                    weight: LINE.emergency,
                    dashArray: "8, 7",
                    lineCap: "butt",
                  }}
                />
              )}

              {/* ROAD CLOSED AHEAD OF THE AMBULANCE */}
              {blockedAhead.length > 0 && (
                <Polyline
                  positions={blockedAhead}
                  pathOptions={{
                    color: COLOURS.closure,
                    weight: LINE.closure,
                    opacity: 0.9,
                    lineCap: "butt",
                  }}
                />
              )}

              {/* DYNAMIC REROUTED ROUTE */}
              {incidentActive && incidentRoute.length > 0 && (
                <Polyline
                  positions={incidentRoute}
                  pathOptions={{
                    color: COLOURS.reroute,
                    weight: LINE.reroute,
                    opacity: 0.95,
                  }}
                />
              )}

              {/* EMERGENCY LOCATION */}
              {emergencyLocation && (
                <CircleMarker
                  center={emergencyLocation}
                  radius={MARKER.incident}
                  pathOptions={{
                    color: COLOURS.emergency,
                    weight: 2.5,
                    fillColor: COLOURS.emergency,
                    fillOpacity: 0.16,
                  }}
                />
              )}

              {/* RECOMMENDED STATION LOCATIONS (numbered by rank) */}
              {optimiser && <OptimiserMarkers results={optimiser.results} />}

              {/* CHOSEN SET OF STATIONS (numbered in pick order) */}
              {stationSet && <StationSetMarkers picks={stationSet.stations} />}

              {/* ALL VEHICLES */}
              {vehicles.map((vehicle) => {
                const isAmbulance =
                  vehicle.vehicle_type === "ambulance" || vehicle.on_emergency

                return (
                  <CircleMarker
                    key={vehicle.vehicle_id}
                    center={[vehicle.latitude, vehicle.longitude]}
                    radius={isAmbulance ? MARKER.ambulance : MARKER.vehicle}
                    pathOptions={{
                      color: MARKER.outline,
                      weight: MARKER.outlineWeight,
                      fillColor: isAmbulance ? COLOURS.emergency : COLOURS.route,
                      fillOpacity: 1,
                    }}
                  />
                )
              })}
            </MapContainer>

            {/* LEGEND: congestion, plus whichever layers are on the map */}
            <MapLegend
              groups={[
                { title: "Live congestion", entries: congestionLegend },
                { title: "On the map", entries: layerLegend },
              ]}
            />
          </div>
        </section>

        {/* ---------------- OPTIMISER RESULTS ---------------- */}
        <OptimiserOutput
          data={optimiser}
          setData={stationSet}
          loading={optimiserLoading}
        />
      </main>
    </div>
  )
}

export default App
