// The optimiser's results as tables and text. Nothing here touches
// Leaflet or the network, so it can be rendered and tested without a
// browser (see tests/optimiserResults.test.tsx).

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

const cell = { padding: "2px 10px", textAlign: "left" as const }

// ------------------------------------------------
// One-station ranking: explanation, table, footnote
// ------------------------------------------------

export function RankingResults({ data }: { data: OptimiserResponse }) {
  if (data.results.length === 0) {
    return <div>No candidate stations were found.</div>
  }

  return (
    <>
      <div style={{ margin: "8px 0" }}>
        <strong>
          Recommended: candidate #1 at{" "}
          {place(data.results[0].latitude, data.results[0].longitude)}
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
              <th style={cell}>Within {minutes(data.target_seconds)}</th>
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
                  {place(result.latitude, result.longitude)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ fontSize: "14px", marginTop: "6px" }}>
        Top {Math.min(SHOWN, data.results.length)} of {data.results.length}{" "}
        candidates, {data.scenarios} random emergencies. Times are road
        distance at {Math.round(data.speed * 3.6)} km/h, with no traffic
        {data.closed_roads > 0
          ? `, avoiding ${data.closed_roads} closed road segments`
          : ""}
        . Ranked by average response time; an emergency a station cannot
        reach counts as the slowest response seen.
      </div>
    </>
  )
}

// ------------------------------------------------
// Several stations: chosen set, comparison with baselines
// ------------------------------------------------

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
      metrics: data.result,
      highlight: true,
    },
    ...data.baselines.map((baseline) => ({
      key: baseline.name,
      label: baseline.label,
      metrics: baseline,
      highlight: false,
    })),
  ]

  return (
    <>
      <div style={{ margin: "8px 0" }}>
        <strong>
          Recommended: {data.count} station{data.count === 1 ? "" : "s"}
        </strong>{" "}
        (purple markers on the map, numbered in the order they add value)
        <ol style={{ margin: "4px 0" }}>
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
              <th style={cell}>Comparison</th>
              <th style={cell}>Average</th>
              <th style={cell}>Worst case</th>
              <th style={cell}>Within {target}</th>
              <th style={cell}>Unreached</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.key}
                style={
                  row.highlight
                    ? {
                        fontWeight: "bold",
                        background: "rgba(124, 58, 237, 0.15)",
                      }
                    : undefined
                }
              >
                <td style={cell}>{row.label}</td>
                <td style={cell}>{minutes(row.metrics.average_seconds)}</td>
                <td style={cell}>{minutes(row.metrics.worst_seconds)}</td>
                <td style={cell}>
                  {percent(row.metrics.within_target_percent)}
                </td>
                <td style={cell}>{count(row.metrics.unreachable)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ fontSize: "14px", marginTop: "6px" }}>
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
    </>
  )
}
