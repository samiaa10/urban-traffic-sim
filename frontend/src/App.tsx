import { useState } from "react"
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
  const [destination, setDestination] = useState<Coordinate | null>(null)
  const [route, setRoute] = useState<Coordinate[]>([])
  const [result, setResult] = useState("")

  const handleMapClick = (location: Coordinate) => {
    if (!start) {
      setStart(location)
      setDestination(null)
      setRoute([])
      setResult("Start selected. Click another location for your destination.")
    } else if (!destination) {
      setDestination(location)
      setResult("Destination selected. Click Calculate Route.")
    } else {
      setStart(location)
      setDestination(null)
      setRoute([])
      setResult("New start selected. Click another location for your destination.")
    }
  }

  const calculateRoute = async () => {
    if (!start || !destination) return

    try {
      const startResponse = await fetch(
        `http://127.0.0.1:8000/nearest-node?latitude=${start[0]}&longitude=${start[1]}`
      )

      const destinationResponse = await fetch(
        `http://127.0.0.1:8000/nearest-node?latitude=${destination[0]}&longitude=${destination[1]}`
      )

      const startNode = await startResponse.json()
      const destinationNode = await destinationResponse.json()

      const routeResponse = await fetch(
        `http://127.0.0.1:8000/route?start=${startNode.node}&destination=${destinationNode.node}`
      )

      const data = await routeResponse.json()

      const coordinates: Coordinate[] = data.path_coordinates.map(
        (point: { latitude: number; longitude: number }) => [
          point.latitude,
          point.longitude,
        ]
      )

      setRoute(coordinates)

      setResult(
        `A* route: ${Math.round(data.distance_metres)} metres | ${data.nodes_explored} nodes explored`
      )
    } catch {
      setResult("Could not calculate route.")
    }
  }

  return (
    <div>
      <h1>NORWICH//SIM</h1>

      <p>Urban Traffic Simulation & Optimisation Platform</p>

      <p>
        {start
          ? destination
            ? "Start and destination selected."
            : "Now select your destination."
          : "Click the map to select a start location."}
      </p>

      {start && destination && (
        <button onClick={calculateRoute}>
          Calculate Route
        </button>
      )}

      <MapContainer
        center={[52.6309, 1.2974]}
        zoom={13}
        style={{ height: "600px", width: "100%" }}
      >
        <TileLayer
          attribution="&copy; OpenStreetMap contributors"
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />

        <MapClickHandler onClick={handleMapClick} />

        {start && (
          <CircleMarker center={start} radius={8}>
          </CircleMarker>
        )}

        {destination && (
          <CircleMarker center={destination} radius={8}>
          </CircleMarker>
        )}

        <Polyline positions={route} />
      </MapContainer>

      {result && <p>{result}</p>}
    </div>
  )
}

export default App