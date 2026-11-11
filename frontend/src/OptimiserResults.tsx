// The optimiser's results as summaries, charts and tables. Nothing here
// touches Leaflet or the network, so it can be rendered and tested without
// a browser (see tests/optimiserResults.test.tsx).

import { Icon } from "./components.tsx"
import {
  count,
  minutes,
  percent,
  place,
  type OptimiserResponse,
  type StationSetResponse,
} from "./optimiser.ts"

// How many of the ranked candidates to show in the table and on the map
export const SHOWN = 10

// ------------------------------------------------
// A simple bar chart made of HTML (no chart library).
// Each bar is also written out as text, so it can be read without seeing it.
// ------------------------------------------------

type Bar = {
  label: string
  // null = no value (for example, nothing was reachable)
  value: number | null
  text: string
  best?: boolean
}

function BarChart({
  title,
  bars,
  max,
  note,
}: {
  title: string
  bars: Bar[]
  // The value that fills a whole bar. Defaults to the largest value.
  max?: number
  note?: string
}) {
  const values = bars.flatMap((bar) => (bar.value === null ? [] : [bar.value]))
  const scale = max ?? Math.max(...values, 0)

  return (
    <div className="chart">
      <h3>{title}</h3>
      <ul className="bars">
        {bars.map((bar) => {
          const width =
            bar.value === null || scale <= 0
              ? 0
              : Math.max(2, Math.min(100, (bar.value / scale) * 100))

          return (
            <li
              key={bar.label}
              className={`bar-row${bar.best ? " bar-row--best" : ""}`}
            >
              <span className="bar-label">{bar.label}</span>
              <span className="bar-track" aria-hidden="true">
                <span className="bar-fill" style={{ width: `${width}%` }} />
              </span>
              <span className="bar-value">{bar.text}</span>
            </li>
          )
        })}
      </ul>
      {note && <p className="chart-note">{note}</p>}
    </div>
  )
}

// ------------------------------------------------
// One-station ranking: explanation, charts, table, footnote
// ------------------------------------------------

export function RankingResults({ data }: { data: OptimiserResponse }) {
  if (data.results.length === 0) {
    return <div>No candidate stations were found.</div>
  }

  const shown = data.results.slice(0, SHOWN)

  return (
    <div className="card results-card">
      <h2 className="results-title">
        <Icon name="stations" size={18} />
        Single-station ranking
      </h2>

      <div className="results-summary">
        <div>
          <strong>
            Recommended: candidate #1 at{" "}
            {place(data.results[0].latitude, data.results[0].longitude)}
          </strong>{" "}
          (green marker on the map)
        </div>
        <ul>
          {data.explanation.map((sentence) => (
            <li key={sentence}>{sentence}</li>
          ))}
        </ul>
      </div>

      <div className="chart-grid">
        <BarChart
          title="Average response by candidate"
          bars={shown.map((result) => ({
            label: `Candidate ${result.rank}`,
            value: result.average_seconds,
            text: minutes(result.average_seconds),
            best: result.rank === 1,
          }))}
          note="Shorter is better."
        />
        <BarChart
          title={`Emergencies reached within ${minutes(data.target_seconds)}`}
          bars={shown.map((result) => ({
            label: `Candidate ${result.rank}`,
            value: result.within_target_percent,
            text: `${result.within_target_percent.toFixed(0)}%`,
            best: result.rank === 1,
          }))}
          max={100}
          note="Longer is better."
        />
      </div>

      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Rank</th>
              <th>Average</th>
              <th>Worst case</th>
              <th>Within {minutes(data.target_seconds)}</th>
              <th>Unreached</th>
              <th>Location</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((result) => (
              <tr
                key={result.node}
                className={result.rank === 1 ? "is-highlight" : undefined}
              >
                <td>{result.rank}</td>
                <td>{minutes(result.average_seconds)}</td>
                <td>{minutes(result.worst_seconds)}</td>
                <td>{result.within_target_percent.toFixed(0)}%</td>
                <td>{result.unreachable}</td>
                <td>{place(result.latitude, result.longitude)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="method-note">
        Top {Math.min(SHOWN, data.results.length)} of {data.results.length}{" "}
        candidates, {data.scenarios} random emergencies. Times are road
        distance at {Math.round(data.speed * 3.6)} km/h, with no traffic
        {data.closed_roads > 0
          ? `, avoiding ${data.closed_roads} closed road segments`
          : ""}
        . Ranked by average response time; an emergency a station cannot
        reach counts as the slowest response seen.
      </div>
    </div>
  )
}

// ------------------------------------------------
// Several stations: chosen set, comparison with baselines
// ------------------------------------------------

// Short names for the charts (the table keeps the full labels)
const SHORT_LABELS: Record<string, string> = {
  best_single: "Best single",
  top_individual: "Top ranked",
  random: "Random",
}

export function StationSetResults({ data }: { data: StationSetResponse }) {
  if (data.stations.length === 0) {
    return <div>No stations were chosen.</div>
  }

  const target = minutes(data.target_seconds)

  // The chosen set first, then every baseline
  const rows = [
    {
      key: "chosen",
      label: `Chosen ${data.count} station${data.count === 1 ? "" : "s"}`,
      short: "Chosen set",
      metrics: data.result,
      highlight: true,
    },
    ...data.baselines.map((baseline) => ({
      key: baseline.name,
      label: baseline.label,
      short: SHORT_LABELS[baseline.name] ?? baseline.label,
      metrics: baseline,
      highlight: false,
    })),
  ]

  return (
    <div className="card results-card">
      <h2 className="results-title">
        <Icon name="chart" size={18} />
        Multi-station placement
      </h2>

      <div className="results-summary">
        <div>
          <strong>
            Recommended: {data.count} station{data.count === 1 ? "" : "s"}
          </strong>{" "}
          (navy markers on the map, numbered in the order they add value)
        </div>
        <ol>
          {data.stations.map((pick) => (
            <li key={pick.node}>
              {place(pick.latitude, pick.longitude)}
              {" - "}
              {data.count > 1
                ? `average with stations 1-${pick.order}: `
                : "average: "}
              {minutes(pick.average_seconds_after)}
            </li>
          ))}
        </ol>
        <ul>
          {data.explanation.map((sentence) => (
            <li key={sentence}>{sentence}</li>
          ))}
        </ul>
      </div>

      <div className="chart-grid">
        <BarChart
          title="Average response time"
          bars={rows.map((row) => ({
            label: row.short,
            value: row.metrics.average_seconds,
            text: minutes(row.metrics.average_seconds),
            best: row.highlight,
          }))}
          note="Shorter is better."
        />
        <BarChart
          title={`Emergencies reached within ${target}`}
          bars={rows.map((row) => ({
            label: row.short,
            value: row.metrics.within_target_percent,
            text: percent(row.metrics.within_target_percent),
            best: row.highlight,
          }))}
          max={100}
          note="Longer is better."
        />
        {data.stations.length > 1 && (
          <BarChart
            title="Average response as stations are added"
            bars={data.stations.map((pick) => ({
              label: `${pick.order} ${pick.order === 1 ? "station" : "stations"}`,
              value: pick.average_seconds_after,
              text: minutes(pick.average_seconds_after),
              best: pick.order === data.stations.length,
            }))}
            note="Each added station helps less than the one before."
          />
        )}
      </div>

      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Comparison</th>
              <th>Average</th>
              <th>Worst case</th>
              <th>Within {target}</th>
              <th>Unreached</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.key}
                className={row.highlight ? "is-highlight" : undefined}
              >
                <td>{row.label}</td>
                <td>{minutes(row.metrics.average_seconds)}</td>
                <td>{minutes(row.metrics.worst_seconds)}</td>
                <td>{percent(row.metrics.within_target_percent)}</td>
                <td>{count(row.metrics.unreachable)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="method-note">
        {data.count} of {data.candidates_considered} candidate junctions,{" "}
        {data.scenarios} random emergencies, each answered by the nearest
        chosen station. Times are road distance at{" "}
        {Math.round(data.speed * 3.6)} km/h, with no traffic
        {data.closed_roads > 0
          ? `, avoiding ${data.closed_roads} closed road segments`
          : ""}
        . Coverage is the share of all emergencies reached within {target}.
        An emergency no chosen station can reach counts as the slowest
        response seen when choosing; the Unreached column shows how many
        there were.
      </div>
    </div>
  )
}
