import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react"
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
import { BUILDINGS, COLOURS, LINE, MARKER, SIGNAL_LIGHTS, VEHICLE_3D } from "./mapStyles.ts"
import {
  SERVICE_MARKERS,
  closureFeatures,
  congestionFeatures,
  hotspotFeatures,
  pointFeatures,
  routeFeatures,
  signalFeatures,
  floodFeatures,
  stationFeatures,
  vehicleFeatures,
} from "./mapData.ts"
import { SHOWN } from "./OptimiserResults.tsx"
import { ScenarioWorkspace, type ScenarioOverlay } from "./ScenarioWorkspace.tsx"
import { HistoryPanel } from "./HistoryPanel.tsx"
import type { RestartedRun } from "./history.ts"
import { EvaluationSection } from "./EvaluationSection.tsx"
import { LiveAnalytics } from "./LiveAnalytics.tsx"
import { LiveControls } from "./LiveControls.tsx"
import { ResiliencePanel } from "./ResiliencePanel.tsx"
import { CommandCentre } from "./CommandCentre.tsx"
import { AnalystDrawer } from "./AnalystDrawer.tsx"
import { InsightsSection } from "./InsightsSection.tsx"
import { Landing, StatusPill } from "./Landing.tsx"
import { useOverview } from "./overview.ts"
import { setActiveRun } from "./activeRun.ts"
import { PlanningWorkspace } from "./PlanningWorkspace.tsx"
import type { Plan } from "./planning.ts"
import { EmergencyMonitor, IncidentMonitor, SimulationTimeline } from "./Operations.tsx"
import {
  DEFAULT_SIGNALS,
  appendSample,
  type FloodInfo,
  type ResilienceReport,
  approachPoint,
  signalsRequest,
  stepQuery,
  type Sample,
  type SignalJunction,
  type SignalSettings,
  type Speed,
} from "./live.ts"
import {
  SERVICES,
  TRAFFIC_MODELS,
  emergencyRequests,
  queuedMessage,
  serviceOf,
  type ServiceName,
  type TrafficModel,
} from "./services.ts"
import {
  allFinished,
  congestionColour,
  describeEmergency,
  describeSimulationState,
  describeTraffic,
  runPath,
  simulationIdFrom,
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
  // Cellular-automaton runs only: cars standing still, and the share of
  // the road's cells taken
  stopped?: number
  occupancy?: number
  // The road's real shape, [lat, lon] points (geometry=true)
  path?: [number, number][]
}

// The 3D map (MapLibre) is only downloaded when someone switches to it
const Map3D = lazy(() => import("./Map3D.tsx"))

type MapView = "2d" | "3d" | "plan" | "command"
const MAP_VIEW_KEY = "norwich-sim-map-view"

// The last view chosen, remembered in this browser (2D if none, or if
// storage is blocked)
function savedMapView(): MapView {
  try {
    const saved = localStorage.getItem(MAP_VIEW_KEY)
    return saved === "3d" || saved === "plan" || saved === "command" ? saved : "2d"
  } catch {
    return "2d"
  }
}

// An empty path that keeps its identity between renders
const NO_PATH: Coordinate[] = []

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

  // Traffic signals: the settings asked for, and every signalised
  // junction's state from the backend (GET /traffic/signals)
  const [signalSettings, setSignalSettings] = useState<SignalSettings>(DEFAULT_SIGNALS)
  const signalsRef = useRef<SignalSettings>(DEFAULT_SIGNALS)
  const [signalJunctions, setSignalJunctions] = useState<SignalJunction[]>([])

  // Floods on the live run (GET /incident/floods), fetched while any exist
  const [floods, setFloods] = useState<FloodInfo[]>([])
  const floodsActive = useRef(false)

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

  // Scenario analysis: a road the next map click should pick (instead of
  // a route point), and what the map shows for the selected scenario
  const [roadPick, setRoadPick] = useState<{
    label: string
    handler: (latitude: number, longitude: number) => void
  } | null>(null)
  const [scenarioOverlay, setScenarioOverlay] =
    useState<ScenarioOverlay | null>(null)

  // A saved study the History section asked to show
  const [studyToOpen, setStudyToOpen] =
    useState<{ id: string; nonce: number } | null>(null)

  // The running simulation's ID, from /simulation/start (null with an
  // older backend that gives none). A ref, not state: the polling timer
  // reads it, and nothing on screen shows it.
  const simulationId =
    useRef<string | null>(null)

  // How roads behave in the next run (backend traffic models), and which
  // emergency service the emergency button raises
  const [trafficModel, setTrafficModel] = useState<TrafficModel>("osm")
  const [emergencyService, setEmergencyService] = useState<ServiceName>("ambulance")
  // The service of the emergency on the map (the selector may have moved
  // on since): names its route, vehicle and status
  const [dispatchedService, setDispatchedService] = useState<ServiceName>("ambulance")

  // Stations created in the current run (the next one's index)
  const stationsCreated = useRef(0)

  // Live controls: paused, and simulated seconds per tick (a ref too, so
  // the polling timer reads the current speed); the live charts' samples
  const [paused, setPaused] = useState(false)
  const [speed, setSpeed] = useState<Speed>(1)
  const speedRef = useRef<Speed>(1)
  const [history, setHistory] = useState<Sample[]>([])

  // 2D (Leaflet) or 3D (MapLibre) map
  const [mapView, setMapView] = useState<MapView>(savedMapView)
  // 3D map only: clicks describe roads, buildings and vehicles
  const [inspecting, setInspecting] = useState(false)
  const chooseMapView = (view: MapView) => {
    setMapView(view)
    try {
      localStorage.setItem(MAP_VIEW_KEY, view)
    } catch {
      // Storage blocked: the choice lasts until the page is reloaded
    }
  }

  // The city model's figures (landing band, status) and the analyst drawer
  const overview = useOverview()
  const [analystOpen, setAnalystOpen] = useState(false)
  // A run the scenario builder started, shown in the command centre
  const [commandRun, setCommandRun] = useState<string | null>(null)

  // The planning and command views bring their own map, legend and controls
  const workspaceView = mapView === "plan" || mapView === "command"

  // Full URL of an endpoint that acts on the running simulation
  const runUrl = (original: string, perRun: string) =>
    `${API_URL}${runPath(simulationId.current, original, perRun)}`

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

  // The next map click picks a road (for a scenario or a live incident).
  // The map is brought into view, since the button may be far below it.
  const pickRoad = (label: string | null, handler: ((lat: number, lon: number) => void) | null) => {
    setRoadPick(label !== null && handler !== null ? { label, handler } : null)
    if (label !== null) {
      document.getElementById("map")?.scrollIntoView?.({ behavior: "smooth", block: "nearest" })
    }
  }

  const handleMapClick = (location: Coordinate) => {
    // Picking a road for a scenario: this click is for that only
    if (roadPick) {
      setRoadPick(null)
      roadPick.handler(location[0], location[1])
      return
    }

    // First click = start
    if (!start) {
      setStart(location)
      setDestination(null)
      setRoute([])
      setVehicles([])
      setCongestion([])
      setSignalJunctions([])
      setFloods([])
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
      setSignalJunctions([])
      setFloods([])
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
    setPaused(false)
    setConnectionLost(false)

    // One update at a time: a busy run (thousands of vehicles at 10x) can
    // take longer than a tick to step, and sending more steps while one is
    // still being answered would only queue them behind the run's lock, so
    // the page fell further and further behind. Ticks that come while an
    // update is in flight are skipped: the page goes as fast as the
    // backend can answer, and no faster.
    let inFlight = false

    simulationInterval.current =
      window.setInterval(
        async () => {
          if (inFlight) return
          inFlight = true
          try {
            const response = await fetch(
              runUrl("/simulation/step", "/step") + stepQuery(speedRef.current),
              {
                method: "POST",
              }
            )

            if (!response.ok) {
              throw new Error("Simulation step failed")
            }

            const stepData = await response.json()

            setVehicles(stepData.vehicles)
            if (typeof stepData.simulation_time === "number") {
              setSimulationTime(stepData.simulation_time)
            }

            // The live charts' next sample
            if (stepData.summary) {
              setHistory((current) =>
                appendSample(current, { time: stepData.simulation_time, ...stepData.summary })
              )
            }

            // Fetch live congestion data
            try {
              const congestionResponse = await fetch(
                // With each road's real shape, so congestion follows the road
                runUrl("/simulation/congestion?geometry=true", "/congestion?geometry=true")
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

            // Floods and their recovery, while there are any
            if (floodsActive.current) {
              try {
                const floodResponse = await fetch(runUrl("/incident/floods", "/incidents/floods"))
                if (floodResponse.ok) {
                  const floodData = await floodResponse.json()
                  const current: FloodInfo[] = floodData.floods ?? []
                  setFloods(current)
                  floodsActive.current = current.length > 0
                }
              } catch {
                // Keep the simulation running without the flood layer
              }
            }

            // Traffic signals' state, while they are on
            if (signalsRef.current.controller !== "none") {
              try {
                const signalResponse = await fetch(runUrl("/traffic/signals", "/traffic/signals"))
                if (signalResponse.ok) {
                  const signalData = await signalResponse.json()
                  setSignalJunctions(signalData.junctions ?? [])
                }
              } catch {
                // Keep the simulation running without the signal layer
              }
            }

            // Fetch the emergency response status (ETA, response time)
            try {
              const statusResponse = await fetch(
                runUrl("/emergency/status", "/emergencies/status")
              )

              if (statusResponse.ok) {
                const statusData = await statusResponse.json()
                const emergencies = statusData.emergencies ?? []

                // The step's own time is newer; this only fills in for
                // a backend whose steps do not report it
                if (typeof stepData.simulation_time !== "number") {
                  setSimulationTime(statusData.simulation_time ?? null)
                }

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
          } finally {
            inFlight = false
          }
        },
        1000
      )
  }

  // ------------------------------------------------
  // WATCH A RESTARTED RUN (from the History section)
  // ------------------------------------------------

  // The same as the end of Calculate Route, for a run the backend
  // restarted from a saved setup: draw its route, name it in every
  // request, and start stepping it
  const watchRestartedRun = (restarted: RestartedRun) => {
    stopSimulationPolling()

    const coordinates = toCoordinates(restarted.route)
    setStart(coordinates[0])
    setDestination(coordinates[coordinates.length - 1])
    setRoute(coordinates)
    setEmergencyLocation(null)
    setEmergencyRoute([])
    setEmergencyActive(false)
    setIncidentRoute([])
    setIncidentActive(false)
    setEmergencyStatus(null)
    setBlockedAhead([])
    setCongestion([])
    setSignalJunctions([])
    setFloods([])
    setSimulationTime(null)
    setScenarioOverlay(null)
    setHistory([])

    simulationId.current = restarted.simulation_id
    setActiveRun(restarted.simulation_id)
    void reapplySignals()
    setVehicles([
      {
        vehicle_id: 0,
        latitude: restarted.vehicle_position.latitude,
        longitude: restarted.vehicle_position.longitude,
        distance_travelled: 0,
        finished: false,
        vehicle_type: "normal",
        on_emergency: false,
      },
    ])
    startSimulationPolling()
    setRouteInfo({
      distance: restarted.distance_metres,
      nodesExplored: restarted.nodes_explored,
    })
    setResult("Restarted from a saved run")
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
        throw new Error(`Could not calculate route: ${data.detail ?? "no route between these points"}`)
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
      setSignalJunctions([])
      setFloods([])
      setRouteInfo(null)
      setSimulationTime(null)

      // 4. Start backend simulation
      simulationId.current = null
      stationsCreated.current = 0

      const simulationResponse = await fetch(
        `${API_URL}/simulation/start?start=${startNode.node}&destination=${destinationNode.node}` +
          // Its own run, replacing nobody else's (every later request names it)
          `&traffic_model=${trafficModel}&replace_default=false`,
        {
          method: "POST",
        }
      )

      const simulationData =
        await simulationResponse.json()

      if (!simulationResponse.ok) {
        throw new Error(`Could not start the simulation: ${simulationData.detail ?? "the backend refused"}`)
      }

      // Later requests name this run, so they reach it even if another
      // run is started elsewhere
      simulationId.current = simulationIdFrom(simulationData)
      setActiveRun(simulationId.current)

      // A new run starts without signals: give it the ones chosen
      await reapplySignals()

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
    } catch (caught) {
      // The backend answered with a reason (no route, too many runs, ...):
      // show it. No answer at all (fetch failed): it may not be running.
      setResult(
        caught instanceof Error && caught.message.startsWith("Could not ")
          ? caught.message
          : "Could not calculate route. Is the backend running?"
      )
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
        runUrl(
          `/incident/block?start=${startNode.node}&destination=${endNode.node}`,
          `/incidents/block?start=${startNode.node}&destination=${endNode.node}`
        ),
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
        runUrl(
          "/simulation/reroute?vehicle_id=0",
          "/reroute?vehicle_id=0"
        ),
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
    const service = serviceOf(emergencyService)
    setDispatchedService(service.name)
    try {
      setResult(`Setting up ${service.label.toLowerCase()} response...`)

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

      const requests = emergencyRequests(
        service.name,
        stationsCreated.current,
        [stationLatitude, stationLongitude],
        [emergencyLatitude, emergencyLongitude],
      )

      // 1. Create a station of the service
      const stationResponse = await fetch(runUrl(...requests.station), {
        method: "POST",
      })

      if (!stationResponse.ok) {
        throw new Error(`Could not create ${service.label.toLowerCase()} station`)
      }
      stationsCreated.current += 1

      // 2. Add one of its vehicles
      const vehicleResponse = await fetch(runUrl(...requests.vehicle), {
        method: "POST",
      })

      if (!vehicleResponse.ok) {
        throw new Error(`Could not add ${service.label.toLowerCase()}`)
      }

      // 3. Create the emergency and dispatch a vehicle
      const emergencyResponse = await fetch(runUrl(...requests.emergency), {
        method: "POST",
      })

      const emergencyData =
        await emergencyResponse.json()

      if (!emergencyResponse.ok) {
        throw new Error(
          emergencyData.detail ||
          `Could not dispatch ${service.label.toLowerCase()}`
        )
      }

      // Every vehicle of the service is busy: it waits in the queue
      if (emergencyResponse.status === 202) {
        setEmergencyLocation([emergencyLatitude, emergencyLongitude])
        setEmergencyActive(true)
        startSimulationPolling()
        setResult(queuedMessage(service.name, emergencyData.queue_position))
        return
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
        `${service.label} ${emergencyData.vehicle_id} dispatched | ${Math.round(
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
        runUrl("/emergency/block-ahead", "/emergencies/block-ahead"),
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

  // Switch the run's traffic signals (from the live controls)
  async function applySignals(settings: SignalSettings) {
    const [original, perRun] = signalsRequest(settings)
    const response = await fetch(runUrl(original, perRun), { method: "POST" })
    const body = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(typeof body.detail === "string" ? body.detail : "The backend refused those signal settings")
    }
    signalsRef.current = settings
    setSignalSettings(settings)
    if (settings.controller === "none") setSignalJunctions([])
    return body as { junctions: number }
  }

  async function reapplySignals() {
    if (signalsRef.current.controller === "none") return
    try {
      await applySignals(signalsRef.current)
    } catch {
      // The run goes on without signals; the controls show the error next time
    }
  }

  // The planning view's data (GET /optimiser/plan); stable, so the
  // workspace only asks again when its settings change
  const fetchPlan = useCallback(async (query: string): Promise<Plan> => {
    const response = await fetch(`${API_URL}${query}`)
    const body = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(typeof body.detail === "string" ? body.detail : "Could not calculate the plan")
    }
    return body as Plan
  }, [])

  // Pause and speed: from the live controls and the timeline alike
  function togglePause() {
    if (paused) {
      startSimulationPolling()
    } else {
      stopSimulationPolling()
      setPaused(true)
    }
  }

  function changeSpeed(value: Speed) {
    setSpeed(value)
    speedRef.current = value
  }

  // Short description of a point on the map, for the route steps
  const formatPoint = (point: Coordinate) =>
    `${point[0].toFixed(4)}, ${point[1].toFixed(4)}`

  // What the last action told the user: an error or plain information
  const resultIsError = /failed|could not/i.test(result)

  // What the 3D map draws: the same state as the 2D map, one GeoJSON
  // source each, rebuilt only when its own inputs change
  const shownEmergencyRoute = emergencyActive ? emergencyRoute : NO_PATH
  const shownIncidentRoute = incidentActive ? incidentRoute : NO_PATH
  const routesData = useMemo(
    () => routeFeatures({ route, incidentRoute: shownIncidentRoute, emergencyRoute: shownEmergencyRoute }),
    [route, shownIncidentRoute, shownEmergencyRoute],
  )
  const closuresData = useMemo(
    () => closureFeatures({ blockedAhead, scenarioClosures: scenarioOverlay?.closures ?? [] }),
    [blockedAhead, scenarioOverlay],
  )
  const pointsData = useMemo(
    () => pointFeatures({ start, destination, emergencyLocation }),
    [start, destination, emergencyLocation],
  )
  const stationsData = useMemo(
    () => stationFeatures({
      candidates: optimiser?.results.slice(0, SHOWN) ?? [],
      stations: stationSet?.stations ?? [],
    }),
    [optimiser, stationSet],
  )
  const congestionData = useMemo(() => congestionFeatures(congestion), [congestion])
  const hotspotsData = useMemo(() => hotspotFeatures(scenarioOverlay?.hotspots ?? []), [scenarioOverlay])
  const vehiclesData = useMemo(() => vehicleFeatures(vehicles), [vehicles])
  const signalsData = useMemo(() => signalFeatures(signalJunctions), [signalJunctions])
  const floodsData = useMemo(() => floodFeatures(floods), [floods])
  const mapData = useMemo(
    () => ({
      congestion: congestionData,
      hotspots: hotspotsData,
      closures: closuresData,
      routes: routesData,
      points: pointsData,
      stations: stationsData,
      vehicles: vehiclesData,
      signals: signalsData,
      floods: floodsData,
    }),
    [congestionData, hotspotsData, closuresData, routesData, pointsData, stationsData, vehiclesData, signalsData, floodsData],
  )

  // The state of the three route-planning steps
  const bothPointsChosen = start !== null && destination !== null

  // What the next click on the map will do (see handleMapClick)
  // Picking a road for a scenario or live incident beats inspecting
  const inspectingNow = mapView === "3d" && inspecting && !roadPick
  const nextClick = roadPick
    ? { label: roadPick.label, colour: COLOURS.closure }
    : inspectingNow
    ? { label: "Inspect", colour: COLOURS.closure }
    : !start
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

  const servicesOnMap = Object.entries(SERVICE_MARKERS)
    .filter(([type]) =>
      vehicles.some(
        (vehicle) =>
          !vehicle.finished &&
          (vehicle.vehicle_type === type ||
            (type === "ambulance" && vehicle.on_emergency && !(vehicle.vehicle_type in SERVICE_MARKERS)))
      )
    )
    .map(([, marker]) => marker)

  const buildingLegend: LegendEntry[] =
    mapView === "3d"
      ? BUILDINGS.map((step) => ({ label: step.label, colour: step.colour, symbol: "block" }))
      : []

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
      label: `${serviceOf(dispatchedService).label} route`,
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
    // The 2D map draws every emergency vehicle as a red dot; the 3D map
    // gives each service its own colour and letter
    mapView === "2d" && ambulanceOnMap && {
      label: serviceOf(dispatchedService).label,
      colour: COLOURS.emergency,
      symbol: "dot",
    },
    ...(mapView === "3d" ? servicesOnMap : []).map((marker) => ({
      label: marker.label,
      colour: marker.colour,
      symbol: "badge" as const,
      letter: marker.letter,
    })),
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
    scenarioOverlay !== null && scenarioOverlay.hotspots.length > 0 && {
      label: "Most time lost",
      colour: COLOURS.delayHotspot,
      symbol: "wide",
    },
    scenarioOverlay !== null && scenarioOverlay.closures.length > 0 && {
      label: "Scenario closure",
      colour: COLOURS.closure,
      symbol: "dash",
    },
    floods.length > 0 && {
      label: "Flooded road (fades as it recovers)",
      colour: COLOURS.flood,
      symbol: "wide",
    },
    // Each signal approach is drawn in the colour its drivers see
    ...(signalJunctions.length > 0
      ? (["green", "amber", "red"] as const).map((light) => ({
          label: `Signal ${light}`,
          colour: SIGNAL_LIGHTS[light],
          symbol: "dot" as const,
        }))
      : []),
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
            <h1><span>Norwich Urban</span> <span>Traffic Simulator</span></h1>
            <p>Traffic, disruption and emergency response on real roads</p>
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
          <a href="#scenarios">
            <Icon name="chart" size={16} />
            <span>Scenarios</span>
          </a>
          <a href="#insights">
            <Icon name="pulse" size={16} />
            <span>Insights</span>
          </a>
          <a href="#evaluation">
            <Icon name="stations" size={16} />
            <span>In traffic</span>
          </a>
          <a href="#history">
            <Icon name="clock" size={16} />
            <span>History</span>
          </a>
          <StatusPill overview={overview} />
          <button type="button" className="nav-analyst" onClick={() => setAnalystOpen(true)}>
            <Icon name="chat" size={15} />
            <span>Analyst</span>
          </button>
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

      <Landing
        overview={overview}
        onOpen={(view) => {
          chooseMapView(view)
          document.getElementById("map")?.scrollIntoView?.({ behavior: "smooth", block: "start" })
        }}
        onAsk={() => setAnalystOpen(true)}
      />

      <AnalystDrawer
        open={analystOpen}
        onClose={() => setAnalystOpen(false)}
        onOpenRun={(id) => {
          setCommandRun(id)
          setActiveRun(id)
          chooseMapView("command")
          setAnalystOpen(false)
          document.getElementById("map")?.scrollIntoView?.({ behavior: "smooth", block: "start" })
        }}
      />

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

            {/* TRAFFIC MODEL */}
            <label className="field">
              Traffic model
              <select
                aria-label="Traffic model"
                value={trafficModel}
                onChange={(event) => setTrafficModel(event.target.value as TrafficModel)}
              >
                {TRAFFIC_MODELS.map((model) => (
                  <option key={model.value} value={model.value}>
                    {model.label}
                  </option>
                ))}
              </select>
            </label>

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
            caption="Dispatch an ambulance, fire engine or police car to the destination"
          >
            {/* WHICH SERVICE */}
            <label className="field">
              Service
              <select
                aria-label="Emergency service"
                value={emergencyService}
                onChange={(event) => setEmergencyService(event.target.value as ServiceName)}
              >
                {SERVICES.map((service) => (
                  <option key={service.name} value={service.name}>
                    {service.label}
                  </option>
                ))}
              </select>
            </label>

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
                Trigger {serviceOf(emergencyService).short} Emergency
              </button>
            ) : (
              <div className="empty">
                <strong>No active emergency</strong>
                Plan a route, then dispatch a vehicle of the chosen service to the destination.
              </div>
            )}

            {/* EMERGENCY PROGRESS */}
            {emergencyStatus && (
              <>
                <div className="emergency-head">
                  <span className="eyebrow">{serviceOf(dispatchedService).label} status</span>
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
                  Close Road Ahead of {serviceOf(dispatchedService).label}
                </button>
              )}
          </Panel>

          {/* LIVE CONTROLS */}
          <LiveControls
            active={routeInfo !== null}
            paused={paused}
            onPauseToggle={togglePause}
            speed={speed}
            onSpeed={changeSpeed}
            send={async ([original, perRun]) => {
              const response = await fetch(runUrl(original, perRun), { method: "POST" })
              const body = await response.json().catch(() => ({}))
              if (!response.ok) {
                throw new Error(typeof body.detail === "string" ? body.detail : "The backend refused that")
              }
              return body
            }}
            onPickRoad={pickRoad}
            onMessage={setResult}
            signals={signalSettings}
            onApplySignals={applySignals}
            floods={floods}
            simulationTime={simulationTime}
            onFloodsChanged={(next) => {
              setFloods(next)
              floodsActive.current = next.length > 0
            }}
          />

          {/* EMERGENCY STATION OPTIMISER */}
          <OptimiserPanel
            data={optimiser}
            onResults={setOptimiser}
            setData={stationSet}
            onSetResults={setStationSet}
            onLoadingChange={setOptimiserLoading}
            simulationId={() => simulationId.current}
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
              <h2>{mapView === "plan" ? "Emergency infrastructure planning"
                : mapView === "command" ? "Emergency response command centre" : "Norwich road network"}</h2>
              <p>{mapView === "plan"
                ? "Road-network response times from existing and optimised stations"
                : mapView === "command"
                  ? "Incidents, units and response times from the live simulation"
                  : "OpenStreetMap road graph with A* routing"}</p>
            </div>
            <div className="map-view" role="group" aria-label="Map view">
              {(["2d", "3d", "plan", "command"] as const).map((view) => (
                <button
                  key={view}
                  type="button"
                  className="map-view-option"
                  aria-pressed={mapView === view}
                  onClick={() => chooseMapView(view)}
                >
                  {view === "plan" ? "Plan" : view === "command" ? "Command" : view.toUpperCase()}
                </button>
              ))}
            </div>
            {!workspaceView && (
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
            )}
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

            {mapView === "command" ? (
              <CommandCentre key={commandRun ?? "new"} attachTo={commandRun} />
            ) : mapView === "plan" ? (
              <PlanningWorkspace
                fetchPlan={fetchPlan}
                onFailed={(reason) => {
                  chooseMapView("2d")
                  setResult(`The planning map could not start (${reason}), so the 2D map is shown.`)
                }}
              />
            ) : mapView === "3d" ? (
              <Suspense fallback={<div className="map map-loading">Loading the 3D map…</div>}>
                <Map3D
                  data={mapData}
                  onClick={handleMapClick}
                  inspecting={inspectingNow}
                  onInspectingChange={setInspecting}
                  onFailed={(reason) => {
                    chooseMapView("2d")
                    setResult(`The 3D map could not start (${reason}), so the 2D map is shown.`)
                  }}
                />
              </Suspense>
            ) : (
            <MapContainer
              className="map"
              center={[52.6309, 1.2974]}
              zoom={13}
            >
              <TileLayer
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
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
                  positions={
                    segment.path && segment.path.length > 1
                      ? segment.path
                      : [
                          [segment.start.latitude, segment.start.longitude],
                          [segment.end.latitude, segment.end.longitude],
                        ]
                  }
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

              {/* SCENARIO: roads that lost the most time, and its closures */}
              {scenarioOverlay?.hotspots.map((road) => (
                <Polyline
                  key={`hotspot-${road.rank}`}
                  positions={[
                    [road.start.latitude, road.start.longitude],
                    [road.end.latitude, road.end.longitude],
                  ]}
                  pathOptions={{
                    color: COLOURS.delayHotspot,
                    weight: LINE.delayHotspot,
                    opacity: 0.55,
                    lineCap: "round",
                  }}
                />
              ))}
              {scenarioOverlay?.closures.map((road, index) => (
                <Polyline
                  key={`scenario-closure-${index}`}
                  positions={[road.start, road.end]}
                  pathOptions={{
                    color: COLOURS.closure,
                    weight: LINE.closure,
                    dashArray: "6, 6",
                    lineCap: "butt",
                  }}
                />
              ))}

              {/* FLOODED ROADS: strongest under water, fading as they recover */}
              {floodsData.features.map((feature, index) => (
                <Polyline
                  key={`flood-${feature.properties.flood}-${index}`}
                  positions={(feature.geometry.coordinates as [number, number][]).map(
                    ([lng, lat]) => [lat, lng] as [number, number],
                  )}
                  pathOptions={{
                    color: COLOURS.flood,
                    weight: LINE.closure + 2,
                    opacity: Number(feature.properties.opacity),
                    lineCap: "round",
                  }}
                />
              ))}

              {/* TRAFFIC SIGNALS: each approach's light, bigger with a queue */}
              {signalJunctions.flatMap((junction) =>
                junction.approaches.map((approach, index) => (
                  <CircleMarker
                    key={`signal-${junction.node}-${index}`}
                    center={approachPoint(junction, approach.bearing)}
                    radius={4 + Math.min(6, approach.queue * 0.6)}
                    pathOptions={{
                      color: MARKER.outline,
                      weight: 1.5,
                      fillColor: SIGNAL_LIGHTS[approach.light],
                      fillOpacity: 1,
                    }}
                  />
                )),
              )}

              {/* ALL VEHICLES */}
              {vehicles.map((vehicle) => {
                const isAmbulance =
                  vehicle.vehicle_type === "ambulance" || vehicle.on_emergency

                return (
                  // Ordinary traffic understated; emergency vehicles bold
                  <CircleMarker
                    key={vehicle.vehicle_id}
                    center={[vehicle.latitude, vehicle.longitude]}
                    radius={isAmbulance ? MARKER.ambulance : MARKER.vehicle}
                    pathOptions={{
                      color: isAmbulance ? MARKER.outline : "#0e151d",
                      weight: isAmbulance ? MARKER.outlineWeight : 0.8,
                      fillColor: isAmbulance ? COLOURS.emergency : VEHICLE_3D,
                      fillOpacity: isAmbulance ? 1 : 0.85,
                    }}
                  />
                )
              })}
            </MapContainer>
            )}

            {/* LEGEND: congestion, plus whichever layers are on the map */}
            {!workspaceView && (
              <MapLegend
                groups={[
                  { title: "Live congestion", entries: congestionLegend },
                  { title: "On the map", entries: layerLegend },
                  { title: "Building height", entries: buildingLegend },
                ]}
              />
            )}
          </div>

          {/* TIMELINE: simulated time, playback and incidents as they happened */}
          {!workspaceView && <SimulationTimeline
            history={history}
            simulationTime={simulationTime}
            paused={paused}
            onPauseToggle={togglePause}
            speed={speed}
            onSpeed={changeSpeed}
            active={routeInfo !== null}
          />}
        </section>

        {/* ---------------- MONITORING (right of the map) ---------------- */}
        <aside className="monitor" aria-label="Monitoring">
          {routeInfo ? (
            <LiveAnalytics history={history} />
          ) : (
            <section className="monitor-card">
              <h3>Live traffic</h3>
              <p className="monitor-empty">Plan a route to start the simulation and its live figures.</p>
            </section>
          )}
          <EmergencyMonitor
            status={emergencyActive ? emergencyStatus : null}
            service={serviceOf(dispatchedService).label}
          />
          <IncidentMonitor
            latest={history[history.length - 1]}
            floods={floods}
            signals={signalJunctions}
            simulationTime={simulationTime}
          />
        </aside>

        {/* ---------------- OPTIMISER RESULTS ---------------- */}
        <OptimiserOutput
          data={optimiser}
          setData={stationSet}
          loading={optimiserLoading}
        />

        {/* ---------------- SCENARIO ANALYSIS ---------------- */}
        {/* ---------------- LIVE TRAFFIC CHARTS ---------------- */}
        {routeInfo && (
          <ResiliencePanel
            measure={async () => {
              const response = await fetch(runUrl("/resilience/accessibility", "/resilience/accessibility"))
              const body = await response.json().catch(() => ({}))
              if (!response.ok) {
                throw new Error(typeof body.detail === "string" ? body.detail : "Could not measure accessibility")
              }
              return body as ResilienceReport
            }}
          />
        )}

        <ScenarioWorkspace
          onPickRoad={pickRoad}
          onShowOnMap={setScenarioOverlay}
          openStudy={studyToOpen}
        />

        {/* ---------------- CITY INTELLIGENCE ---------------- */}
        <InsightsSection />

        {/* ---------------- THE OPTIMISER TESTED IN TRAFFIC ---------------- */}
        <EvaluationSection />

        {/* ---------------- SAVED RUNS AND STUDIES ---------------- */}
        <HistoryPanel
          onRestarted={watchRestartedRun}
          onOpenStudy={(id) => setStudyToOpen({ id, nonce: Date.now() })}
        />
      </main>
    </div>
  )
}

export default App
