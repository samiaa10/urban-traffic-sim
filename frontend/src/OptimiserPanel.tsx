import { useState } from "react"
import { CircleMarker, Popup, Tooltip } from "react-leaflet"

// Same backend address the rest of the app uses
const API = "http://127.0.0.1:8000"

// How many of the ranked candidates to show in the table and on the map
const SHOWN = 10

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

const minutes = (seconds: number | null) =>
  seconds === null ? "n/a" : `${(seconds / 60).toFixed(1)} min`

// ------------------------------------------------
// MARKERS (drawn inside the map): 1 = best station
// ------------------------------------------------

export function OptimiserMarkers({
  results,
}: {
  results: OptimiserResult[]
}) {
  return (
    <>
      {results.slice(0, SHOWN).map((result) => (
        <CircleMarker
          key={`optimiser-${result.node}`}
          center={[result.latitude, result.longitude]}
          radius={13}
          pathOptions={{
            color: result.rank === 1 ? "#15803d" : "#1d4ed8",
            fillColor: result.rank === 1 ? "#16a34a" : "#2563eb",
            fillOpacity: 0.85,
            // Clicking a marker should not also pick a route start point
            bubblingMouseEvents: false,
          }}
        >
          <Tooltip permanent direction="center" opacity={1}>
            {result.rank}
          </Tooltip>
          <Popup>
            <strong>Candidate #{result.rank}</strong>
            <br />
            Average response: {minutes(result.average_seconds)}
            <br />
            Worst case: {minutes(result.worst_seconds)}
            <br />
            Within target: {result.within_target_percent.toFixed(0)}%
          </Popup>
        </CircleMarker>
      ))}
    </>
  )
}

// ------------------------------------------------
// PANEL: button, explanation and ranked table
// ------------------------------------------------

export function OptimiserPanel({
  data,
  onResults,
}: {
  data: OptimiserResponse | null
  onResults: (data: OptimiserResponse | null) => void
}) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")

  const runOptimiser = async () => {
    setLoading(true)
    setError("")

    try {
      const response = await fetch(
        `${API}/optimiser/stations?scenarios=500`
      )

      const body = await response.json()

      if (!response.ok) {
        throw new Error(body.detail || "The optimiser failed")
      }

      onResults(body)
    } catch (caught) {
      setError(
        caught instanceof Error
          ? `Optimiser failed: ${caught.message}`
          : "Optimiser failed."
      )
    } finally {
      setLoading(false)
    }
  }

  const cell = { padding: "2px 10px", textAlign: "left" as const }

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: "6px",
        padding: "8px 12px",
        margin: "10px 0",
        textAlign: "left",
      }}
    >
      <div>
        <button onClick={runOptimiser} disabled={loading}>
          🏥 Find best ambulance station locations
        </button>

        {data && (
          <button
            onClick={() => onResults(null)}
            style={{ marginLeft: "10px" }}
          >
            Clear results
          </button>
        )}

        {loading && (
          <span style={{ marginLeft: "10px" }}>
            Running 500 random emergencies from every candidate...
          </span>
        )}
      </div>

      {error && <div>{error}</div>}

      {data && data.results.length > 0 && (
        <>
          <div style={{ margin: "8px 0" }}>
            <strong>
              Recommended: candidate #1 at{" "}
              {data.results[0].latitude.toFixed(4)},{" "}
              {data.results[0].longitude.toFixed(4)}
            </strong>{" "}
            (green marker on the map)
            <ul style={{ margin: "4px 0" }}>
              {data.explanation.map((sentence) => (
                <li key={sentence}>{sentence}</li>
              ))}
            </ul>
          </div>

          <div style={{ overflowX: "auto" }}>
            <table style={{ borderCollapse: "collapse", width: "100%" }}>
              <thead>
                <tr>
                  <th style={cell}>Rank</th>
                  <th style={cell}>Average</th>
                  <th style={cell}>Worst case</th>
                  <th style={cell}>
                    Within {minutes(data.target_seconds)}
                  </th>
                  <th style={cell}>Unreached</th>
                  <th style={cell}>Location</th>
                </tr>
              </thead>
              <tbody>
                {data.results.slice(0, SHOWN).map((result) => (
                  <tr
                    key={result.node}
                    style={
                      result.rank === 1
                        ? {
                            fontWeight: "bold",
                            background: "rgba(22, 163, 74, 0.15)",
                          }
                        : undefined
                    }
                  >
                    <td style={cell}>{result.rank}</td>
                    <td style={cell}>{minutes(result.average_seconds)}</td>
                    <td style={cell}>{minutes(result.worst_seconds)}</td>
                    <td style={cell}>
                      {result.within_target_percent.toFixed(0)}%
                    </td>
                    <td style={cell}>{result.unreachable}</td>
                    <td style={cell}>
                      {result.latitude.toFixed(4)},{" "}
                      {result.longitude.toFixed(4)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ fontSize: "14px", marginTop: "6px" }}>
            Top {Math.min(SHOWN, data.results.length)} of{" "}
            {data.results.length} candidates, {data.scenarios} random
            emergencies. Times are road distance at{" "}
            {Math.round(data.speed * 3.6)} km/h, with no traffic
            {data.closed_roads > 0
              ? `, avoiding ${data.closed_roads} closed road segments`
              : ""}
            . Ranked by average response time; an emergency a station
            cannot reach counts as the slowest response seen.
          </div>
        </>
      )}
    </div>
  )
}
