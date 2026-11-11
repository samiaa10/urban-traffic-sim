import { useEffect, useState } from "react"
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
import { Icon, Panel, Spinner } from "./components.tsx"
import { COLOURS } from "./mapStyles.ts"

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
            color: result.rank === 1 ? COLOURS.rankBest : COLOURS.rank,
            fillColor: result.rank === 1 ? COLOURS.rankBest : COLOURS.rank,
            weight: 2,
            fillOpacity: 0.92,
            // Clicking a marker should not also pick a route start point
            bubblingMouseEvents: false,
          }}
        >
          <Tooltip
            permanent
            direction="center"
            opacity={1}
            className="marker-number"
          >
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

// Navy markers for a chosen set of stations, numbered in pick order
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
            color: COLOURS.chosen,
            fillColor: COLOURS.chosen,
            weight: 2,
            fillOpacity: 0.92,
            bubblingMouseEvents: false,
          }}
        >
          <Tooltip
            permanent
            direction="center"
            opacity={1}
            className="marker-number"
          >
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
// PANEL: the controls (shown in the sidebar)
// ------------------------------------------------

export function OptimiserPanel({
  data,
  onResults,
  setData,
  onSetResults,
  onLoadingChange,
}: {
  data: OptimiserResponse | null
  onResults: (data: OptimiserResponse | null) => void
  setData: StationSetResponse | null
  onSetResults: (data: StationSetResponse | null) => void
  // Lets the screen show a placeholder while a search is running
  onLoadingChange?: (loading: boolean) => void
}) {
  const [loading, setLoading] = useState<"ranking" | "set" | null>(null)
  const [error, setError] = useState("")

  const [stationCount, setStationCount] = useState(3)
  const [targetMinutes, setTargetMinutes] = useState(8)

  useEffect(() => {
    onLoadingChange?.(loading !== null)
  }, [loading, onLoadingChange])

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
    <Panel
      id="optimiser"
      title="Station optimiser"
      caption="Where should ambulance stations go?"
      icon="stations"
    >
      <button
        className="btn btn--primary btn--block"
        onClick={runRanking}
        disabled={loading !== null}
        aria-busy={loading === "ranking"}
      >
        {loading === "ranking" ? <Spinner /> : <Icon name="stations" size={16} />}
        Find best ambulance station locations
      </button>

      {loading === "ranking" && (
        <p className="hint">
          Running {SCENARIOS} random emergencies from every candidate...
        </p>
      )}

      <div className="divider" />

      <div className="fields">
        <label className="field">
          Stations
          <select
            aria-label="Number of stations"
            value={stationCount}
            disabled={loading !== null}
            onChange={(event) => setStationCount(Number(event.target.value))}
          >
            {STATION_COUNTS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Target
          <select
            aria-label="Target response time"
            value={targetMinutes}
            disabled={loading !== null}
            onChange={(event) => setTargetMinutes(Number(event.target.value))}
          >
            {TARGET_MINUTES.map((value) => (
              <option key={value} value={value}>
                {value} min
              </option>
            ))}
          </select>
        </label>
      </div>

      <button
        className="btn btn--block"
        onClick={runStationSet}
        disabled={loading !== null}
        aria-busy={loading === "set"}
      >
        {loading === "set" ? <Spinner /> : <Icon name="chart" size={16} />}
        Find best set of {stationCount} stations
      </button>

      {loading === "set" && (
        <p className="hint">
          Choosing {stationCount} stations that work well together...
        </p>
      )}

      {error && (
        <div role="alert" className="notice notice--error">
          <Icon name="alert" size={17} />
          <span>{error}</span>
        </div>
      )}

      {(data || setData) && (
        <button
          className="btn btn--quiet"
          onClick={() => {
            onResults(null)
            onSetResults(null)
          }}
        >
          <Icon name="close" size={15} />
          Clear results
        </button>
      )}
    </Panel>
  )
}

// ------------------------------------------------
// OUTPUT: the results (shown under the map)
// ------------------------------------------------

export function OptimiserOutput({
  data,
  setData,
  loading,
}: {
  data: OptimiserResponse | null
  setData: StationSetResponse | null
  loading: boolean
}) {
  const hasResults = data !== null || setData !== null

  return (
    <section
      className="results"
      id="optimiser-results"
      aria-label="Optimiser results"
    >
      {!hasResults && loading && (
        <div className="card skeleton" aria-hidden="true">
          <span />
          <span />
          <span />
          <span />
        </div>
      )}

      {!hasResults && !loading && (
        <div className="empty">
          <Icon name="chart" size={26} />
          <strong>No optimisation results yet</strong>
          Run a search from the Station optimiser panel to compare candidate
          locations. Results appear here and as numbered markers on the map.
        </div>
      )}

      {setData && <StationSetResults data={setData} />}

      {data && <RankingResults data={data} />}
    </section>
  )
}
