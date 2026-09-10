import { useState } from "react"
import { MapContainer, TileLayer, Polyline } from "react-leaflet"
import "leaflet/dist/leaflet.css"

function App() {
  const [route, setRoute] = useState<[number, number][]>([])
  const [result, setResult] = useState("")

  const calculateRoute = async () => {
    try {
      const response = await fetch(
        "http://127.0.0.1:8000/route?start=198846&destination=340003738"
      )

      const data = await response.json()

      const coordinates: [number, number][] =
        data.path_coordinates.map(
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
      setResult("Could not connect to the routing API.")
    }
  }

  return (
    <div>
      <h1>NORWICH//SIM</h1>

      <p>Urban Traffic Simulation & Optimisation Platform</p>

      <button onClick={calculateRoute}>
        Calculate A* Route
      </button>

      {result && <p>{result}</p>}

      <MapContainer
        center={[52.6309, 1.2974]}
        zoom={13}
        style={{ height: "600px", width: "100%" }}
      >
        <TileLayer
          attribution="&copy; OpenStreetMap contributors"
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />

        <Polyline positions={route} />
      </MapContainer>
    </div>
  )
}

export default App