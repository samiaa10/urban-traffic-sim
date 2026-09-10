import { useState } from "react"

function App() {
  const [result, setResult] = useState<string>("")

  const calculateRoute = async () => {
    try {
      const response = await fetch(
        "http://127.0.0.1:8000/route?start=198846&destination=340003738"
      )

      const data = await response.json()

      setResult(
        `A* calculated a route of ${Math.round(
          data.distance_metres
        )} metres using ${data.nodes_explored} nodes.`
      )
    } catch {
      setResult("Could not connect to the routing API.")
    }
  }

  return (
    <div>
      <h1>NORWICH//SIM</h1>

      <p>Urban Traffic Simulation & Optimisation Platform</p>

      <hr />

      <h2>Routing Engine</h2>

      <p>Norwich road network loaded</p>

      <button onClick={calculateRoute}>
        Calculate Route
      </button>

      {result && <p>{result}</p>}
    </div>
  )
}

export default App
