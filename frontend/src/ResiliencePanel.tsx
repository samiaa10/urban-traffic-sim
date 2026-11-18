// How reachable Norwich is right now, against the same roads undisrupted:
// junctions still connected, extra drive time, the worst-hit areas, and how
// many places the run's stations reach within their target. Measured by
// the backend on request (GET /resilience/accessibility), from the live
// run's actual closures, accidents and floods.

import { useState } from "react"

import { formatSeconds } from "./scenarios.ts"
import type { ResilienceReport } from "./live.ts"

const percent = (value: number | null) => (value === null ? "-" : `${value.toFixed(1)}%`)
const seconds = (value: number | null) => (value === null ? "-" : formatSeconds(value))

export function ResiliencePanel({ measure }: { measure: () => Promise<ResilienceReport> }) {
  const [report, setReport] = useState<ResilienceReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  const run = async () => {
    setBusy(true)
    setError("")
    try {
      setReport(await measure())
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not measure accessibility")
    } finally {
      setBusy(false)
    }
  }

  const before = report?.accessibility.before
  const now = report?.accessibility.now
  const services = report ? Object.keys(report.emergency_access.now) : []

  return (
    <section className="card resilience" id="resilience" aria-labelledby="resilience-title">
      <h2 id="resilience-title" className="results-title">Network resilience</h2>
      <p className="hint">
        How reachable the city is now, at the speed limits around closed and slowed roads, against the
        same roads with nothing disrupted: from every junction to {report?.destinations_sampled ?? 60} sampled
        destinations.
      </p>
      <button type="button" className="btn" onClick={() => void run()} disabled={busy}>
        {busy ? "Measuring…" : "Measure accessibility now"}
      </button>
      {error && <p className="notice notice--error" role="status">{error}</p>}
      {report && before && now && (
        <>
          <p className="hint" role="status">
            {report.closed_roads} roads closed, {report.affected_roads} closed or slowed.
          </p>
          <table className="data-table" aria-label="Accessibility before and now">
            <thead>
              <tr><th scope="col">Measure</th><th scope="col">Undisrupted</th><th scope="col">Now</th></tr>
            </thead>
            <tbody>
              <tr><th scope="row">Junction pairs connected</th><td>{percent(before.reachable_percent)}</td><td>{percent(now.reachable_percent)}</td></tr>
              <tr><th scope="row">Mean drive time</th><td>{seconds(before.mean_seconds)}</td><td>{seconds(now.mean_seconds)}</td></tr>
              <tr><th scope="row">Mean extra drive time</th><td>-</td><td>{seconds(now.mean_increase_seconds)}</td></tr>
              <tr><th scope="row">Worst extra drive time</th><td>-</td><td>{seconds(now.worst_increase_seconds)}</td></tr>
              {services.map((service) => (
                <tr key={service}>
                  <th scope="row">{service[0].toUpperCase() + service.slice(1)} reach within target</th>
                  <td>{percent(report.emergency_access.before[service]?.within_target_percent ?? null)}</td>
                  <td>{percent(report.emergency_access.now[service]?.within_target_percent ?? null)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {now.areas.length > 0 && (
            <>
              <h3 className="resilience-subtitle">Worst-hit areas (about 500 m squares)</h3>
              <ol className="resilience-areas">
                {now.areas.map((area) => (
                  <li key={area.bounds.join(",")}>
                    Around {((area.bounds[0] + area.bounds[2]) / 2).toFixed(4)},{" "}
                    {((area.bounds[1] + area.bounds[3]) / 2).toFixed(4)}:{" "}
                    {area.cut_off_pairs > 0 ? `${area.cut_off_pairs.toLocaleString("en-GB")} pairs cut off, ` : ""}
                    {seconds(area.mean_increase_seconds)} extra on average
                  </li>
                ))}
              </ol>
            </>
          )}
        </>
      )}
    </section>
  )
}
