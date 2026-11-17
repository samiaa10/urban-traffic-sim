// The station optimiser's best answers tested in full traffic simulations
// (POST /optimiser/evaluations): the optimiser picks its top sites (or a
// station set), the backend simulates each at several demand levels and
// seeds, and this shows the ranking in traffic, the gap to the optimiser's
// estimate, confidence intervals and the network impact. Asks nothing
// until the button is pressed.

import { useEffect, useRef, useState } from "react"

import { Icon, Spinner } from "./components.tsx"
import { postJson, request } from "./scenarios.ts"
import {
  EVALUATION_POLL_MS,
  SERVICES,
  evaluationProgress,
  evaluationRequest,
  minutesWithInterval,
  type Distribution,
  type Evaluation,
  type ServiceName,
} from "./services.ts"

const minutes = (seconds: number | null) =>
  seconds === null ? "-" : `${(seconds / 60).toFixed(1)} min`

export function EvaluationSection() {
  const [kind, setKind] = useState<"ranking" | "station_set">("ranking")
  const [stations, setStations] = useState(3)
  const [service, setService] = useState<ServiceName>("ambulance")
  const [distribution, setDistribution] = useState<Distribution>("uniform")
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  // Checking an evaluation that has not finished yet
  const [running, setRunning] = useState(false)
  const poll = useRef<number | null>(null)

  const stop = () => {
    if (poll.current !== null) {
      window.clearInterval(poll.current)
      poll.current = null
    }
    setRunning(false)
  }

  useEffect(() => () => stop(), [])

  const follow = (id: string) => {
    stop()
    const check = async () => {
      try {
        const latest = await request<Evaluation>(`/optimiser/evaluations/${id}`)
        setEvaluation(latest)
        if (latest.report.complete || ["cancelled", "interrupted"].includes(latest.status)) stop()
      } catch (caught) {
        stop()
        setError(caught instanceof Error ? caught.message : "Lost contact with the evaluation")
      }
    }
    poll.current = window.setInterval(check, EVALUATION_POLL_MS)
    setRunning(true)
    void check()
  }

  const start = async () => {
    setStarting(true)
    setError(null)
    setEvaluation(null)
    try {
      const created = await postJson<{ evaluation_id: string }>(
        "/optimiser/evaluations",
        evaluationRequest({ kind, stations, service, distribution })
      )
      follow(created.evaluation_id)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not start the evaluation")
    } finally {
      setStarting(false)
    }
  }

  const report = evaluation?.report

  return (
    <section className="evaluation" id="evaluation" aria-labelledby="evaluation-title">
      <div className="scenarios-head">
        <div>
          <h2 id="evaluation-title">Test the optimiser in traffic</h2>
          <p>
            The optimiser estimates response times without traffic. This runs its best
            answers through full simulations at two traffic levels and three seeds, with the
            same trips and emergencies for each, and ranks them by what really happened.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="fields">
          <label className="field">
            Test
            <select aria-label="What to test" value={kind} disabled={running || starting}
              onChange={(event) => setKind(event.target.value as "ranking" | "station_set")}>
              <option value="ranking">Top 5 single sites</option>
              <option value="station_set">A set of stations</option>
            </select>
          </label>
          {kind === "station_set" && (
            <label className="field">
              Stations
              <select aria-label="Stations in the set" value={stations} disabled={running || starting}
                onChange={(event) => setStations(Number(event.target.value))}>
                {[2, 3, 4, 5].map((value) => <option key={value} value={value}>{value}</option>)}
              </select>
            </label>
          )}
          <label className="field">
            Service
            <select aria-label="Service to test" value={service} disabled={running || starting}
              onChange={(event) => setService(event.target.value as ServiceName)}>
              {SERVICES.map((item) => <option key={item.name} value={item.name}>{item.label}</option>)}
            </select>
          </label>
          <label className="field">
            Emergencies happen
            <select aria-label="Where emergencies happen" value={distribution} disabled={running || starting}
              onChange={(event) => setDistribution(event.target.value as Distribution)}>
              <option value="uniform">Anywhere (every junction alike)</option>
              <option value="population">Where people live (Census 2021)</option>
            </select>
          </label>
        </div>

        <button type="button" className="btn btn--primary" onClick={start}
          disabled={running || starting} aria-busy={starting || running}>
          {starting || running ? <Spinner /> : <Icon name="chart" size={16} />}
          Test in traffic
        </button>

        {error && (
          <div className="notice notice--error" role="alert">
            <Icon name="alert" size={16} />
            <span>{error}</span>
          </div>
        )}

        {evaluation && !report?.complete && (
          <p className="hint" role="status">{evaluationProgress(evaluation)}</p>
        )}
      </div>

      {report?.complete && (
        <div className="card results-card" role="region" aria-label="Optimiser tested in traffic">
          <div className="results-summary" aria-label="What the simulations showed">
            <strong>What the simulations showed</strong>
            <ul>
              {report.explanation.map((line) => <li key={line}>{line}</li>)}
            </ul>
          </div>
          <div className="table-wrap">
            <table className="data-table" aria-label="Candidates ranked in traffic">
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Candidate</th>
                  <th>Estimate (same emergencies)</th>
                  <th>Simulated</th>
                  <th>Gap</th>
                  <th>Optimiser's rank</th>
                  {report.candidates[0].by_demand.map((d) => (
                    <th key={d.vehicles}>At {d.vehicles} vehicles</th>
                  ))}
                  <th>Delay added (busiest)</th>
                </tr>
              </thead>
              <tbody>
                {report.candidates.map((row) => {
                  const busiest = row.by_demand[row.by_demand.length - 1]
                  return (
                    <tr key={row.label}>
                      <td>{row.simulated_rank}</td>
                      <td>{row.label}</td>
                      <td>{minutes(row.paired_estimate_seconds ?? row.estimate.mean_response_seconds)}</td>
                      <td>{minutes(row.simulated_mean_response_seconds)}</td>
                      <td>{row.estimate_gap_percent === null ? "-" : `${row.estimate_gap_percent > 0 ? "+" : ""}${row.estimate_gap_percent.toFixed(0)}%`}</td>
                      <td>{row.estimated_rank}</td>
                      {row.by_demand.map((d) => (
                        <td key={d.vehicles}>{minutesWithInterval(d.mean_response_seconds)}</td>
                      ))}
                      <td>
                        {busiest.added_delay_vehicle_hours.mean === null
                          ? "-"
                          : `${busiest.added_delay_vehicle_hours.mean.toFixed(2)} veh-h`}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <p className="method-note">
            Simulated: mean response over all runs. Per traffic level: mean ± 95% interval over
            the seeds. Estimate: the optimiser's free-flow time for the same emergencies. Delay
            added: extra vehicle-hours of delay for other traffic compared with the same run
            without emergencies.
            {report.rank_agreement_kendall_tau !== null &&
              ` Agreement with the optimiser's order: Kendall's tau ${report.rank_agreement_kendall_tau.toFixed(2)}.`}
          </p>
        </div>
      )}
    </section>
  )
}
