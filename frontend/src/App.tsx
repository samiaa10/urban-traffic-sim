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

  // Store positions of all vehicles
  const [vehiclePositions, setVehiclePositions] =
    useState<Coordinate[]>([])

  const [result, setResult] = useState("")

  const simulationInterval =
    useRef<number | null>(null)

  const handleMapClick = (location: Coordinate) => {

    // First click = start
    if (!start) {

      setStart(location)
      setDestination(null)
      setRoute([])
      setVehiclePositions([])

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
      setVehiclePositions([])

      setResult(
        "New start selected. Click another location for your destination."
      )
    }
  }

  const calculateRoute = async () => {

    if (!start || !destination) return

    try {

      // --------------------------------
      // 1. Find nearest start road node
      // --------------------------------

      const startResponse = await fetch(
        `http://127.0.0.1:8000/nearest-node?latitude=${start[0]}&longitude=${start[1]}`
      )

      const startNode = await startResponse.json()


      // --------------------------------
      // 2. Find nearest destination node
      // --------------------------------

      const destinationResponse = await fetch(
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

      const data = await routeResponse.json()


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


      // --------------------------------
      // 4. Start backend simulation
      // --------------------------------

      const simulationResponse = await fetch(
        `http://127.0.0.1:8000/simulation/start?start=${startNode.node}&destination=${destinationNode.node}`,
        {
          method: "POST",
        }
      )

      const simulationData =
        await simulationResponse.json()


      // Put both vehicles at their starting positions
      if (simulationData.vehicle_position) {

        setVehiclePositions([
          [
            simulationData.vehicle_position.latitude,
            simulationData.vehicle_position.longitude,
          ],
        ])

      }


      // --------------------------------
      // 5. Stop previous simulation timer
      // --------------------------------

      if (simulationInterval.current !== null) {

        clearInterval(
          simulationInterval.current
        )

      }


      // --------------------------------
      // 6. Move backend vehicles every second
      // --------------------------------

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

              const stepData =
                await response.json()


              // --------------------------------
              // Update ALL vehicle positions
              // --------------------------------

              const positions: Coordinate[] =
                stepData.vehicles.map(
                  (vehicle: {
                    latitude: number
                    longitude: number
                  }) => [
                    vehicle.latitude,
                    vehicle.longitude,
                  ]
                )

              setVehiclePositions(positions)


              // --------------------------------
              // Check if all vehicles finished
              // --------------------------------

              const allFinished =
                stepData.vehicles.every(
                  (vehicle: {
                    finished: boolean
                  }) => vehicle.finished
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


      // --------------------------------
      // 7. Show route information
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


  // Clean up timer if the React component closes
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


      {start && destination && (

        <button onClick={calculateRoute}>
          Calculate Route
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


        {/* Start marker */}

        {start && (

          <CircleMarker
            center={start}
            radius={8}
          />

        )}


        {/* Destination marker */}

        {destination && (

          <CircleMarker
            center={destination}
            radius={8}
          />

        )}


        {/* A* route */}

        <Polyline positions={route} />


        {/* All backend-controlled vehicles */}

        {vehiclePositions.map(
          (position, index) => (

            <CircleMarker
              key={index}
              center={position}
              radius={10}
            />

          )
        )}

      </MapContainer>


      {result && <p>{result}</p>}

    </div>
  )
}

export default App