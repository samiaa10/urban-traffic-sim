import { useState } from "react"
import { CircleMarker, Popup, Tooltip } from "react-leaflet"
import {
  SCENARIOS,
  describeFailure,
  fetchRanking,
  fetchStationSet,
  minutes,
  percent,
  type OptimiserResponse,
  type OptimiserResult,
  type StationSetPick,
  type StationSetResponse,
} from "./optimiser.ts"
import {
  RankingResults,
  SHOWN,
  StationSetResults,
} from "./OptimiserResults.tsx"

// Choices offered for the several-station search
const STATION_COUNTS = [2, 3, 4, 5]
const TARGET_MINUTES = [3, 4, 5, 8]

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

// Purple markers for a chosen set of stations, numbered in pick order
export function StationSetMarkers({
  picks,
}: {
  picks: StationSetPick[]
}) {
  return (
    <>
      {picks.map((pick) => (
        <CircleMarker
          key={`station-set-${pick.node}`}
          center={[pick.latitude, pick.longitude]}
          radius={15}
          pathOptions={{
            color: "#6d28d9",
            fillColor: "#7c3aed",
            fillOpacity: 0.85,
            bubblingMouseEvents: false,
          }}
        >
          <Tooltip permanent direction="center" opacity={1}>
            {pick.order}
          </Tooltip>
          <Popup>
            <strong>Station {pick.order}</strong>
            <br />
            Average with stations 1-{pick.order}:{" "}
            {minutes(pick.average_seconds_after)}
            <br />
            Within target: {percent(pick.within_target_percent_after)}
          </Popup>
        </CircleMarker>
      ))}
    </>
  )
}

// ------------------------------------------------
// PANEL: buttons, results
// ------------------------------------------------

export function OptimiserPanel({
  data,
  onResults,
  setData,
  onSetResults,
}: {
  data: OptimiserResponse | null
  onResults: (data: OptimiserResponse | null) => void
  setData: StationSetResponse | null
  onSetResults: (data: StationSetResponse | null) => void
}) {
  const [loading, setLoading] = useState<"ranking" | "set" | null>(null)
  const [error, setError] = useState("")

  const [stationCount, setStationCount] = useState(3)
  const [targetMinutes, setTargetMinutes] = useState(8)

  const runRanking = async () => {
    setLoading("ranking")
    setError("")

    try {
      onResults(await fetchRanking())
    } catch (caught) {
      setError(describeFailure(caught))
    } finally {
      setLoading(null)
    }
  }

  const runStationSet = async () => {
    setLoading("set")
    setError("")

    try {
      onSetResults(await fetchStationSet(stationCount, targetMinutes))
    } catch (caught) {
      setError(describeFailure(caught))
    } finally {
      setLoading(null)
    }
  }

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
        <button onClick={runRanking} disabled={loading !== null}>
          🏥 Find best ambulance station locations
        </button>

        {(data || setData) && (
          <button
            onClick={() => {
              onResults(null)
              onSetResults(null)
            }}
            style={{ marginLeft: "10px" }}
          >
            Clear results
          </button>
        )}

        {loading === "ranking" && (
          <span style={{ marginLeft: "10px" }}>
            Running {SCENARIOS} random emergencies from every candidate...
          </span>
        )}
      </div>

      <div style={{ marginTop: "8px" }}>
        <label>
          Stations:{" "}
          <select
            aria-label="Number of stations"
            value={stationCount}
            onChange={(event) => setStationCount(Number(event.target.value))}
          >
            {STATION_COUNTS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>{" "}
        <label>
          Target:{" "}
          <select
            aria-label="Target response time"
            value={targetMinutes}
            onChange={(event) => setTargetMinutes(Number(event.target.value))}
          >
            {TARGET_MINUTES.map((value) => (
              <option key={value} value={value}>
                {value} min
              </option>
            ))}
          </select>
        </label>{" "}
        <button onClick={runStationSet} disabled={loading !== null}>
          🏥🏥 Find best set of {stationCount} stations
        </button>

        {loading === "set" && (
          <span style={{ marginLeft: "10px" }}>
            Choosing {stationCount} stations that work well together...
          </span>
        )}
      </div>

      {error && <div role="alert">{error}</div>}

      {setData && <StationSetResults data={setData} />}

      {data && <RankingResults data={data} />}
    </div>
  )
}
