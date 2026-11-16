// Saved runs and studies: list what the database holds, see a run's saved
// history, restart an ended run from its setup, and open or resume a past
// scenario study. Loads nothing until asked (the page makes no requests
// on its own).

import { useState } from "react"

import { Icon, Spinner } from "./components.tsx"
import {
  canRestart,
  canResume,
  formatWhen,
  restartSummary,
  runStatusLabel,
  type RestartedRun,
  type RunHistory,
  type SavedRun,
  type StudySummary,
} from "./history.ts"
import { formatSeconds, postJson, request } from "./scenarios.ts"

export function HistoryPanel({
  onRestarted,
  onOpenStudy,
}: {
  // A run was restarted: show it on the map
  onRestarted: (restarted: RestartedRun) => void
  // Show a study in the scenario analysis section
  onOpenStudy: (studyId: string) => void
}) {
  const [runs, setRuns] = useState<SavedRun[] | null>(null)
  const [studies, setStudies] = useState<StudySummary[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [details, setDetails] = useState<Record<string, RunHistory>>({})
  const [open, setOpen] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [runList, studyList] = await Promise.all([
        request<{ simulations: SavedRun[] }>("/simulations?limit=20"),
        request<{ studies: StudySummary[] }>("/scenarios/studies?limit=20"),
      ])
      setRuns(runList.simulations)
      setStudies(studyList.studies)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load the history")
    } finally {
      setLoading(false)
    }
  }

  const toggleDetails = async (run: SavedRun) => {
    if (open === run.simulation_id) {
      setOpen(null)
      return
    }
    setOpen(run.simulation_id)
    if (details[run.simulation_id]) return
    try {
      const history = await request<RunHistory>(`/simulations/${run.simulation_id}/history`)
      setDetails((current) => ({ ...current, [run.simulation_id]: history }))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load that run")
    }
  }

  const restart = async (run: SavedRun) => {
    setBusy(run.simulation_id)
    setError(null)
    try {
      const restarted = await postJson<RestartedRun>(
        `/simulations/${run.simulation_id}/restart`, {}
      )
      onRestarted(restarted)
      setNotice(restartSummary(restarted))
      await load()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not restart that run")
    } finally {
      setBusy(null)
    }
  }

  const resume = async (study: StudySummary) => {
    setBusy(study.study_id)
    setError(null)
    try {
      await postJson(`/scenarios/studies/${study.study_id}/resume`, {})
      onOpenStudy(study.study_id)
      setNotice(`Resumed ${study.name}: its unfinished scenarios are running again.`)
      await load()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not resume that study")
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="history" id="history" aria-labelledby="history-title">
      <div className="scenarios-head history-head">
        <div>
          <h2 id="history-title">Saved runs and studies</h2>
          <p>
            Everything saved in the database: look back at past runs, restart one from
            its setup, or open and resume a scenario study.
          </p>
        </div>
        <button type="button" className="btn" onClick={load} disabled={loading} aria-busy={loading}>
          {loading ? <Spinner /> : <Icon name="clock" size={16} />}
          {runs === null ? "Load saved runs and studies" : "Refresh"}
        </button>
      </div>

      {error && (
        <div className="notice notice--error" role="alert">
          <Icon name="alert" size={16} />
          <span>{error}</span>
        </div>
      )}
      {notice && (
        <div className="notice notice--info" role="status">
          <Icon name="info" size={16} />
          <span>{notice}</span>
        </div>
      )}

      {runs !== null && studies !== null && (
        <div className="history-body">
          <div className="card results-card" role="region" aria-label="Saved runs">
            <h3 className="results-title">Simulation runs</h3>
            {runs.length === 0 ? (
              <p className="method-note">No runs saved yet.</p>
            ) : (
              <div className="table-wrap">
                <table className="data-table" aria-label="Saved simulation runs">
                  <thead>
                    <tr>
                      <th>Started</th>
                      <th>Status</th>
                      <th>Simulated</th>
                      <th>Vehicles</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map((run) => {
                      const history = details[run.simulation_id]
                      return [
                        <tr key={run.simulation_id}>
                          <td>{formatWhen(run.created_at)}</td>
                          <td>
                            <span className={run.live ? "badge badge--ok" : "badge"}>
                              {runStatusLabel(run)}
                            </span>
                          </td>
                          <td>{formatSeconds(run.simulation_time)}</td>
                          <td>{run.vehicles}</td>
                          <td className="row-actions">
                            <button
                              type="button"
                              className="btn btn--quiet"
                              aria-expanded={open === run.simulation_id}
                              onClick={() => toggleDetails(run)}
                            >
                              Details
                            </button>
                            {canRestart(run) && (
                              <button
                                type="button"
                                className="btn btn--quiet"
                                onClick={() => restart(run)}
                                disabled={busy !== null}
                                aria-busy={busy === run.simulation_id}
                              >
                                Restart
                              </button>
                            )}
                          </td>
                        </tr>,
                        open === run.simulation_id && (
                          <tr key={`${run.simulation_id}-details`} className="detail-row">
                            <td colSpan={5}>
                              {history ? (
                                <ul className="history-facts" aria-label="Saved history">
                                  <li>Route from junction {run.start} to {run.destination}</li>
                                  <li>{history.counts.closures} closure(s), {history.counts.accidents} accident(s)</li>
                                  <li>
                                    {history.counts.emergencies_arrived} of {history.counts.emergencies} emergencies reached
                                    {history.mean_response_seconds !== null &&
                                      `, mean response ${formatSeconds(history.mean_response_seconds)}`}
                                  </li>
                                  <li>{history.counts.stations} station(s), {history.counts.fleets} fleet(s), {history.counts.events} events</li>
                                  {history.restarted_from && <li>Restarted from an earlier run</li>}
                                </ul>
                              ) : (
                                <Spinner />
                              )}
                            </td>
                          </tr>
                        ),
                      ]
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <p className="method-note">
              A run cannot carry on after the server restarts (its vehicles were only in
              memory). Restart starts a new run with the same setup: route, stations,
              the closures and accidents still in place, and fleets with the same trips.
            </p>
          </div>

          <div className="card results-card" role="region" aria-label="Saved studies">
            <h3 className="results-title">Scenario studies</h3>
            {studies.length === 0 ? (
              <p className="method-note">No studies saved yet.</p>
            ) : (
              <div className="table-wrap">
                <table className="data-table" aria-label="Saved scenario studies">
                  <thead>
                    <tr>
                      <th>Created</th>
                      <th>Study</th>
                      <th>Status</th>
                      <th>Done</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {studies.map((study) => (
                      <tr key={study.study_id}>
                        <td>{formatWhen(study.created_at)}</td>
                        <td>{study.name}</td>
                        <td>
                          <span className={study.status === "completed" ? "badge badge--ok" : canResume(study) ? "badge badge--warn" : "badge"}>
                            {study.status}
                          </span>
                        </td>
                        <td>{study.completed + study.failed} of {study.scenario_count}</td>
                        <td className="row-actions">
                          <button
                            type="button"
                            className="btn btn--quiet"
                            aria-label={`Open ${study.name}`}
                            onClick={() => onOpenStudy(study.study_id)}
                          >
                            Open
                          </button>
                          {canResume(study) && (
                            <button
                              type="button"
                              className="btn btn--quiet"
                              aria-label={`Resume ${study.name}`}
                              onClick={() => resume(study)}
                              disabled={busy !== null}
                              aria-busy={busy === study.study_id}
                            >
                              Resume
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="method-note">
              Studies a server restart interrupts are resumed automatically when it starts
              again; Resume does the same by hand, for an interrupted or cancelled study.
              Completed scenarios keep their results.
            </p>
          </div>
        </div>
      )}
    </section>
  )
}
