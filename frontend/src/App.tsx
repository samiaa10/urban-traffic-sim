import { useEffect, useRef, useState } from "react"
import {
  MapContainer,
  TileLayer,
  Polyline,
  CircleMarker,
  useMapEvents,
} from "react-leaflet"
import "leaflet/dist/leaflet.css"

type Coordinate = [number, number]

type Vehicle = {
  vehicle_id: number
  latitude: number
  longitude: number
  distance_travelled: number
  finished: boolean
  vehicle_type: string
  on_emergency: boolean
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

  const [vehicles, setVehicles] =
    useState<Vehicle[]>([])

  const [emergencyLocation, setEmergencyLocation] =
    useState<Coordinate | null>(null)

  const [emergencyRoute, setEmergencyRoute] =
    useState<Coordinate[]>([])

  const [emergencyActive, setEmergencyActive] =
    useState(false)

  const [result, setResult] = useState("")

  const simulationInterval =
    useRef<number | null>(null)

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
      setEmergencyLocation(null)
      setEmergencyRoute([])
      setEmergencyActive(false)

      setResult(
        "Start selected. Click another location for your destination."
      )
    }

    // Second click = destination
    else if (!destination) {

      setDestination(location)

      setResult(
        "Destination selected. Click Calculate Route."
      )
    }

    // Third click = new start
    else {

      setStart(location)
      setDestination(null)
      setRoute([])
      setVehicles([])
      setEmergencyLocation(null)
      setEmergencyRoute([])
      setEmergencyActive(false)

      setResult(
        "New start selected. Click another location for your destination."
      )
    }
  }

  // ------------------------------------------------
  // SIMULATION POLLING
  // ------------------------------------------------

  const startSimulationPolling = () => {

    if (simulationInterval.current !== null) {

      clearInterval(
        simulationInterval.current
      )

    }

    simulationInterval.current =
      window.setInterval(
        async () => {

          try {

            const response = await fetch(
              "http://127.0.0.1:8000/simulation/step",
              {
                method: "POST",
              }
            )

            if (!response.ok) {
              throw new Error(
                "Simulation step failed"
              )
            }

            const stepData =
              await response.json()

            setVehicles(
              stepData.vehicles
            )

            const allFinished =
              stepData.vehicles.length > 0 &&
              stepData.vehicles.every(
                (vehicle: Vehicle) =>
                  vehicle.finished
              )

            if (allFinished) {

              if (
                simulationInterval.current !== null
              ) {

                clearInterval(
                  simulationInterval.current
                )

                simulationInterval.current = null

              }

              setResult(
                "All vehicles reached their destinations."
              )
            }

          } catch {

            if (
              simulationInterval.current !== null
            ) {

              clearInterval(
                simulationInterval.current
              )

              simulationInterval.current = null

            }

            setResult(
              "Simulation connection lost."
            )
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

      // --------------------------------
      // 1. Find nearest start road node
      // --------------------------------

      const startResponse = await fetch(
        `http://127.0.0.1:8000/nearest-node?latitude=${start[0]}&longitude=${start[1]}`
      )

      const startNode =
        await startResponse.json()


      // --------------------------------
      // 2. Find nearest destination node
      // --------------------------------

      const destinationResponse =
        await fetch(
          `http://127.0.0.1:8000/nearest-node?latitude=${destination[0]}&longitude=${destination[1]}`
        )

      const destinationNode =
        await destinationResponse.json()


      // --------------------------------
      // 3. Calculate A* route
      // --------------------------------

      const routeResponse = await fetch(
        `http://127.0.0.1:8000/route?start=${startNode.node}&destination=${destinationNode.node}`
      )

      const data =
        await routeResponse.json()


      if (!routeResponse.ok) {
        throw new Error(
          "Could not calculate route"
        )
      }


      // Convert backend coordinates into Leaflet coordinates
      const coordinates: Coordinate[] =
        data.path_coordinates.map(
          (point: {
            latitude: number
            longitude: number
          }) => [
            point.latitude,
            point.longitude,
          ]
        )

      setRoute(coordinates)

      // Clear previous emergency display
      setEmergencyLocation(null)
      setEmergencyRoute([])
      setEmergencyActive(false)


      // --------------------------------
      // 4. Start backend simulation
      // --------------------------------

      const simulationResponse =
        await fetch(
          `http://127.0.0.1:8000/simulation/start?start=${startNode.node}&destination=${destinationNode.node}`,
          {
            method: "POST",
          }
        )

      const simulationData =
        await simulationResponse.json()


      if (!simulationResponse.ok) {
        throw new Error(
          "Could not start simulation"
        )
      }


      // Put first vehicle at starting position
      if (
        simulationData.vehicle_position
      ) {

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


      // --------------------------------
      // 5. Start simulation polling
      // --------------------------------

      startSimulationPolling()


      // --------------------------------
      // 6. Show route information
      // --------------------------------

      setResult(
        `A* route: ${Math.round(
          data.distance_metres
        )} metres | ${
          data.nodes_explored
        } nodes explored | 2 vehicles`
      )

    } catch {

      setResult(
        "Could not calculate route."
      )

    }
  }

  // ------------------------------------------------
  // TRIGGER EMERGENCY
  // ------------------------------------------------

  const triggerEmergency = async () => {

    try {

      setResult(
        "Setting up ambulance response..."
      )


      // --------------------------------
      // Emergency location
      // --------------------------------

      const emergencyLatitude =
        52.6319

      const emergencyLongitude =
        1.2988


      // --------------------------------
      // Ambulance station location
      // --------------------------------

      const stationLatitude =
        52.63143

      const stationLongitude =
        1.2970518


      // --------------------------------
      // 1. Create ambulance station
      // --------------------------------

      const stationResponse =
        await fetch(
          `http://127.0.0.1:8000/station/create?latitude=${stationLatitude}&longitude=${stationLongitude}&station_type=ambulance`,
          {
            method: "POST",
          }
        )

      if (!stationResponse.ok) {
        throw new Error(
          "Could not create ambulance station"
        )
      }


      // --------------------------------
      // 2. Add ambulance to station
      // --------------------------------

      const vehicleResponse =
        await fetch(
          "http://127.0.0.1:8000/station/add-vehicle?station_id=0&speed=20&vehicle_type=ambulance",
          {
            method: "POST",
          }
        )

      if (!vehicleResponse.ok) {
        throw new Error(
          "Could not add ambulance"
        )
      }


      // --------------------------------
      // 3. Create emergency + dispatch
      // --------------------------------

      const emergencyResponse =
        await fetch(
          `http://127.0.0.1:8000/emergency/create?latitude=${emergencyLatitude}&longitude=${emergencyLongitude}&emergency_type=ambulance`,
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


      // --------------------------------
      // 4. Show emergency on map
      // --------------------------------

      setEmergencyLocation([
        emergencyLatitude,
        emergencyLongitude,
      ])

      const emergencyCoordinates: Coordinate[] =
        emergencyData.route.map(
          (point: {
            latitude: number
            longitude: number
          }) => [
            point.latitude,
            point.longitude,
          ]
        )

      setEmergencyRoute(
        emergencyCoordinates
      )

      setEmergencyActive(true)


      // --------------------------------
      // 5. Restart polling
      // --------------------------------

      startSimulationPolling()


      setResult(
        `🚑 Ambulance ${emergencyData.vehicle_id} dispatched | ${Math.round(
          emergencyData.route_distance_metres
        )} m response route | ${
          emergencyData.nodes_explored
        } nodes explored`
      )

    } catch (error) {

      if (error instanceof Error) {

        setResult(
          `Emergency failed: ${error.message}`
        )

      } else {

        setResult(
          "Emergency dispatch failed."
        )

      }

    }
  }


  // ------------------------------------------------
  // CLEAN UP TIMER
  // ------------------------------------------------

  useEffect(() => {

    return () => {

      if (
        simulationInterval.current !== null
      ) {

        clearInterval(
          simulationInterval.current
        )

      }

    }

  }, [])


  // ------------------------------------------------
  // UI
  // ------------------------------------------------

  return (
    <div>

      <h1>NORWICH//SIM</h1>

      <p>
        Urban Traffic Simulation & Optimisation Platform
      </p>


      <p>

        {start

          ? destination

            ? "Start and destination selected."

            : "Now select your destination."

          : "Click the map to select a start location."

        }

      </p>


      {/* --------------------------------
          ROUTE BUTTON
          -------------------------------- */}

      {start && destination && (

        <button onClick={calculateRoute}>
          Calculate Route
        </button>

      )}


      {/* --------------------------------
          EMERGENCY BUTTON
          -------------------------------- */}

      {start && destination && (

        <button
          onClick={triggerEmergency}
          style={{
            marginLeft: "10px",
          }}
        >
          🚑 Trigger Ambulance Emergency
        </button>

      )}


      <MapContainer
        center={[52.6309, 1.2974]}
        zoom={13}
        style={{
          height: "600px",
          width: "100%",
        }}
      >

        <TileLayer
          attribution="&copy; OpenStreetMap contributors"
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />


        <MapClickHandler
          onClick={handleMapClick}
        />


        {/* --------------------------------
            START MARKER
            -------------------------------- */}

        {start && (

          <CircleMarker
            center={start}
            radius={8}
          />

        )}


        {/* --------------------------------
            DESTINATION MARKER
            -------------------------------- */}

        {destination && (

          <CircleMarker
            center={destination}
            radius={8}
          />

        )}


        {/* --------------------------------
            NORMAL A* ROUTE
            -------------------------------- */}

        <Polyline
          positions={route}
        />


        {/* --------------------------------
            EMERGENCY ROUTE
            -------------------------------- */}

        {emergencyActive &&
          emergencyRoute.length > 0 && (

          <Polyline
            positions={emergencyRoute}
            pathOptions={{
              color: "red",
              weight: 5,
              dashArray: "10, 10",
            }}
          />

        )}


        {/* --------------------------------
            EMERGENCY LOCATION
            -------------------------------- */}

        {emergencyLocation && (

          <CircleMarker
            center={emergencyLocation}
            radius={14}
            pathOptions={{
              color: "red",
              fillColor: "red",
              fillOpacity: 0.8,
            }}
          />

        )}


        {/* --------------------------------
            ALL VEHICLES
            -------------------------------- */}

        {vehicles.map((vehicle) => {

          const isAmbulance =
            vehicle.vehicle_type ===
              "ambulance" ||
            vehicle.on_emergency

          return (

            <CircleMarker
              key={vehicle.vehicle_id}
              center={[
                vehicle.latitude,
                vehicle.longitude,
              ]}
              radius={
                isAmbulance
                  ? 12
                  : 10
              }
              pathOptions={
                isAmbulance
                  ? {
                      color: "red",
                      fillColor: "red",
                      fillOpacity: 0.9,
                    }
                  : undefined
              }
            />

          )

        })}

      </MapContainer>


      {/* --------------------------------
          RESULT
          -------------------------------- */}

      {result && (

        <p>
          {result}
        </p>

      )}

    </div>
  )
}

export default App